# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LengthLoopTerminator — hard escape hatch for the length-truncation loop.

Systemic failure mode observed across multiple Tmax tasks: the model keeps
emitting long narration that hits the per-call ``max_tokens`` cap
(``finish_reason == "length"``) *without* issuing a tool call. The run loop
appends a passive "please continue" nudge and re-primes the same runaway
generation. ``LengthTruncationRecoveryProcessor`` already tries to break this
by collapsing the runaway content and swapping the passive nudge for an
escalating corrective instruction — but the model frequently ignores the
nudge, emits a single tool call, then relapses into the same narration. The
result is a task that oscillates between truncated narration and the odd tool
call, accumulating *many* truncations while making no real progress, and
burns 700-1000s of wall-clock before terminating with ``no_tool_calls`` or
``budget_exceeded`` at max steps.

The escalating-nudge recovery uses a *consecutive* counter that resets on any
tool call, so it never sees the full extent of the thrash. This processor
tracks the **cumulative** number of length-truncations for the task. Once the
count exceeds ``max_length_truncations`` the loop is judged unrecoverable and
the processor raises ``LoopDetectedError`` — the run loop catches it, recovers
the best output produced so far, and ends the task with
``exit_reason='loop_detected'`` instead of letting it grind the budget to
zero.

This does not attempt to make the underlying task pass — a task stuck in this
loop has usually hit a genuine reasoning/capability wall. Its purpose is to
stop wasted compute (the omitted middle of each truncated generation is pure
waste) and reclaim wall-clock/budget for the rest of the round.

The cap is intentionally set well above the highest truncation count seen on a
task that *recovered and passed*, so genuinely-recovering tasks keep their
full runway; only the runaway loops are cut.
"""

from __future__ import annotations

from harnessx.core.events import ModelResponseEvent, TaskEndEvent, TaskStartEvent
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runloop import LoopDetectedError


class LengthLoopTerminator(MultiHookProcessor):
    """Terminate a task after too many cumulative length-truncations.

    Complements ``LengthTruncationRecoveryProcessor`` (which does the
    per-relapse content-collapse + corrective nudge). This processor is the
    last-resort escape hatch: it counts *total* truncations for the task and
    aborts once recovery has plainly failed.
    """

    _singleton_group = "tmax_length_loop_terminator"
    # Run after the recovery processor (_order = 5) so the corrective nudge
    # gets its chance first; termination is the fallback.
    _order = 6

    def __init__(self, max_length_truncations: int = 10) -> None:
        self.max_length_truncations = max(1, int(max_length_truncations))
        self._count: int = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._count = 0
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        length_truncated = event.finish_reason == "length" and not event.tool_calls
        if length_truncated:
            self._count += 1
            if self._count > self.max_length_truncations:
                # Recovery has failed repeatedly; stop burning budget. The run
                # loop catches LoopDetectedError, recovers the best output, and
                # ends the task cleanly with exit_reason='loop_detected'.
                raise LoopDetectedError(
                    "LengthLoopTerminator: "
                    f"{self._count} length-truncations without recovery "
                    f"(cap={self.max_length_truncations}); aborting runaway loop."
                )
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._count = 0
        yield event
