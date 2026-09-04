# SPDX-License-Identifier: MIT
"""IterativeVerifyGate — a bounded, work-aware completion gate.

Problem class (observable, task-agnostic)
------------------------------------------
The run loop treats an assistant turn with ``finish_reason in {stop,
end_turn}`` and **no tool calls** as "done". A weak model frequently
emits such a turn while *asserting* completion ("task complete", "all
tests passed") without having executed any objective verification of the
deliverable, or while it is stuck repeating text and simply gives up. In
both cases the run exits, the external verifier then finds the end state
wrong, and reward=0.

The stock one-shot self-verify nudge helps once, but:
  * it fires at most once per task, so a second bare "done" claim (or a
    later text-only turn from a stuck loop) exits unchecked; and
  * it cannot distinguish an agent that *actually ran* verification
    commands after the nudge from one that merely re-asserted success.

Mechanism
---------
This processor gates the no-tool-call exit up to ``max_nudges`` times.
It only *re-*nudges when the agent tries to exit **without having
executed a real (non-synthetic) tool call since the previous nudge** —
i.e. it re-asserted completion or looped without doing any new
verification work. If the agent responded to a nudge by actually running
commands and then exits, the gate steps aside immediately. This keeps
the added cost bounded and, crucially, does not penalise the fast
passing cluster that genuinely verifies before exiting (they run
commands after the first checklist, so they are not re-nudged).

The nudge is a general verification checklist keyed off task state only
(required outputs exist, contents are semantically correct, services
still alive, no self-reported "passed" without a real test). It contains
no task-specific literals, paths, or answers.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCall,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

_GATE_TOOL = "_tb2_iter_verify"
_GATE_ACK = "Verification gate: see the instructions in the message above, then continue."

_CHECKLIST = """\
Before finishing, do NOT just assert completion — actually verify the end state now. Run real commands:

1. **Re-read the task requirements** and list, concretely, every artifact / behaviour it demands (exact output paths, formats, thresholds, running services).

2. **Prove each required output exists at its exact path** with `ls -lh`, and **inspect its contents** with `cat`/`head` — confirm the values are semantically correct, not merely that a file is present or a script exited 0.

3. **Re-run the real end-to-end behaviour**, not a trivial smoke test. A command that exited 0 earlier, or a test that only checks syntax/importability, does NOT prove correctness.

4. **For long-running services:** confirm they are still alive and reachable *right now*, and that no stale/duplicate instances are lingering and no required port is left bound by a dead run.

If anything is wrong, fix it and re-verify. Statements like "task complete" or "all tests passed" are not accepted unless the commands above were actually executed and their output confirms success.\
"""

_STUCK_NOTE = (
    "\n\nNote: you appear to be finishing without having run any new verification "
    "command since the last check. Execute the concrete checks above with real "
    "commands before declaring the task done."
)


class IterativeVerifyGate(MultiHookProcessor):
    """Bounded, work-aware no-tool-call exit gate.

    Parameters
    ----------
    max_nudges:
        Maximum number of times the gate will intercept a no-tool-call
        exit for a single task. Bounds the added cost.
    """

    _singleton_group = "tb2_iter_verify"
    _order = 90

    def __init__(self, max_nudges: int = 2) -> None:
        self.max_nudges = int(max_nudges)
        self._nudges_used = 0
        self._ran_tool_since_nudge = False
        self._nudged_at_least_once = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._nudges_used = 0
        self._ran_tool_since_nudge = False
        self._nudged_at_least_once = False
        self._pending_message = ""
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        # Last message is a tool result (the synthetic gate ack) → append
        # exactly +1 user message. Preserves the +1-per-chain contract.
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if not exit_intent:
            yield event
            return

        # First exit attempt is always gated once (mirrors the stock
        # self-verify). Subsequent exits are only re-gated when the agent
        # did NOT run a real tool call since the previous nudge — i.e. it
        # re-asserted completion or looped without doing new verification.
        first_time = not self._nudged_at_least_once
        did_no_work = self._nudged_at_least_once and not self._ran_tool_since_nudge

        if self._nudges_used < self.max_nudges and (first_time or did_no_work):
            self._nudges_used += 1
            self._nudged_at_least_once = True
            self._ran_tool_since_nudge = False
            note = "" if first_time else _STUCK_NOTE
            self._pending_message = _CHECKLIST + note
            keepalive = ToolCall(
                id=f"ivg-{uuid.uuid4().hex[:8]}",
                name=_GATE_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _GATE_TOOL:
            # Synthetic keepalive — intercept, do not execute, and do NOT
            # count as verification work.
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_GATE_ACK
            )
        else:
            # A real tool call — the agent is doing verification work.
            if self._nudged_at_least_once:
                self._ran_tool_since_nudge = True
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._nudges_used = 0
        self._ran_tool_since_nudge = False
        self._nudged_at_least_once = False
        self._pending_message = ""
        yield event
