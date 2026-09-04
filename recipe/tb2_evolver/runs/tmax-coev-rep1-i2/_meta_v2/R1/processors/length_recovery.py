# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LengthTruncationRecoveryProcessor (v3 — small-max_tokens loop break).

Closes a systemic ``budget_exceeded`` / step-cap failure mode observed on the
small-model TB2 runs: the model keeps emitting the *same* verbose narration
paragraph, which is cut off by a small provider ``max_tokens`` cap (here the
truncated turns are only ~2000 chars — about 512 tokens — with **no tool call**
and ``finish_reason == "length"``). The run loop reacts by appending a passive
"Your previous response was cut off by the token limit. Please continue from
where you left off." user message, which *re-primes the very narration that got
truncated*. The model then regenerates the identical paragraph, is truncated
again, and the cycle repeats for dozens of steps until the task grinds to the
step cap with reward 0.

Trajectory evidence (r0 round):
  * task_000740 — 11 passive "cut off" nudges, budget_exceeded @ 80 steps.
  * task_001032 — 17 passive "cut off" nudges, 51 steps, done-but-wrong.
  * task_000396 — 10 passive nudges, budget_exceeded @ 80 steps.
  * task_000028 / task_001653 / task_000015 / task_000958 — 3-5 nudges each,
    all failing, several budget_exceeded.
  Every one of these runaway turns was ~2000-3100 chars — *far* below the v2
  ``content_char_threshold`` of 40000, so v2 was a silent no-op on this model.

Why v2 failed on this model
---------------------------
1. ``content_char_threshold`` (40000) was calibrated for a *different* backend
   that emitted 160K-char blobs. This model's max_tokens truncates at ~2000
   chars, so the char branch never matched.
2. v2's ``on_before_model`` blindly *appended* a corrective user message. But
   after a length-truncated turn the run loop has already appended its own
   passive user "continue" message, so the last message role is ``user`` — and
   the HarnessX before_model contract forbids adding a message when the last
   role is already ``user`` (only content modification of the last user is
   allowed there). v2's corrective nudge was therefore dropped / contract-
   violating exactly in the situation it was written for, leaving the passive
   "continue" message as the last thing the model saw.

v3 fix
------
* ``on_after_model`` trigger keys on the reliable signal for this model:
  ``finish_reason == "length"`` with no tool call (OR an oversized content blob
  as a secondary safety net). Consecutive-truncation streak is tracked and a
  tiered corrective nudge is armed. Oversized content is still collapsed.
* ``on_before_model`` is now **contract-aware**:
    - If the last message is the passive "continue" user message (or any user
      message), it **rewrites that last user message's content** in place
      (``len_delta == 0``; only the last user's content changes — contract
      legal). This *replaces* the counter-productive "continue from where you
      left off" instruction with an actionable "stop narrating, run ONE
      command" directive that the model actually reacts to.
    - Otherwise (last role is not user, e.g. a tool result) it appends exactly
      one user message (``+1``; contract legal).
  Either way the passive re-priming instruction never survives as the last
  thing the model reads once a truncation streak is active.

The processor is benchmark-agnostic: it keys purely on ``finish_reason`` /
``tool_calls`` / ``content`` length and on the harness's own passive-nudge
text, never on task content.
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
_CONTENT_CHAR_THRESHOLD = 40000

# Substring of the run loop's passive continuation message. If the last user
# message contains this, it is the re-priming nudge we want to overwrite.
_PASSIVE_MARKER = "cut off by the token limit"

_TRUNC_MARKER = (
    "\n\n[response truncated by harness: the model emitted a long response with "
    "no tool call and hit the token limit (a repetition loop); the omitted "
    "middle was discarded to prevent the loop from re-seeding itself]\n\n"
)

# First occurrence: gentle but concrete redirect.
_NUDGE_FIRST = (
    "Your last turn was cut off by the token limit before you ran any command. "
    "Do NOT continue or re-explain the previous narration — that is what caused "
    "the cut-off. In your next turn write at most TWO short sentences of "
    "reasoning, then issue exactly ONE concrete Bash command that makes forward "
    "progress (inspect a file, run/compile a script, or write an output file). "
    "Keep the whole response short so it is not truncated again."
)

# Repeated occurrence: hard reset — the model is stuck regenerating the same text.
_NUDGE_REPEAT = (
    "STOP. Your responses have been cut off by the token limit multiple turns in "
    "a row, which means you keep re-writing the same long analysis instead of "
    "acting. Abandon that line of narration completely and do NOT restate it. "
    "Write NO prose analysis. Respond with a SINGLE short Bash command only, that "
    "either (a) writes the required output file(s) to the exact path named in the "
    "task, or (b) runs one quick command to inspect the current state so you can "
    "pick the next concrete step. One short command, nothing else."
)


class LengthTruncationRecoveryProcessor(MultiHookProcessor):
    """Break the small-max_tokens repetition loop and redirect the model to act.

    Parameters
    ----------
    repeat_threshold:
        Number of *consecutive* truncations at (or above) which the escalated
        nudge is used instead of the gentle one. Default 2.
    head_chars / tail_chars:
        How much of a runaway content blob to keep when collapsing it.
    content_char_threshold:
        Secondary trigger: assistant ``content`` length (chars) at or above
        which a no-tool-call turn is treated as a runaway truncation even when
        ``finish_reason`` is not reported as ``"length"``. The primary trigger
        is ``finish_reason == "length"``, which is reliable on this backend.
    """

    _singleton_group = "tb2_length_recovery"
    # Run early in the after-model chain so downstream processors see collapsed
    # content; run late enough in before-model that the run loop's passive nudge
    # is already present as the last message for us to overwrite.
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
        # Primary trigger: explicit length finish (reliable on this backend).
        # Secondary: an oversized no-tool-call content blob (other backends).
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

        messages = event.messages
        if messages and messages[-1].role == "user":
            # Contract-safe path: rewrite ONLY the last user message's content.
            # This overwrites the run loop's passive "continue from where you
            # left off" re-priming nudge (the actual root cause of the loop)
            # with an actionable directive. len_delta == 0, history untouched.
            last = messages[-1]
            existing = last.content or ""
            # Preserve nothing of the passive nudge; if the last user message is
            # something else, prepend the directive so its content is not lost.
            if _PASSIVE_MARKER in existing:
                new_content = nudge
            else:
                new_content = nudge + "\n\n" + existing
            new_last = dataclasses.replace(last, content=new_content)
            yield dataclasses.replace(
                event, messages=messages[:-1] + (new_last,)
            )
        else:
            # Last role is not user (e.g. a tool result) — append exactly one
            # user message (+1, contract legal).
            yield dataclasses.replace(
                event,
                messages=messages + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._consecutive = 0
        self._pending_nudge = ""
        yield event
