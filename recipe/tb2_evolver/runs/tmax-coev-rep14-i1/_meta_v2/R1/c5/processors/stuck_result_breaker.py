# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""StuckResultBreaker — recover (not terminate) from verbatim tool-result loops.

TB2 trajectories show a recurring failure shape: the model re-issues a
*byte-identical* Bash command that keeps returning the *same* error/output,
turn after turn, with near-identical assistant text ("Let me try ... a
different approach"), making zero progress until the step budget is exhausted
(``exit_reason=budget_exceeded``). The existing pipeline has no recovery hook
for this: ``ParseRetryProcessor`` only handles unparseable model output, and
``CustomEditToolProcessor`` only counts *write* commands, not repeated
identical *results*.

This processor watches the stream of tool results. When the same tool returns
the *same* result content ``warn_threshold`` times in a row, it appends an
escalating corrective note to the tool result (an ``on_after_tool`` mutation,
exactly like ``CustomEditToolProcessor``'s warning append). The note tells the
model, in concrete terms, that repeating the identical command cannot change
the outcome and that it must inspect the raw input with a *different* mechanism
or change strategy. It does NOT raise or terminate — the goal is to break the
fixation early so the reclaimed budget can be spent debugging and verifying the
solution, rather than killing the run.

The nudge is content-agnostic: it names no task, path, tool syntax, or
constant. It fires on the structural signal (identical result repetition)
alone, so it generalizes to any tool/any task that falls into a verbatim loop.
"""

from __future__ import annotations

import dataclasses
import hashlib

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


def _signature(result: str, error: str | None) -> str:
    """Stable hash of a tool result's observable content."""
    payload = (result or "") + "\x00" + (error or "")
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()


_WARN = (
    "\n\n[StuckResultBreaker] This tool has now returned the IDENTICAL output "
    "{count} times in a row. Re-issuing the same command will not change the "
    "result. Stop repeating it. Instead: (a) inspect the raw input directly "
    "with a different mechanism than the one that is failing (e.g. dump the "
    "whole file and read it, or reach for a general-purpose interpreter), and "
    "(b) change your parsing/approach based on what you actually see. Do not "
    "emit the same command again."
)

_WARN_HARD = (
    "\n\n[StuckResultBreaker] CRITICAL: the identical output has now repeated "
    "{count} times — you are in a no-progress loop and burning your step "
    "budget. Abandon this exact command entirely and switch to a "
    "fundamentally different method to obtain what you need. If you have "
    "already recovered the value you were after, move on to the next step of "
    "the task now."
)


class StuckResultBreaker(MultiHookProcessor):
    """Append an escalating corrective note when a tool result repeats verbatim.

    Parameters
    ----------
    warn_threshold:
        Number of consecutive identical results before the first note is
        appended.
    hard_threshold:
        Consecutive-repeat count at/after which the stronger note is used.
    reset_after_nudge:
        When True, the consecutive counter is reset after a note is emitted so
        the escalation restarts if the model ignores the first nudge and keeps
        looping (this makes the note re-fire every ``warn_threshold`` further
        repeats rather than only once).
    """

    _singleton_group = "stuck_result_breaker"
    _order = 35  # after CustomEditToolProcessor (30), before self-verify (90)

    def __init__(
        self,
        warn_threshold: int = 3,
        hard_threshold: int = 6,
        reset_after_nudge: bool = True,
    ) -> None:
        self.warn_threshold = max(2, int(warn_threshold))
        self.hard_threshold = max(self.warn_threshold, int(hard_threshold))
        self.reset_after_nudge = bool(reset_after_nudge)
        self._last_sig: dict[str, str] = {}
        self._run: dict[str, int] = {}
        self._total_repeats: dict[str, int] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._last_sig.clear()
        self._run.clear()
        self._total_repeats.clear()
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        tool = event.tool_name or ""
        sig = _signature(event.result, event.error)

        if self._last_sig.get(tool) == sig:
            run = self._run.get(tool, 1) + 1
        else:
            run = 1
        self._last_sig[tool] = sig
        self._run[tool] = run

        if run < self.warn_threshold:
            yield event
            return

        # Cumulative count of how many identical outputs have been seen in this
        # streak, for accurate messaging even when the counter is reset.
        total = self._total_repeats.get(tool, 0) + 1
        self._total_repeats[tool] = total
        display = max(run, total)

        if run >= self.hard_threshold:
            note = _WARN_HARD.format(count=display)
        else:
            note = _WARN.format(count=display)

        if self.reset_after_nudge:
            # Restart the streak so the nudge re-fires if the loop persists,
            # while keeping the cumulative total growing.
            self._run[tool] = 0
            self._last_sig[tool] = sig  # keep sig so next identical result counts

        yield dataclasses.replace(event, result=(event.result or "") + note)

    async def on_task_end(self, event: TaskEndEvent):
        self._last_sig.clear()
        self._run.clear()
        self._total_repeats.clear()
        yield event
