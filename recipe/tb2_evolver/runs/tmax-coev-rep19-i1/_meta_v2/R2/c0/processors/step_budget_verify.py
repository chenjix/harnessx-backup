# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""StepBudgetVerifyProcessor — force convergence before the step budget runs out.

Closes a systemic ``budget_exceeded`` failure mode observed on Tmax/TB2: an
agent burns its entire step budget on an unresolved sub-problem (a hanging
interactive automation, a flaky build, a stubborn service) and hits the hard
step cap (``exit_reason=budget_exceeded``) *never having confirmed the required
deliverables exist at their exact paths*. In the assigned trajectory the model
even renamed the one file the task explicitly required to exist to work around
an import error, then spent the remaining steps fighting a hanging command —
so at step 80 the required output path did not exist at all and the verifier
hard-failed on a missing file.

The pipeline already carries two convergence mechanisms, but neither fires in
this situation:

* ``CustomSelfVerifyProcessor`` injects an excellent pre-exit verification
  checklist, but only when the model *voluntarily tries to stop* (an
  exit-intent turn). A run that dies at the step cap never produces an
  exit-intent turn, so the checklist is never shown.
* ``TaskTimeReminderProcessor`` warns at time fractions, but it is a no-op
  unless ``timeout_seconds`` is supplied (it is not in the live config), and
  even when active its text is a generic "time is running low" nudge, not a
  concrete "confirm each required output file exists now" checklist.

This processor closes the gap by counting steps itself (the run loop does not
expose a step index on the event, so we track it locally, exactly as
``TaskTimeReminderProcessor`` tracks elapsed time) and, once the run crosses a
configurable fraction of the known step budget, injecting a single
budget-proximity verification message. The message tells the agent it is close
to the hard step cap and must, right now, (a) re-read the task requirements,
(b) confirm every required output artifact exists at its exact stated path and
is non-empty, and (c) stop fighting any single stuck sub-problem and instead
produce the best-possible required deliverables while budget remains.

Generalisation: entirely task-agnostic. It names no task, path, command, or
constant, keys only on the step budget fraction, and fires at most once per
run. It benefits any task at risk of ``budget_exceeded`` — steering the model
to salvage the required outputs before the cap instead of discovering at the
last step that nothing was produced.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    Message,
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

_BUDGET_MSG = (
    "[StepBudget] You are approaching the hard step limit for this task "
    "(~{used}/{budget} steps used). You may not get many more turns, and the "
    "run will be terminated when the cap is hit — leaving whatever state the "
    "workspace is in right now. Stop fighting any single stuck sub-problem and "
    "converge on the deliverables. Do this now, before continuing:\n"
    "1. Re-read the task requirements and list every artifact the task "
    "explicitly requires (exact file paths, running services, log contents).\n"
    "2. For each required file, run `ls -lh <exact_path>` and `head`/`cat` it "
    "to confirm it EXISTS at the exact required path and holds the correct "
    "content. A workaround that renamed, moved, or skipped a required output "
    "means that requirement is currently UNMET — restore it.\n"
    "3. If a sub-step is still failing, prefer a simpler approach that still "
    "satisfies the stated requirement over continuing to retry the failing "
    "one; a partially-correct deliverable at the right path beats an empty or "
    "missing one.\n"
    "4. Verify running services are still alive right now, not just that they "
    "started earlier.\n"
    "Prioritise producing every required output at its exact path with the "
    "remaining budget."
)


class StepBudgetVerifyProcessor(MultiHookProcessor):
    """Inject a one-shot verification checklist as the step budget nears its cap.

    Parameters
    ----------
    step_budget:
        The hard step cap for a task run (Tmax/TB2 default is 80). Used only to
        compute the warning threshold; if the real cap differs the nudge simply
        fires at a slightly different absolute step.
    warn_fraction:
        Fraction of ``step_budget`` at which the one-shot nudge fires
        (default 0.8 → step 64 of 80). Chosen to leave a meaningful tail of
        turns for the agent to act on the checklist before the cap.
    """

    _singleton_group = "step_budget_verify"
    _order = 7  # after TaskTimeReminderProcessor (6); a step_start context nudge

    def __init__(
        self,
        step_budget: int = 80,
        warn_fraction: float = 0.8,
    ) -> None:
        self.step_budget = max(1, int(step_budget))
        self.warn_fraction = min(max(float(warn_fraction), 0.0), 1.0)
        self._threshold = max(1, int(self.step_budget * self.warn_fraction))
        self._steps = 0
        self._fired = False

    async def on_task_start(self, event: TaskStartEvent):
        self._steps = 0
        self._fired = False
        yield event

    async def on_step_start(self, event: StepStartEvent):
        self._steps += 1
        if self._fired or self._steps < self._threshold:
            yield event
            return
        self._fired = True
        msg = Message(
            role="user",
            content=_BUDGET_MSG.format(used=self._steps, budget=self.step_budget),
        )
        yield dataclasses.replace(
            event,
            messages=event.messages + (msg,),
            raw_messages=event.raw_messages + (msg,),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._steps = 0
        self._fired = False
        yield event
