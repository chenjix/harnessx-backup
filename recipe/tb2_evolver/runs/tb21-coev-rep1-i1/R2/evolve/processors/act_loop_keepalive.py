# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ActLoopKeepalive — keep a stalled agent acting instead of ending the run.

Motivation (harness deficiency, not a model-knowledge gap)
----------------------------------------------------------
On Terminal-Bench 2 the only tool is ``Bash``; a task is solved by *writing
files / starting services via Bash*, never by emitting prose. The run loop,
however, treats any assistant turn with ``finish_reason in ("end_turn",
"stop")`` and **no tool calls** as a natural completion and ``break``s the
episode (harnessx/core/runloop.py ~L745). The stock
``CustomSelfVerifyProcessor`` intercepts this — but only **once per task**
("Fires at most once per task run. On the next no-tool-call turn it stays
silent."). After that single keepalive is spent, the *next* text-only turn
ends the run.

Failure class this addresses:
  A run ends on a large text-only assistant turn that contains no tool call —
  the model rambles / plans in prose instead of executing, so the required
  output file simply never gets created because the agent stopped mid-thought.
  Healthy runs keep assistant turns small and tool-call-bearing; the stall
  signature is a big final assistant message with an empty ``tool_calls`` list.

This is a *class* deficiency: the harness gives up after one nudge, so a model
prone to multi-turn rambling loses the run. The fix is to make the keepalive
**repeatable but bounded** — nudge the agent back to action up to
``max_reprompts`` times, then let the run stop so we never spin forever.

Mechanism
---------
``on_after_model`` (``_order`` just after the stock one-shot self-verify, so on
the turn *that* processor handles this stays silent because the response now
carries its keepalive tool call):

  * Detect a *stall*: ``finish_reason in ("end_turn","stop")`` AND no tool calls
    AND non-empty content (an empty first-turn response is handled by the run
    loop's own retry; a genuinely empty stop is left alone).
  * If the content contains an explicit completion sentinel
    (``completion_marker``, default ``"SUCCESS"``), the agent is deliberately
    finishing — let it stop (yield unchanged).
  * Otherwise, if reprompt budget remains, inject a synthetic keepalive tool
    call so the run loop does not treat the turn as ``done``, and queue a short
    user nudge (delivered on the next ``on_before_model``) telling the agent to
    continue with a concrete ``Bash`` action or to emit the completion sentinel
    to finish. The keepalive tool call is intercepted in ``on_before_tool`` and
    never actually runs (``approved=False`` + ``synthetic_result``), mirroring
    the stock self-verify keepalive contract exactly.
  * When the reprompt budget is exhausted, yield unchanged → the run loop stops
    naturally. Bounded, so no infinite loop.

Contract notes
--------------
- ``on_after_model`` only rewrites ``tool_calls`` on the returned
  ``ModelResponseEvent`` (same shape the stock processor uses); it never edits
  message history from this hook.
- ``on_before_model`` appends **exactly one** ``user`` message and only when a
  nudge is pending, keeping the +1 insertion contract. The prior turn ended
  with a (synthetic) tool result, so appending a user message is legal.
- No task-specific literals: the nudge text is generic terminal-agent guidance
  and fires on any task that stalls.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import (
    ModelResponseEvent,
    ToolCallEvent,
    TaskStartEvent,
    TaskEndEvent,
    BeforeModelEvent,
    Message,
    ToolCall,
)
from harnessx.core.processor import MultiHookProcessor

_KEEPALIVE_TOOL = "_tb2_act_keepalive"
_KEEPALIVE_ACK = "Continuation acknowledged. See the message below and keep working."

_NUDGE_MSG = (
    "You stopped without running a command, but the task is not finished — on "
    "this environment work only counts when it is performed through the Bash "
    "tool (writing files, running scripts, starting services). Planning or "
    "explaining in prose does not change the filesystem.\n\n"
    "Do ONE of the following now:\n"
    "  1. If there is still work to do, take the next concrete step by calling "
    "the Bash tool with an actual command (create/inspect the required output "
    "file, run your script, verify a service is up, etc.).\n"
    "  2. If — and only if — you have already verified with `ls` and `cat` that "
    "every required output file exists at its exact path and its contents are "
    "correct, end your message with the line `SUCCESS: task complete.` to "
    "finish.\n\n"
    "Do not repeat this reasoning back to me; act."
)


class ActLoopKeepalive(MultiHookProcessor):
    """Repeatable, bounded keepalive that stops a rambling agent from ending
    the run prematurely on a text-only turn.

    Args:
        max_reprompts: Maximum number of continuation nudges injected per task.
            Each fires on a distinct no-tool-call stall turn. After the budget
            is spent the run loop is allowed to stop normally. Kept modest so a
            model that truly has nothing to do still terminates.
        completion_marker: Case-insensitive substring that, when present in the
            stalling turn's content, is treated as a deliberate completion — the
            agent is allowed to stop instead of being nudged again.
        min_content_chars: Only treat a no-tool-call turn as a rambling stall
            worth reprompting when its content is at least this long. Very short
            end_turn responses are left to the stock one-shot self-verify / the
            run loop's own handling.
    """

    required_providers: frozenset = frozenset()

    _singleton_group = "tb2_act_loop_keepalive"
    # Just after the stock CustomSelfVerifyProcessor (_order=90): on the turn
    # that one-shot handles, the response already carries its keepalive tool
    # call, so this processor sees tool_calls != () and stays silent.
    _order = 91

    def __init__(
        self,
        max_reprompts: int = 6,
        completion_marker: str = "SUCCESS",
        min_content_chars: int = 1,
    ) -> None:
        self.max_reprompts = max(0, int(max_reprompts))
        self.completion_marker = str(completion_marker).strip().lower()
        self.min_content_chars = max(0, int(min_content_chars))
        # Keyed by run_id so parallel workers sharing the singleton each track
        # their own state independently.
        self._counts: dict[str, int] = {}
        self._pending: dict[str, str] = {}

    # -- lifecycle -------------------------------------------------------

    async def on_task_start(self, event: TaskStartEvent):
        self._counts.pop(event.run_id, None)
        self._pending.pop(event.run_id, None)
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._counts.pop(event.run_id, None)
        self._pending.pop(event.run_id, None)
        yield event

    # -- inject queued nudge --------------------------------------------

    async def on_before_model(self, event: BeforeModelEvent):
        msg = self._pending.pop(event.run_id, "")
        if not msg:
            yield event
            return
        # Prior turn ended with the synthetic keepalive tool result (role tool),
        # so appending exactly one user message satisfies the +1 contract and
        # leaves the window ending on "user".
        yield dataclasses.replace(
            event,
            messages=tuple(event.messages) + (Message(role="user", content=msg),),
        )

    # -- detect stall ----------------------------------------------------

    async def on_after_model(self, event: ModelResponseEvent):
        run_id = event.run_id
        stall = (
            event.finish_reason in ("end_turn", "stop")
            and not event.tool_calls
            and bool((event.content or "").strip())
            and len(event.content or "") >= self.min_content_chars
        )
        if not stall:
            yield event
            return

        # Deliberate completion — let it stop.
        if self.completion_marker and self.completion_marker in (event.content or "").lower():
            yield event
            return

        count = self._counts.get(run_id, 0)
        if count >= self.max_reprompts:
            # Budget exhausted — allow the run loop to stop naturally.
            yield event
            return

        self._counts[run_id] = count + 1
        self._pending[run_id] = _NUDGE_MSG
        keepalive = ToolCall(
            id=f"ak-{uuid.uuid4().hex[:8]}",
            name=_KEEPALIVE_TOOL,
            input={},
        )
        yield dataclasses.replace(event, tool_calls=(keepalive,))

    # -- swallow the synthetic keepalive tool call ----------------------

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _KEEPALIVE_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_KEEPALIVE_ACK
            )
        else:
            yield event
