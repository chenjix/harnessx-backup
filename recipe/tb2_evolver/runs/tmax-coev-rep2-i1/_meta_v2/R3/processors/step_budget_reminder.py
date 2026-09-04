# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""StepBudgetReminderProcessor — inject a "converge and produce deliverables"
nudge as the agent approaches the hard step-budget limit.

Systemic failure mode (Tmax eval, R2 trajectories):
  A distinct cluster of tasks exits with ``exit_reason=budget_exceeded`` at the
  full step budget (80 steps): task_000010, task_000118, task_000958,
  task_001031, task_001032, task_001321. Unlike R1's identical-command loops
  (already broken by LengthLoopDeprime + RepeatedCommandBreaker — max consecutive
  identical calls in R2 is now only 3-5, down from 21-33), these tasks now burn
  the budget through *varied but unproductive* exploration and run out of steps
  before writing/verifying the required output artifacts.

Why no existing guard covers this:
  * ``TaskTimeReminderProcessor`` is the natural home for a budget-pressure
    signal, but in the live config it is instantiated WITHOUT ``timeout_seconds``
    — so ``self._timeout`` is None and it no-ops on every step (verified: it
    fired 0 times on all six budget_exceeded tasks). The wall-clock budget is not
    the binding constraint here; the STEP budget is, and nothing warns on it.
  * ``CustomSelfVerifyProcessor`` only fires when the model tries to EXIT
    (finish_reason=end_turn with no tool calls). The budget_exceeded cluster
    never voluntarily exits — it is cut off at max_steps — so the self-verify
    checkpoint is never reached.

Mechanism (Control lever): count our own ``on_step_start`` invocations to track
the current step, read the hard budget from ``event.task.max_steps`` (no
hardcoded constant — adapts to whatever budget the runner sets), and when the
agent crosses a configurable fraction of the budget, append a single user
message telling it to STOP exploring and spend its remaining steps writing and
verifying the concrete deliverables the task requires at their exact paths. Each
threshold band fires at most once per task.

Pareto safety (why regression risk is near-zero):
  In the R2 run, 29 of 30 PASSING tasks finished in 8-37 steps — far below the
  first (0.75) threshold band. Only one passing task (task_001818, 69 steps)
  even reaches the band, and a "focus on writing/verifying required deliverables
  now" nudge is exactly aligned with what a near-budget task should do — it does
  not redirect the agent away from a productive path, it reinforces convergence.
  The message is advisory only; this processor NEVER raises.

Contract-safe: mirrors ``TaskTimeReminderProcessor`` exactly — in
``on_step_start`` it appends at most one ``role="user"`` message to both
``event.messages`` and ``event.raw_messages`` (never removes, never touches the
system prompt, never mutates tool results or history ordering).
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


_STEP_WARN = (
    "[StepBudgetReminder] You have used about {pct:.0%} of your step budget "
    "(~{used} of {total} steps). You will be cut off when the budget runs out, "
    "and the task is scored ONLY on the concrete output artifacts it asks for — "
    "not on your exploration. STOP investigating and STOP re-running diagnostics. "
    "Re-read the task description, list every required deliverable (files at "
    "specific paths, a running service, a report), and spend your remaining steps "
    "WRITING and then VERIFYING each one exists and is non-empty at its exact "
    "path (`ls -l <path>` / `cat <path>`)."
)

_STEP_WARN_CRIT = (
    "[StepBudgetReminder] CRITICAL: you have used about {pct:.0%} of your step "
    "budget (~{used} of {total} steps) and will be terminated very soon. Any "
    "required output file that is missing at that point scores 0. Do NOT begin "
    "any new investigation. In your remaining steps, write every required "
    "deliverable to its exact path and confirm each one with `ls -l` / `cat`, "
    "then finish."
)


class StepBudgetReminderProcessor(MultiHookProcessor):
    """Warn-only step-budget pressure signal for the budget_exceeded cluster.

    Args:
        warn_at:  fractions of ``task.max_steps`` at which to fire, low->high.
                  Each band fires at most once. Default (0.75, 0.90).
        default_max_steps: fallback budget when ``task.max_steps`` is missing
                  or non-positive (default 80, the Tmax runner default).
    """

    _singleton_group = "tmax_step_budget_reminder"
    _order = 7  # right after TaskTimeReminderProcessor (_order=6); step_start hook

    def __init__(
        self,
        warn_at: tuple[float, ...] = (0.75, 0.90),
        default_max_steps: int = 80,
    ) -> None:
        # keep only sane fractions in (0, 1], sorted ascending
        self._warn_at = sorted(f for f in warn_at if 0.0 < float(f) <= 1.0)
        self._default_max_steps = max(1, int(default_max_steps))
        self._triggered: set[float] = set()
        self._step: int = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._triggered = set()
        self._step = 0
        yield event

    async def on_step_start(self, event: StepStartEvent):
        self._step += 1

        total = self._default_max_steps
        task = getattr(event, "task", None)
        if task is not None:
            cand = getattr(task, "max_steps", None)
            if isinstance(cand, int) and cand > 0:
                total = cand

        pct = self._step / total

        warn: str | None = None
        # fire the highest crossed, not-yet-triggered band
        for threshold in reversed(self._warn_at):
            if pct >= threshold and threshold not in self._triggered:
                self._triggered.add(threshold)
                template = _STEP_WARN_CRIT if threshold >= 0.90 else _STEP_WARN
                warn = template.format(pct=pct, used=self._step, total=total)
                break

        if warn:
            msg = Message(role="user", content=warn)
            yield dataclasses.replace(
                event,
                messages=event.messages + (msg,),
                raw_messages=event.raw_messages + (msg,),
            )
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._triggered = set()
        self._step = 0
        yield event
