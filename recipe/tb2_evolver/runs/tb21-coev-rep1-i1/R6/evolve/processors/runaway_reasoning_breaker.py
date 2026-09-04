# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RunawayReasoningBreaker — stop a length-truncated no-tool-call reasoning
spiral and redirect the agent to act.

Motivation (harness deficiency, not a model-knowledge gap)
----------------------------------------------------------
On Terminal-Bench 2 the only tool is ``Bash``; a task is solved only by
*executing commands* (writing files, running scripts, starting services). A
dominant, previously-unhandled terminal failure signature on the R5
trajectories is a **runaway reasoning spiral**: the model emits a single
enormous prose reasoning turn that contains **no tool call** and is cut off by
the output-token limit (``finish_reason == "length"``). It then does the same
thing again, and again, until the context window is exhausted — never once
calling Bash.

Concrete evidence (R5, ``tb21-coev-rep1-i1-r5-traj``):

  * regex-log: FIVE consecutive assistant turns of 66k / 321k / 223k / 100k
    chars, **zero Bash calls in the entire run**; each turn generated exactly
    65,536 output tokens (the max-output cap) and was truncated. Cumulative
    input climbed 65,536 -> 131,072 -> 196,608 -> 262,144 until the model
    produced 0 output tokens at the context ceiling. Task died with the
    required ``/app/regex.txt`` never created.
  * Same "one or more >50k-char no-tool-call reasoning turns" shape recurs on
    large-scale-text-editing (309k total prose chars), protein-assembly (312k),
    db-wal-recovery (162k), llm-inference-batching-scheduler (153k),
    vulnerable-secret (152k), sam-cell-seg (123k).

Why the existing mechanisms miss it
------------------------------------
* The R2 ``ActLoopKeepalive`` fires only on ``finish_reason in
  ("end_turn","stop")`` + no tool calls. A length-truncated turn has
  ``finish_reason == "length"``, so the keepalive never triggers.
* The R5 ``SemanticLoopBreaker`` needs the *same* content prefix to recur
  ``repeat_threshold`` (=4) times. A run that dies in ~5 turns whose prefixes
  vary ("Let me break down the requirements" vs "The user is asking me to
  continue…") never reaches the threshold, so it stays silent too.
* The run loop itself makes this WORSE: on a length-truncated no-tool-call turn
  (harnessx/core/runloop.py ~L726-738) it injects the user message
  *"Your previous response was cut off by the token limit. Please continue from
  where you left off."* — which literally instructs the model to keep
  generating the same runaway prose. That is the feedback loop this processor
  breaks.

This is a *class* deficiency (a missing mechanical redirect on a
length-truncated no-tool-call turn), not a domain-knowledge gap: the model is
demonstrably capable of running Bash (it does so 20-120 times on other tasks);
it just spirals into unbounded reasoning on these and the harness feeds the
spiral rather than interrupting it.

Mechanism
---------
``on_after_model`` (``_order=93`` — after the R5 SemanticLoopBreaker at 92, so
it observes the final response for the step): detect a *runaway* turn —
``finish_reason == "length"`` (or a very large content turn) AND no tool calls
AND non-empty content. When detected and the per-run intervention budget
remains, arm a pending redirect for the next model call.

``on_before_model`` delivers the redirect **without inserting a message**: the
run loop has already appended its own "continue from where you left off" user
message on exactly these turns, so the last role is ``user``. Per the hook
contract, when the last role is ``user`` a processor may *modify the content of
that last user message* (net +0 insertion). We overwrite the counterproductive
stock continuation text with a forceful "stop reasoning, emit ONE short Bash
command now" redirect. If — defensively — the last role is not ``user`` (no
stock nudge present), we append exactly one ``user`` message instead (+1,
contract-safe because the prior turn is an assistant turn).

Contract notes
--------------
- ``on_after_model`` only reads the response and arms internal state; it never
  mutates message history from that hook and never touches ``tool_calls`` —
  the run loop's own length-truncation handling still runs this step.
- ``on_before_model`` performs a net **+0** change in the common case (rewrites
  the last user message's content) or a contract-safe **+1** append when no
  stock user nudge is present. Never removes messages, never edits history
  other than the tail user message, never mutates the system prompt.
- Bounded by ``max_interventions`` per run so a genuinely-progressing agent
  (e.g. configure-git-webserver, which emits a big turn then recovers to Bash
  on its own) is not spammed — and for that agent the redirect merely agrees
  with what it already does (stop reasoning, run a command), so it is harmless.
- No task-specific literals: trigger is a generic ``finish_reason`` / size
  check and the redirect text is generic terminal-agent guidance applicable to
  any task.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    ModelResponseEvent,
    TaskStartEvent,
    TaskEndEvent,
    BeforeModelEvent,
    Message,
)
from harnessx.core.processor import MultiHookProcessor

# Substrings that identify the run loop's own length-truncation continuation
# nudge (harnessx/core/runloop.py). Matching lets us *replace* that message
# rather than stack a second one, keeping the insertion net at +0.
_STOCK_CONTINUE_MARKERS = (
    "cut off by the token limit",
    "continue from where you left off",
)

_REDIRECT_MSG = (
    "STOP. Your previous response was cut off because it was an extremely long "
    "block of reasoning with NO command. On this environment, reasoning in "
    "prose changes nothing — the task is only solved by running commands "
    "through the Bash tool. Do NOT continue that reasoning.\n\n"
    "Right now, in this turn, do exactly this:\n"
    "  1. Emit ONE short Bash command that makes concrete progress (inspect a "
    "file with `ls`/`cat`, write your current best result to the required "
    "output path, or run your script). Keep any explanation to a single short "
    "line.\n"
    "  2. Then wait for the command's output before reasoning further.\n\n"
    "Never write more than a few sentences before your next Bash command. If "
    "you already know the answer, write it to the required output file now with "
    "a single `cat > /path <<'EOF' ... EOF` or `echo` command."
)


class RunawayReasoningBreaker(MultiHookProcessor):
    """Interrupt a length-truncated no-tool-call reasoning spiral and force the
    agent to emit a concrete Bash command instead of more prose.

    Args:
        max_interventions: Per-run cap on how many redirects are injected. Kept
            small so a genuinely-progressing agent that emits one big turn and
            then recovers on its own is not spammed.
        min_content_chars: Only treat a no-tool-call turn as runaway when its
            content is at least this long. Short truncations are left to the
            run loop's own handling.
        trigger_on_large_content: When True, also arm the redirect on a very
            large no-tool-call turn even if ``finish_reason`` is not "length"
            (some providers report "stop"/"end_turn" on a maxed-out turn). Gated
            by ``large_content_chars``.
        large_content_chars: Size threshold (chars) for the large-content
            fallback trigger.
    """

    required_providers: frozenset = frozenset()

    _singleton_group = "tb2_runaway_reasoning_breaker"
    # After the R5 SemanticLoopBreaker (_order=92). This trigger (length
    # truncation) is orthogonal to both the R2 keepalive (end_turn/stop stall)
    # and the R5 loop breaker (repeated prefixes), so ordering only needs to be
    # deterministic and after them.
    _order = 93

    def __init__(
        self,
        max_interventions: int = 4,
        min_content_chars: int = 20000,
        trigger_on_large_content: bool = True,
        large_content_chars: int = 60000,
    ) -> None:
        self.max_interventions = max(0, int(max_interventions))
        self.min_content_chars = max(0, int(min_content_chars))
        self.trigger_on_large_content = bool(trigger_on_large_content)
        self.large_content_chars = max(self.min_content_chars, int(large_content_chars))
        # Per-run state keyed by run_id so parallel workers sharing the
        # singleton track independently.
        self._interventions: dict[str, int] = {}
        self._pending: dict[str, bool] = {}

    # -- lifecycle -------------------------------------------------------

    async def on_task_start(self, event: TaskStartEvent):
        self._interventions.pop(event.run_id, None)
        self._pending.pop(event.run_id, None)
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._interventions.pop(event.run_id, None)
        self._pending.pop(event.run_id, None)
        yield event

    # -- deliver queued redirect ----------------------------------------

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending.get(event.run_id):
            yield event
            return

        msgs = tuple(event.messages)
        if not msgs:
            yield event
            return

        last = msgs[-1]
        if last.role == "user":
            # Common case: the run loop already appended its "continue from where
            # you left off" nudge. Replace THAT message's content (net +0) with
            # our forceful redirect. Guard on the stock markers so we only ever
            # overwrite the harness's own continuation nudge, never a real user
            # turn from the task.
            content = (last.content or "")
            is_stock = any(m in content.lower() for m in _STOCK_CONTINUE_MARKERS)
            if is_stock:
                self._pending.pop(event.run_id, None)
                new_last = dataclasses.replace(last, content=_REDIRECT_MSG)
                yield dataclasses.replace(event, messages=msgs[:-1] + (new_last,))
                return
            # Last user message is a genuine task/user turn — keep pending and
            # do not clobber it; deliver on a later step where the tail is safe.
            yield event
            return

        # Defensive fallback: no stock user nudge present. Append exactly one
        # user message (+1, contract-safe because the prior turn is assistant).
        self._pending.pop(event.run_id, None)
        yield dataclasses.replace(
            event,
            messages=msgs + (Message(role="user", content=_REDIRECT_MSG),),
        )

    # -- detect runaway reasoning turn ----------------------------------

    async def on_after_model(self, event: ModelResponseEvent):
        run_id = event.run_id
        content = event.content or ""
        length = len(content)

        no_tool = not event.tool_calls
        nonempty = bool(content.strip())
        truncated = event.finish_reason == "length"
        large = (
            self.trigger_on_large_content
            and length >= self.large_content_chars
        )

        runaway = (
            no_tool
            and nonempty
            and (truncated or large)
            and length >= self.min_content_chars
        )
        if not runaway:
            yield event
            return

        used = self._interventions.get(run_id, 0)
        if used >= self.max_interventions or self._pending.get(run_id):
            yield event
            return

        self._interventions[run_id] = used + 1
        self._pending[run_id] = True
        yield event
