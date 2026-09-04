# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""OutputStallRecoveryProcessor — break "same result, no new information" stalls.

Closes a systemic Tmax failure mode that is *complementary* to input-side loop
detection: the agent issues tool calls whose **result content is byte-identical**
to the immediately preceding result(s), churning without gaining any new
information, until the step budget is exhausted (``exit_reason=budget_exceeded``).

This is a broader signal than "identical tool call repeated":

* the archetype re-ran the exact same command dozens of times, each returning
  an identical warning and no usable output;
* other tasks *vary* the command slightly (different flags, a tweaked one-liner)
  yet keep getting the **same** error output — an input-fingerprint loop detector
  never fires on those, but the result stream is just as stuck.

Rather than terminate the run, this processor is **warn-only**: when it sees
``warn_threshold`` consecutive identical non-empty tool results, it appends an
escalating corrective note to the tool result so the model reads it on its very
next turn. The note tells the agent that the last action produced no new
information and that repeating it (or minor variants) cannot make progress — it
must change approach, inspect state differently, or finish if the task is
already done. Injecting into the tool result (not the system prompt) keeps the
processor within the hook contract and lets a terminate-based loop guard, if
present, still own the hard stop.

Compaction awareness: the identical-result run counter is reset when a
message-count drop of >= ``compaction_drop_threshold`` is seen in
``on_step_start`` (same heuristic the sibling processors use), so stale
pre-compaction results never contribute to a spurious stall count.
"""

from __future__ import annotations

import dataclasses
import hashlib

from harnessx.core.events import (
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


_WARN = (
    "\n\n[OutputStall] ⚠️  Your last {count} tool calls returned the EXACT same "
    "output — you are not gaining any new information. Repeating this command (or "
    "a minor variant of it) will keep producing the same result. Stop and change "
    "approach: inspect the problem from a different angle, check an assumption you "
    "have not questioned yet, or — if the required outputs already exist — finish."
)

_WARN_HARD = (
    "\n\n[OutputStall] ⛔ STOP REPEATING. The same output has now come back {count} "
    "times in a row. Do NOT issue this command again or paraphrase it. Take a "
    "concretely different action THIS turn: (a) re-read the task and write the "
    "required output file(s) to the exact path named, (b) run a diagnostic that "
    "reveals *why* the previous command is not working, or (c) if the deliverables "
    "already exist and are correct, end. One decisive command only."
)


class OutputStallRecoveryProcessor(MultiHookProcessor):
    """Warn-only recovery for consecutive byte-identical tool results."""

    _singleton_group = "tmax_output_stall_recovery"
    _order = 21  # after LoopDetectionProcessor (20) if that is also present

    def __init__(
        self,
        warn_threshold: int = 3,
        hard_threshold: int = 6,
        min_result_chars: int = 1,
        compaction_drop_threshold: int = 5,
    ) -> None:
        self.warn_threshold = max(2, int(warn_threshold))
        self.hard_threshold = max(self.warn_threshold + 1, int(hard_threshold))
        self.min_result_chars = max(0, int(min_result_chars))
        self.compaction_drop_threshold = max(1, int(compaction_drop_threshold))

        self._last_fp: str = ""
        self._run: int = 0
        self._prev_message_count: int = 0
        self._current_run_id: str = ""

    # ------------------------------------------------------------------
    def _fingerprint(self, result: str) -> str:
        return hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:16]

    def _reset(self) -> None:
        self._last_fp = ""
        self._run = 0
        self._prev_message_count = 0

    # ------------------------------------------------------------------
    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        self._current_run_id = getattr(event, "run_id", "")
        yield event

    async def on_step_start(self, event: StepStartEvent):
        if getattr(event, "run_id", "") != self._current_run_id:
            self._reset()
            self._current_run_id = getattr(event, "run_id", "")

        current = len(event.messages)
        if (
            self._prev_message_count > 0
            and self._prev_message_count - current >= self.compaction_drop_threshold
        ):
            # Compaction happened: history the model relied on was evicted, so a
            # stall counter accumulated against pre-compaction results is stale.
            self._last_fp = ""
            self._run = 0
        self._prev_message_count = current
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        result = event.result or ""
        # Empty / whitespace-only or too-short results are not treated as a
        # stall signal: they carry little content and false-positive easily.
        if len(result.strip()) < self.min_result_chars or not result.strip():
            self._last_fp = ""
            self._run = 0
            yield event
            return

        fp = self._fingerprint(result)
        if fp == self._last_fp:
            self._run += 1
        else:
            self._last_fp = fp
            self._run = 1

        if self._run >= self.hard_threshold:
            yield dataclasses.replace(
                event, result=result + _WARN_HARD.format(count=self._run)
            )
        elif self._run >= self.warn_threshold:
            yield dataclasses.replace(
                event, result=result + _WARN.format(count=self._run)
            )
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
