# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ContentRepetitionRecoveryProcessor for Tmax / TB2-style agents.

Closes a systemic failure mode distinct from the max-tokens truncation loop
already handled by ``LengthTruncationRecoveryProcessor``:

The model issues (near-)identical assistant *narration* turn after turn —
often with a tool call that also repeats or accomplishes nothing — while
declaring "I keep getting the same error, let me try a different approach"
and then NOT actually changing approach. The turn finishes normally
(``finish_reason`` in stop/end_turn/tool_calls), so the length-recovery
processor never fires; the edit-loop guard only watches file-write commands,
so diagnostic/compile loops slip past it. The task burns its entire step
budget in a degenerate loop and exits ``budget_exceeded`` / ``max_steps``
with the required output file never written.

Observed shape (this failure class, generalised — no task-specific literals):
consecutive assistant turns whose normalized content shares the same short
prefix / same overall signature 4–15 times in a row.

This processor:
* fingerprints each assistant turn's content (whitespace/case-normalized,
  length-bounded prefix hash) at ``on_after_model``;
* counts consecutive turns with a matching fingerprint;
* once the run of look-alike turns reaches ``repeat_threshold``, arms an
  escalating corrective nudge injected at the next ``on_before_model``,
  instructing the model to abandon the repeated line of reasoning and take
  one concrete, *different* action (or, if genuinely stuck, to write the
  best-effort required output to its exact task path before it runs out of
  steps).

It never blocks a tool call and mutates messages by at most +1 user turn
per chain (replacing a trailing user message if the run loop already
appended one), satisfying the HarnessX message contract.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

_WS = re.compile(r"\s+")

_NUDGE_FIRST = (
    "You have now produced several nearly identical turns in a row — you are "
    "restating the same analysis instead of making real progress. Do NOT repeat "
    "that reasoning again. In your next turn, change tactic concretely: run a "
    "command you have not run yet (inspect a different file, dump raw bytes, "
    "print intermediate values, or test a smaller isolated case) so you get NEW "
    "information. One short, different Bash command."
)

_NUDGE_REPEAT = (
    "STOP — you are stuck in a repetition loop and burning your step budget. The "
    "approach you keep retrying is not working and re-explaining it will not help. "
    "Abandon it entirely. Decide right now between two moves and take exactly one: "
    "(a) if you have a partial but plausible result, WRITE the required output "
    "file(s) to the exact path named in the task and then verify it exists with "
    "`ls`; or (b) gather genuinely new evidence with a single command you have not "
    "tried before. Do not restate prior narration. One Bash command only."
)


def _fingerprint(content: str, prefix_chars: int) -> str:
    """Whitespace/case-normalized bounded-prefix hash of assistant content."""
    norm = _WS.sub(" ", (content or "").strip().lower())
    if not norm:
        return ""
    return hashlib.sha1(norm[:prefix_chars].encode("utf-8")).hexdigest()


class ContentRepetitionRecoveryProcessor(MultiHookProcessor):
    """Break a near-identical-narration repetition loop and redirect to act."""

    _singleton_group = "tmax_content_repetition_recovery"
    # Run after length-recovery (_order=5) so a length-truncation loop is
    # handled by its dedicated processor first; this one catches the
    # normal-finish narration loop that length-recovery ignores.
    _order = 6

    def __init__(
        self,
        repeat_threshold: int = 3,
        escalate_threshold: int = 5,
        prefix_chars: int = 120,
        min_content_chars: int = 40,
    ) -> None:
        # Number of consecutive look-alike turns before the first nudge.
        self.repeat_threshold = max(2, int(repeat_threshold))
        # Consecutive look-alike turns before the hard "stop / write output" nudge.
        self.escalate_threshold = max(self.repeat_threshold, int(escalate_threshold))
        self.prefix_chars = max(20, int(prefix_chars))
        # Ignore trivially short turns (e.g. "Done." / "Now let me run it.") so
        # ordinary terse narration does not trip the guard.
        self.min_content_chars = max(0, int(min_content_chars))
        self._last_fp: str = ""
        self._run: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._last_fp = ""
        self._run = 0
        self._pending_nudge = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        content = event.content or ""
        # Only consider substantive narration; blank/very short turns reset
        # nothing and are not counted (they are usually terse action turns).
        norm_len = len(_WS.sub(" ", content.strip()))
        if norm_len < self.min_content_chars:
            yield event
            return

        fp = _fingerprint(content, self.prefix_chars)
        if fp and fp == self._last_fp:
            self._run += 1
        else:
            self._last_fp = fp
            self._run = 1

        if self._run >= self.repeat_threshold:
            self._pending_nudge = (
                _NUDGE_REPEAT
                if self._run >= self.escalate_threshold
                else _NUDGE_FIRST
            )
            # Reset the run counter so we nudge once per detected streak
            # start, then re-arm only if the loop genuinely continues past
            # the threshold again (avoids nudging every single turn).
            self._run = 0
            self._last_fp = ""
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Preserve the +1 message contract: if the run loop already appended a
        # trailing user message, replace it rather than adding a second one.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._last_fp = ""
        self._run = 0
        self._pending_nudge = ""
        yield event
