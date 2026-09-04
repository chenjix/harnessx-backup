# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""AssistantReasoningRepeatBreaker — break verbatim-reasoning thrash loops.

Failure mode this closes
------------------------
The Tmax agent sometimes fixates on a dead-end sub-problem and, turn after
turn, emits the **byte-identical assistant reasoning content** while making
tiny, cosmetically-different tool calls (e.g. re-writing a source file with
slightly different bytes that produce the *same* error). The model literally
re-generates the same paragraph of "I keep making the same mistake, let me
fix this properly" and never escapes. The run burns its entire step budget
and dies with ``exit_reason=error`` having produced none of the required
output files.

Observed on the evolve set:

* ``task_000109_09ddd96b`` (data_science, exit=error @ 42 steps): the
  assistant re-emitted one identical reasoning block **8 consecutive times**
  (content hash repeats 8×, msgs 69/71/73/75/77/79/81/83) while its WAV-parser
  Go source varied slightly each turn, yielding the same "missing data chunk"
  error. The required ``embeddings.json`` / ``anomaly.txt`` were never written.

Why the existing pipeline misses it
-----------------------------------
* ``CyclicLoopBreaker`` fingerprints the (tool_name, tool_input, result)
  *triple*. Because the tool INPUT (the file bytes) changes every turn, no
  period-k triple cycle forms, so it never escalates past intermittent
  warnings.
* ``LengthTruncationRecoveryProcessor`` fires only on
  ``finish_reason == "length"`` with no tool call. Here the model DOES issue a
  tool call each turn, so it never fires.
* The stock exact loop detector counts a period-1 *tool* tail only.

None of them observe the one property that is actually stable across the
loop: the assistant's *reasoning content* is byte-identical.

Design — escalate, then hard-stop
---------------------------------
Keyed purely on the structural property "consecutive assistant responses have
identical normalized content". Carries no task-specific constants, paths,
commands, or answers.

1. ``on_after_model``: fingerprint the normalized assistant ``content``. Track
   a run of consecutive identical fingerprints. Any change (or empty/too-short
   content) resets the run — genuine progress is never flagged.
2. ``on_before_model``: when the run reaches ``warn_threshold`` identical
   responses, inject a single escalating corrective ``user`` message that
   tells the model to abandon the repeated line and either write the required
   output or take a fundamentally different concrete step. Injected once per
   escalation level; replaces a trailing passive ``user`` nudge if present so
   the before-model message contract is not violated.
3. Safety net: when the run reaches ``break_threshold`` identical responses
   (>= warn_threshold + 1), raise ``LoopDetectedError`` so a pathological
   non-recovering agent produces a clean ``loop_detected`` stop *before* the
   step cap — reclaiming budget and avoiding the ``exit_reason=error`` death
   that produces zero output.

False-positive guards:
* Content is normalized (whitespace-collapsed, lower-cased) and must exceed
  ``min_content_chars`` to count — short acknowledgements ("Done.", "OK") and
  empty tool-only turns can never build a run.
* Compaction-aware: a large message-count drop clears the run so stale
  pre-compaction reasoning can't inflate the count.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runloop import LoopDetectedError

_WARN_NUDGE = (
    "STOP. Your last few turns have produced the EXACT SAME reasoning text with "
    "no real progress — you are stuck re-explaining the same idea instead of "
    "solving the task. Do NOT repeat that reasoning again. Abandon the current "
    "line of attack entirely; it is almost certainly a distraction from the real "
    "deliverable. In your next turn, do ONE of: (a) issue a single concrete Bash "
    "command that writes the required output file(s) to the exact path named in "
    "the task, or (b) run one quick command that inspects the current state so "
    "you can pick a fundamentally different next step. Keep it short."
)

_WHITESPACE_RE = re.compile(r"\s+")


class AssistantReasoningRepeatBreaker(MultiHookProcessor):
    """Detect verbatim-repeated assistant reasoning; nudge, then hard-stop.

    Args:
        warn_threshold: Number of consecutive identical assistant responses
            after which a corrective nudge is injected (default 3).
        break_threshold: Number of consecutive identical assistant responses
            after which the run is stopped with ``LoopDetectedError`` as a
            hard safety net (default 5). Clamped to ``> warn_threshold``.
        min_content_chars: Minimum normalized content length for a response to
            be eligible to join a repeat run (default 40). Guards short
            acknowledgements and empty tool-only turns.
        compaction_drop_threshold: Message-count drop that signals compaction
            and clears the run (default 5).
    """

    _singleton_group = "assistant_reasoning_repeat_breaker"
    # After CyclicLoopBreaker (_order 22) so the tool-triple detector gets first
    # crack; this handles the residual assistant-content-only shape it misses.
    _order = 23

    def __init__(
        self,
        warn_threshold: int = 3,
        break_threshold: int = 5,
        min_content_chars: int = 40,
        compaction_drop_threshold: int = 5,
    ) -> None:
        self.warn_threshold = max(2, int(warn_threshold))
        self.break_threshold = max(self.warn_threshold + 1, int(break_threshold))
        self.min_content_chars = max(1, int(min_content_chars))
        self.compaction_drop_threshold = int(compaction_drop_threshold)

        self._last_fp: str = ""
        self._run_len: int = 0
        self._pending_nudge: str = ""
        self._warned_at: int = 0
        self._current_run_id: str = ""
        self._prev_message_count: int = 0

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]

    def _normalize(self, content: str) -> str:
        return _WHITESPACE_RE.sub(" ", (content or "").strip().lower())

    def _reset(self) -> None:
        self._last_fp = ""
        self._run_len = 0
        self._pending_nudge = ""
        self._warned_at = 0
        self._prev_message_count = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        self._current_run_id = event.run_id
        yield event

    async def on_step_start(self, event: StepStartEvent):
        if event.run_id != self._current_run_id:
            self._reset()
            self._current_run_id = event.run_id
        current = len(event.messages)
        if (
            self._prev_message_count > 0
            and self._prev_message_count - current >= self.compaction_drop_threshold
        ):
            # Compaction dropped history; stale reasoning can't count.
            self._last_fp = ""
            self._run_len = 0
            self._warned_at = 0
        self._prev_message_count = current
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        norm = self._normalize(event.content or "")
        if len(norm) < self.min_content_chars:
            # Too short to be a meaningful reasoning block: breaks any run.
            self._last_fp = ""
            self._run_len = 0
            self._warned_at = 0
            yield event
            return

        fp = self._hash(norm)
        if fp == self._last_fp:
            self._run_len += 1
        else:
            self._last_fp = fp
            self._run_len = 1
            self._warned_at = 0

        if self._run_len >= self.break_threshold:
            raise LoopDetectedError(
                f"Assistant repeated byte-identical reasoning content "
                f"{self._run_len} turns in a row without progress; stopping the "
                f"run before it burns the step budget with no output."
            )

        if self._run_len >= self.warn_threshold and self._warned_at != self._run_len:
            self._pending_nudge = _WARN_NUDGE
            self._warned_at = self._run_len

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        if msgs and getattr(msgs[-1], "role", None) == "user":
            # Replace a trailing passive user nudge to avoid a +1 insertion when
            # the last role is already user (before-model contract).
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
