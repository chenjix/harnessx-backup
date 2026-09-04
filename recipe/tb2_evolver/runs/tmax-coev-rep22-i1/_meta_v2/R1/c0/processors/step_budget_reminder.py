# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""StepBudgetReminderProcessor — converge-before-the-step-cap guard.

Closes a systemic thrash failure mode observed across multiple tmax tasks:
the agent spends its entire step budget exploring / debugging the environment
(background processes, proxies, probing commands) and hits the run loop's
``max_steps`` cap (``exit_reason=budget_exceeded``) without ever writing the
deliverable file(s) the task named. The final state then fails verification
because the required output was never produced.

The benchmark caps runs by *step count* (``BaseTask.max_steps``), not by
wall-clock time. The sibling ``TaskTimeReminderProcessor`` only fires when a
``timeout_seconds`` is configured (it is not in this eval), so today there is
no budget signal reaching the agent at all. This processor supplies the
missing step-based checkpoint.

Mechanism (mirrors ``TaskTimeReminderProcessor`` but step-driven):
* Tracks the step index internally (incremented once per ``on_step_start``).
* Reads the run's ``max_steps`` from ``event.task`` (falls back to a
  configurable default when unavailable).
* At configurable fractions of the budget it injects exactly one ``user``
  message nudging the agent to stop exploring, write every required output
  file, and verify each one exists before continuing.

The nudge is deliberately generic (it names no task, path, or command); it
only reminds the agent of a discipline that applies to *every* terminal task:
produce and verify the named deliverables before the budget runs out.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    Message,
    StepStartEvent,
    TaskStartEvent,
    TaskEndEvent,
)
from harnessx.core.processor import MultiHookProcessor


_WARN_EARLY = (
    "[StepBudgetReminder] You have used about {pct:.0f}% of your step budget "
    "(step {step} of {total}). Stop open-ended exploration and debugging. "
    "Re-read the task, identify every output file / artifact it requires, and "
    "focus the remaining steps on producing them. If you are stuck debugging "
    "the environment, prefer the simplest approach that produces the required "
    "deliverable over a perfect one."
)

_WARN_CRITICAL = (
    "[StepBudgetReminder] CRITICAL: you have used about {pct:.0f}% of your step "
    "budget (step {step} of {total}); only ~{remaining} steps remain, after "
    "which the run is terminated and any missing deliverable scores 0. Do NOT "
    "start new investigation. If any required output file has not been written "
    "yet, write it NOW, then run `ls -l` (or `cat`) on each expected output "
    "path to confirm it exists and is non-empty."
)


class StepBudgetReminderProcessor(MultiHookProcessor):
    """Inject a converge-and-deliver nudge as the step budget is consumed.

    Each configured fraction fires at most once per task.
    """

    _singleton_group = "tmax_step_budget_reminder"
    _order = 7  # after TaskTimeReminderProcessor (6), before length recovery (5 runs earlier hook set)

    def __init__(
        self,
        warn_at: tuple[float, ...] = (0.6, 0.85),
        critical_at: float = 0.85,
        default_max_steps: int = 80,
    ) -> None:
        # De-duplicate and clamp fractions to (0, 1].
        cleaned = sorted({float(w) for w in warn_at if 0.0 < float(w) <= 1.0})
        self._warn_at = tuple(cleaned)
        self._critical_at = float(critical_at)
        self._default_max_steps = max(1, int(default_max_steps))
        self._triggered: set[float] = set()
        self._step: int = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._triggered = set()
        self._step = 0
        yield event

    def _resolve_max_steps(self, event: StepStartEvent) -> int:
        task = getattr(event, "task", None)
        max_steps = getattr(task, "max_steps", None)
        try:
            max_steps = int(max_steps)
        except (TypeError, ValueError):
            max_steps = 0
        if max_steps <= 0:
            max_steps = self._default_max_steps
        return max_steps

    async def on_step_start(self, event: StepStartEvent):
        self._step += 1
        max_steps = self._resolve_max_steps(event)
        pct = self._step / max_steps

        warn: str | None = None
        for threshold in reversed(self._warn_at):
            if pct >= threshold and threshold not in self._triggered:
                self._triggered.add(threshold)
                remaining = max(0, max_steps - self._step)
                fmt = _WARN_CRITICAL if threshold >= self._critical_at else _WARN_EARLY
                warn = fmt.format(
                    pct=pct * 100.0,
                    step=self._step,
                    total=max_steps,
                    remaining=remaining,
                )
                break

        if warn is None:
            yield event
            return

        msg = Message(role="user", content=warn)
        yield dataclasses.replace(
            event,
            messages=event.messages + (msg,),
            raw_messages=event.raw_messages + (msg,),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._triggered = set()
        self._step = 0
        yield event
