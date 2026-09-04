# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LengthTruncationRecoveryProcessor (v2 — finish_reason-agnostic trigger).

Closes a systemic failure mode observed on small-model TB2 runs: the model
enters a *degenerate repetition loop* — it keeps emitting the same narration
paragraph over and over inside the assistant ``content`` field with no tool
call, until the response hits the provider ``max_tokens`` limit. The run loop
then appends a passive "Your previous response was cut off by the token limit.
Please continue from where you left off." nudge, which merely re-primes the
same runaway generation. The cycle repeats, burning steps and wall-clock and
eventually crashing the task (``exit_reason=error``) or grinding to the step
cap with reward 0.

The v1 processor gated purely on ``finish_reason == "length"``. Trajectory
evidence from later rounds showed the vLLM/OpenAI-compatible backend used by
this eval records ``finish_reason=None`` even on responses that clearly hit the
output token cap (assistant ``content`` fields of 120K-320K characters with no
tool call). Because the gate never matched, the v1 processor was a silent
no-op on exactly the runaway loops it was written to break — every long-running
failure in the round exhibited 1-44 oversized no-tool-call assistant turns with
``finish_reason=None``, while *every* passing task kept its largest assistant
turn under ~12K characters. That bimodal separation (passing max ~12K vs runaway
min ~160K) is what this v2 keys on.

v2 makes the trigger robust: a turn is treated as a runaway truncation when it
carries **no tool call** and *either* ``finish_reason == "length"`` *or* its
``content`` exceeds a large character threshold (``content_char_threshold``).
The character threshold sits far above any legitimate assistant turn seen in
passing trajectories, so normal long-but-productive turns are never affected.

Two mechanical hooks, unchanged in spirit from v1:

* ``on_after_model`` — when a runaway truncation is detected, the
  (potentially hundreds-of-KB) runaway ``content`` is collapsed to a short
  head+tail excerpt so it does not pollute the next turn's context and re-seed
  the loop. A per-task counter of *consecutive* truncations is maintained, and
  a pending corrective nudge is armed.

* ``on_before_model`` — if a corrective nudge is armed, exactly one ``user``
  message is appended that redirects the model away from open-ended narration
  and toward a single concrete Bash action (escalating on repeats). This
  replaces the passive "continue" behaviour with an actionable instruction that
  breaks the loop.

Any response that ends normally (a tool call, or a non-``length`` finish under
the character threshold) resets the counter, so a one-off long turn is
tolerated without penalty.

The processor is benchmark-agnostic in mechanism: it keys purely on
``finish_reason`` / ``tool_calls`` / ``content`` length, never on task content.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor


_HEAD_CHARS = 1200
_TAIL_CHARS = 600
# A legitimate assistant turn in the passing cluster never exceeded ~12K chars;
# every runaway max_tokens blob was >=160K chars. 40K sits comfortably in the
# empty band between the two, so this trigger cannot fire on a normal turn.
_CONTENT_CHAR_THRESHOLD = 40000
_TRUNC_MARKER = (
    "\n\n[response truncated by harness: the model emitted a very long response "
    "with no tool call (a max_tokens repetition loop); the omitted middle was "
    "discarded to prevent the loop from re-seeding itself]\n\n"
)

# First occurrence: gentle redirect toward a concrete action.
_NUDGE_FIRST = (
    "Your last turn produced a very long response without running any command. "
    "Do NOT re-explain or continue the previous narration. In your next turn write "
    "at most two sentences of reasoning, then issue exactly ONE concrete Bash "
    "command that makes progress (inspect a file, run a script, or write output). "
    "Keep the response short."
)

# Repeated occurrence: the model is stuck in a loop — force a decisive action.
_NUDGE_REPEAT = (
    "STOP. You have now produced runaway-length responses multiple turns in a row, "
    "which means you are repeating yourself instead of acting. Abandon the current "
    "line of narration entirely. Do not write any prose analysis. Respond with a "
    "SINGLE short Bash tool call that either (a) writes the required output file(s) "
    "to the path named in the task, or (b) runs a quick command to check the "
    "current state so you can decide the next concrete step. One command only."
)


class LengthTruncationRecoveryProcessor(MultiHookProcessor):
    """Break the max_tokens repetition loop and redirect the model to act.

    Parameters
    ----------
    repeat_threshold:
        Number of *consecutive* truncations at (or above) which the escalated
        nudge is used instead of the gentle one. Default 2 (i.e. the first
        truncation gets the gentle nudge, the second and beyond get the
        forceful one).
    head_chars / tail_chars:
        How much of the runaway content to keep at the start / end when
        collapsing an over-length response.
    content_char_threshold:
        Assistant ``content`` length (chars) at or above which a no-tool-call
        turn is treated as a runaway truncation even when ``finish_reason`` is
        not reported as ``"length"``. Set well above any legitimate turn length.
    """

    _singleton_group = "tb2_length_recovery"
    # Run early in the after-model chain so downstream processors (e.g. the
    # self-verify keepalive) see the already-collapsed content.
    _order = 5

    def __init__(
        self,
        repeat_threshold: int = 2,
        head_chars: int = _HEAD_CHARS,
        tail_chars: int = _TAIL_CHARS,
        content_char_threshold: int = _CONTENT_CHAR_THRESHOLD,
    ) -> None:
        self.repeat_threshold = max(1, int(repeat_threshold))
        self.head_chars = max(0, int(head_chars))
        self.tail_chars = max(0, int(tail_chars))
        self.content_char_threshold = max(1, int(content_char_threshold))
        self._consecutive: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._consecutive = 0
        self._pending_nudge = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        content = event.content or ""
        no_tool_call = not event.tool_calls
        # Trigger on either the explicit length finish OR an oversized
        # no-tool-call content blob (the backend reports finish_reason=None
        # on truncated responses in this eval, so length alone is unreliable).
        length_truncated = no_tool_call and (
            event.finish_reason == "length"
            or len(content) >= self.content_char_threshold
        )
        if not length_truncated:
            # Any normal / tool-calling turn clears the streak.
            self._consecutive = 0
            self._pending_nudge = ""
            yield event
            return

        self._consecutive += 1
        self._pending_nudge = (
            _NUDGE_REPEAT
            if self._consecutive >= self.repeat_threshold
            else _NUDGE_FIRST
        )

        if len(content) > (self.head_chars + self.tail_chars + len(_TRUNC_MARKER)):
            collapsed = (
                content[: self.head_chars]
                + _TRUNC_MARKER
                + (content[-self.tail_chars :] if self.tail_chars else "")
            )
            yield dataclasses.replace(event, content=collapsed)
        else:
            yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        # Append exactly one user message (contract-safe: +1 insertion).
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=nudge),),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._consecutive = 0
        self._pending_nudge = ""
        yield event
