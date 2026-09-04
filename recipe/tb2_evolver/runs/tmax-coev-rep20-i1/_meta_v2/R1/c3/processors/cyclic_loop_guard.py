# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""CyclicLoopGuard — hard-stop for multi-step repeating tool-call cycles.

Closes a systemic budget-burn failure mode that the existing
``LoopDetectionProcessor`` (exact-consecutive) and the soft text nudges
(``CustomEditToolProcessor``, name-only warnings) all miss:

A weak model gets stuck in a **cycle of period > 1** — it alternates
between two or more distinct tool calls that together make no progress,
e.g.::

    step A:  rm -f X && touch X && ls X      (fingerprint a)
    step B:  ./server > log 2>&1 &            (fingerprint b)
    step C:  sleep 1 && cat log               (fingerprint c)
    step A:  rm -f X && touch X && ls X       (a again)
    ...

Because consecutive fingerprints differ (a, b, c, a, b, c, ...), the
exact-consecutive detector never fires a raise, and the model happily
ignores every appended text warning — it emitted the same
"let me try a different approach" narration verbatim on every cycle
while doing the identical thing. The task ran to ``budget_exceeded``
after 80 steps / 550s, burning wall-clock that other tasks needed.

This processor detects a repeating cycle of any period ``1..max_period``
at the *tail* of a sliding fingerprint window. It warns once (soft,
appended to the tool result) and then raises :exc:`LoopDetectedError`
when the cycle persists — converting an unrecoverable full-budget stall
into a clean, early ``exit_reason=loop_detected`` and freeing the run
budget for the rest of the benchmark.

Scope: general. No task-specific literals; keys off command fingerprints
only. Applies uniformly to every task.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import deque

from harnessx.core.events import (
    ToolCallEvent,
    ToolResultEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runloop import LoopDetectedError


_WARN_TEMPLATE = (
    "\n\n[CyclicLoopGuard] ⚠️  You have repeated the same cycle of {period} "
    "command(s) {reps} times with no change in outcome. This is a loop — the "
    "approach is not working. Do NOT run the same sequence again. Stop, re-read "
    "the task, and take a *fundamentally different* action (inspect why the last "
    "attempt failed, read the relevant source/config, or fix the root cause). If "
    "the task is already done, finish."
)


def _normalize_command(cmd: str) -> str:
    """Collapse whitespace so trivially-reformatted commands hash the same."""
    return " ".join(cmd.split())


class CyclicLoopGuard(MultiHookProcessor):
    """Detect a repeating multi-step tool-call cycle and hard-stop it.

    A "cycle of period *p*" means the last ``p`` fingerprints repeat as a
    block. ``period=1`` degenerates to the exact-consecutive case (kept so
    a single guard covers both shapes). Interleaved distinct steps break
    the tail run, avoiding false positives on legitimate exploration.

    Args:
        max_period:      Largest cycle length to look for (default 4).
        warn_reps:       Number of full cycle repetitions that injects a
                         soft warning into the tool result (default 3).
        raise_reps:      Number of full cycle repetitions that raises
                         :exc:`LoopDetectedError` and ends the task
                         cleanly (default 4).
        window_size:     Sliding fingerprint window (default 24; must be
                         >= max_period * raise_reps to detect the longest
                         cycle at its raise threshold).
    """

    _singleton_group = "cyclic_loop_guard"
    # Run after the name-only LoopDetection band; order is not critical
    # because this processor only reads its own fingerprint window.
    _order = 21

    def __init__(
        self,
        max_period: int = 4,
        warn_reps: int = 3,
        raise_reps: int = 4,
        window_size: int = 24,
    ) -> None:
        self.max_period = max(1, int(max_period))
        self.warn_reps = max(2, int(warn_reps))
        self.raise_reps = max(self.warn_reps + 1, int(raise_reps))
        needed = self.max_period * self.raise_reps
        self.window_size = max(int(window_size), needed)

        self._fps: deque[str] = deque(maxlen=self.window_size)
        self._pending: dict[str, str] = {}
        self._warned_signatures: set[str] = set()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fingerprint(self, event: ToolCallEvent) -> str:
        cmd = ""
        try:
            if isinstance(event.tool_input, dict):
                cmd = str(event.tool_input.get("command", ""))
            else:
                cmd = str(event.tool_input)
        except Exception:
            cmd = repr(event.tool_input)
        payload = f"{event.tool_name}\x00{_normalize_command(cmd)}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _max_cycle_reps(self) -> tuple[int, int]:
        """Return (period, reps) for the longest-repeating tail cycle.

        Scans periods 1..max_period; for each, counts how many times the
        most recent length-``p`` block repeats consecutively at the tail.
        Returns the (period, reps) with the highest reps (ties → smallest
        period, which is the tightest loop).
        """
        n = len(self._fps)
        best_period, best_reps = 0, 1
        for p in range(1, self.max_period + 1):
            if n < p * 2:
                break
            block = list(self._fps)[-p:]
            reps = 1
            # Walk backwards in blocks of size p.
            idx = n - p
            while idx - p >= 0:
                prev = list(self._fps)[idx - p:idx]
                if prev == block:
                    reps += 1
                    idx -= p
                else:
                    break
            if reps > best_reps:
                best_period, best_reps = p, reps
        return best_period, best_reps

    def _reset(self) -> None:
        self._fps.clear()
        self._pending.clear()
        self._warned_signatures.clear()

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        self._pending[event.tool_call_id] = self._fingerprint(event)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        fp = self._pending.pop(event.tool_call_id, None)
        if fp is None:
            yield event
            return

        self._fps.append(fp)
        period, reps = self._max_cycle_reps()

        if period == 0:
            yield event
            return

        if reps >= self.raise_reps:
            raise LoopDetectedError(
                f"Cyclic loop detected: a cycle of {period} tool call(s) "
                f"repeated {reps} times consecutively with no progress"
            )

        if reps >= self.warn_reps:
            # Signature keyed on the actual cycle block so a *new* distinct
            # cycle still gets its own first warning before escalation.
            sig = "|".join(list(self._fps)[-period:])
            if sig not in self._warned_signatures:
                self._warned_signatures.add(sig)
                warning = _WARN_TEMPLATE.format(period=period, reps=reps)
                yield dataclasses.replace(
                    event, result=(event.result or "") + warning
                )
                return

        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
