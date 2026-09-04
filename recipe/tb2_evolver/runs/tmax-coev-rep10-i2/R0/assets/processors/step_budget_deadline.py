# SPDX-License-Identifier: MIT
"""StepBudgetDeadlineProcessor — step-based deadline nudges.

Terminal-Bench-style tasks run under a hard *step* budget (``task.max_steps``,
80 in the tmax eval). The existing ``TaskTimeReminderProcessor`` keys off a
wall-clock ``timeout_seconds`` that is never populated by the recipe runner, so
it is inert — no deadline nudge ever reaches the model. The observed failure
mode is: the agent explores / thrashes for the whole budget and hits
``exit_reason=budget_exceeded`` with **zero deliverable files written to disk**,
which is an automatic score of 0 regardless of how close the reasoning was.

This processor closes that gap generically. It watches the fraction of the step
budget consumed (``step_id / task.max_steps``) and injects escalating user-role
reminders at configurable fractions. The message tells the agent to stop
exploring, commit its current best-effort output to the required paths, and
verify the files exist with ``ls`` — the single behaviour that turns a
budget_exceeded loss into a possible pass.

It is deliberately task-agnostic: it names no paths, no domains, and no
task ids. It only knows "you are running out of steps; get your deliverables
onto disk now".
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import Message, StepStartEvent, TaskEndEvent, TaskStartEvent
from harnessx.core.processor import MultiHookProcessor

_WARN_EARLY = (
    "[StepBudgetReminder] You have used about {pct:.0f}% of your step budget "
    "(step {step} of {total}). Stop open-ended exploration now. If the task "
    "requires producing output files or artifacts, make sure your current "
    "best-effort version of EVERY required output is written to its exact "
    "expected path, then continue refining. Prefer committing a working "
    "partial result over leaving nothing on disk."
)
_WARN_CRITICAL = (
    "[StepBudgetReminder] CRITICAL: about {pct:.0f}% of your step budget is "
    "gone (step {step} of {total}). The run will be terminated shortly and any "
    "required output that is missing from disk scores 0 automatically. Do NOT "
    "start new investigation or a fundamentally different approach. Write your "
    "best current answer to every required output path IMMEDIATELY, then run "
    "`ls -l` (or `cat`) on each expected path to confirm it exists and is "
    "non-empty."
)


class StepBudgetDeadlineProcessor(MultiHookProcessor):
    """Inject escalating deadline reminders keyed off the step budget.

    Parameters
    ----------
    warn_at:
        Fractions of ``task.max_steps`` at which to fire. The highest fraction
        that is ``>= 0.8`` uses the CRITICAL wording; lower fractions use the
        early wording. Each fraction fires at most once per task.
    """

    _singleton_group = "step_budget_deadline"
    _order = 7  # right after TaskTimeReminderProcessor (_order=6)

    def __init__(self, warn_at: tuple[float, ...] = (0.65, 0.85)) -> None:
        self._warn_at = sorted(float(w) for w in warn_at)
        self._triggered: set[float] = set()

    async def on_task_start(self, event: TaskStartEvent):
        self._triggered = set()
        yield event

    async def on_step_start(self, event: StepStartEvent):
        task = event.task
        total = int(getattr(task, "max_steps", 0) or 0)
        if total <= 0:
            yield event
            return

        step = int(event.step_id or 0)
        pct = step / total

        warn: str | None = None
        for threshold in reversed(self._warn_at):
            if pct >= threshold and threshold not in self._triggered:
                self._triggered.add(threshold)
                template = _WARN_CRITICAL if threshold >= 0.8 else _WARN_EARLY
                warn = template.format(pct=pct * 100, step=step, total=total)
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
        yield event
