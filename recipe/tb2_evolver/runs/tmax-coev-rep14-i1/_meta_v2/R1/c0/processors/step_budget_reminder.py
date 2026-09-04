# SPDX-License-Identifier: MIT
"""StepBudgetReminderProcessor — step-count analog of TaskTimeReminderProcessor.

Motivation
----------
The tmax / TB2 run loop enforces a **step** budget (``BaseTask.max_steps``,
default 80) and terminates a task with ``exit_reason=budget_exceeded`` once
``state.step >= max_steps``.  The existing ``TaskTimeReminderProcessor`` warns
at fractions of a *time* budget, but in the tmax harness path
``timeout_seconds`` is never injected, so that processor is inert and the
budget-approaching case gets **no proactive signal at all**.

``CustomSelfVerifyProcessor`` only fires on *voluntary* exit (finish_reason in
{end_turn, stop} with no tool call).  A task that spirals — burning its whole
step budget debugging one sub-component — never reaches voluntary exit, so it
is terminated at the wall with its required deliverables never finalized.

Observed failure shape (budget_exceeded cluster): the agent gets absorbed in a
single sub-problem (a background proxy, a daemon, a service) for tens of steps,
loses track of the primary deliverable named in the task, and in some cases has
even *renamed or relocated* the required output file away from its exact
specified path.  When the wall arrives, the verifier checks the exact path the
task named and finds nothing (or the wrong name) → automatic 0.

This processor closes that gap generically: as the run approaches its step
budget, it injects a user-role reminder to stop exploring, finalize and verify
every required output file at its **exact** specified path, and keep any
required services alive — without embedding any task-specific knowledge.

Contract
--------
Mirrors ``TaskTimeReminderProcessor``: appends exactly one user-role ``Message``
to ``event.messages`` (and ``raw_messages``) from ``on_step_start``.  Each
threshold fires at most once per task.  No message removal, no system-prompt
mutation — contract-safe.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import Message, StepStartEvent, TaskEndEvent, TaskStartEvent
from harnessx.core.processor import MultiHookProcessor


_STEP_WARN_SOFT = (
    "[StepBudgetReminder] You have used about {pct:.0f}% of your step budget "
    "(step {step} of {total}). Stop broad exploration and debugging side-quests. "
    "Re-read the task description and identify every required deliverable "
    "(output files, running services) and its EXACT specified path/name. "
    "Confirm each required output already exists at its exact path with "
    "`ls -lh <path>` — a file written to a different name or directory does not "
    "count. Spend remaining steps finalizing and verifying deliverables, not "
    "perfecting one sub-component."
)
_STEP_WARN_HARD = (
    "[StepBudgetReminder] CRITICAL: you are near your step budget "
    "(step {step} of {total}). The task will be terminated soon. If any required "
    "output file does NOT yet exist at the EXACT path the task specified, write "
    "it NOW; do not rename, move, or relocate a required deliverable away from "
    "its specified path. Then verify every required output with `ls -lh <path>` "
    "and confirm any required service is still running. A missing or misnamed "
    "output file is an automatic score of 0. Do not start new work — finalize "
    "and verify what the task asked for."
)


class StepBudgetReminderProcessor(MultiHookProcessor):
    """Inject deliverable-finalization reminders as the step budget runs out.

    ``warn_at`` are fractions of ``task.max_steps``.  The highest threshold that
    has just been crossed fires (once each) with an escalating message.
    """

    _singleton_group = "step_budget_reminder"
    _order = 7  # right after TaskTimeReminderProcessor (6)

    def __init__(
        self,
        warn_at: tuple[float, ...] = (0.60, 0.80),
        min_budget: int = 20,
        hard_threshold: float = 0.80,
    ) -> None:
        self._warn_at = sorted(float(w) for w in warn_at)
        self._min_budget = int(min_budget)
        self._hard_threshold = float(hard_threshold)
        self._triggered: set[float] = set()

    async def on_task_start(self, event: TaskStartEvent):
        self._triggered = set()
        yield event

    async def on_step_start(self, event: StepStartEvent):
        task = getattr(event, "task", None)
        total = int(getattr(task, "max_steps", 0) or 0) if task is not None else 0
        # Only meaningful when there is a real, non-trivial step budget.
        if total < self._min_budget:
            yield event
            return

        step = int(getattr(event, "step_id", 0) or 0)
        pct = step / total

        warn: str | None = None
        for threshold in reversed(self._warn_at):
            if pct >= threshold and threshold not in self._triggered:
                self._triggered.add(threshold)
                template = (
                    _STEP_WARN_HARD if threshold >= self._hard_threshold else _STEP_WARN_SOFT
                )
                warn = template.format(pct=pct * 100.0, step=step, total=total)
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
