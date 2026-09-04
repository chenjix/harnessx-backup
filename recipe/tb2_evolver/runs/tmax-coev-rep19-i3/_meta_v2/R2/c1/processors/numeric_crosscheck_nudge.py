# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""NumericCrossCheckNudge — one-shot pre-exit reminder to independently
re-derive a *computed numeric* deliverable before committing it, extended
with an *optimum-selection* clause for search/optimization-trajectory tasks.

Motivation (Tmax/TB2 ``scientific_computing`` cluster)
------------------------------------------------------
The recurring *shape* — not a recurring *bug* — is: the agent writes a
plausible implementation, runs it once, obtains a specific value, verifies
only that the output FILE exists and is correctly *formatted*, and exits after
very few steps, with no independent cross-check of the VALUE.

R2 addition — the "last iterate is not the optimum" trap
--------------------------------------------------------
A distinct sub-shape within this cluster: when the deliverable is *the optimal
/ converged / best / minimum / maximum* result of a **search or optimization
run** (simulated annealing, MCMC, gradient descent, genetic / random search,
grid sweep), the answer is the candidate that OPTIMISES the recorded objective
— i.e. the position/parameter at ``argmin``/``argmax`` of the recorded score —
NOT the last value the search happened to visit or log. Stochastic searches
accept a random move on their final step, so the last-iterate value is usually
*not* the optimum. An agent that reads only the trajectory of candidates and
grabs the tail element commits a value that is one perturbation away from the
true optimum and fails a hidden exact-match grader, even though every mechanical
step (fix, compile, run, extract) was correct.

The remedy is general and names no task, dataset, algorithm, constant, or path:
when the spec asks for *the optimum of a search*, select it by the objective —
inspect the recorded score/energy/error series and take the candidate at its
best value — and reconcile that against the last-iterate value; if they differ,
the last iterate is a stochastic wobble, not the answer.

Why this is not task-specific knowledge
---------------------------------------
Both clauses encode general strategies — *cross-validate a required computed
number by a second independent method*, and *select the optimum of a search by
its objective, not by recency* — that transfer to any task whose deliverable is
a specific computed value or the optimum of a search. It is the computational
analogue of the existing file-existence checklist.

Composition with the existing pipeline
---------------------------------------
``CustomSelfVerifyProcessor`` (order 90) already intercepts the first
exit-intent turn. This processor is ordered *after* it (order 91) and keys on
the *same* exit-intent signal, appending its addendum as one extra user message
on that same pre-model turn. It fires **at most once per task**, is a no-op on
runs that never reach an exit-intent turn, and emits no tool calls of its own,
so it composes with the self-verify keepalive rather than competing with it.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

_CROSSCHECK_MSG = (
    "[NumericCrossCheck] Before you finish: does this task require a specific "
    "COMPUTED value as its deliverable (e.g. a fitted coefficient, a mean, a "
    "confidence-interval bound, a numerical integral, an optimized parameter, "
    "a count), OR the OPTIMUM of a search/optimization (the best / converged / "
    "minimum / maximum result)? If so, a file that exists and is correctly "
    "formatted is NOT enough — the value itself must match the intended "
    "calculation, and the grader is hidden from you, so a self-consistent-but-"
    "wrong implementation will pass all of your own checks and still fail.\n"
    "Do this now, once, before exiting:\n"
    "1. Re-derive each required numeric result by a SECOND, independent method "
    "and compare. Examples of independent second routes: a hand-rolled formula "
    "cross-checked against a library primitive (or vice-versa); a closed-form "
    "value against an empirical/simulated one; a different but equivalent "
    "algebraic formulation. If two independent routes agree to the required "
    "precision, trust the value; if they disagree beyond rounding, your "
    "implementation or your reading of the spec is wrong — investigate before "
    "committing.\n"
    "2. If the deliverable is the OPTIMUM of a search or optimization run "
    "(simulated annealing, MCMC, gradient descent, genetic/random search, grid "
    "sweep), select it BY THE OBJECTIVE, not by recency: inspect the recorded "
    "score/energy/error/loss series and take the candidate (position, "
    "parameter, sequence, grid size) at its best value — argmin for a "
    "minimisation, argmax for a maximisation. Do NOT assume the LAST value the "
    "search visited or logged is the optimum: stochastic searches accept a "
    "random move on their final step, so the last iterate is usually one "
    "perturbation away from the true optimum. Reconcile the argmin/argmax "
    "candidate against the last-iterate value; if they differ, the last iterate "
    "is a wobble, not the answer.\n"
    "3. Re-read the specification for every convention that silently changes "
    "the number: which variable is regressed on which, exact seed and RNG "
    "call order, sample size, percentile-index convention, rounding rule, "
    "0- vs 1-indexing, and units. A number that is 'close' is still a failure "
    "if such a convention is off.\n"
    "4. Sanity-check magnitude and sign against the described physical/"
    "statistical meaning of the quantity.\n"
    "Only after the value survives an independent cross-check should you "
    "finalize the output file. If it is not a computed-number or search-optimum "
    "task, ignore this and proceed."
)


class NumericCrossCheckNudge(MultiHookProcessor):
    """Inject a one-shot numeric-reconciliation + optimum-selection reminder on
    the exit-intent turn.

    Fires at most once per task, on the first turn where the model tries to
    stop without a tool call (``finish_reason ∈ {end_turn, stop}`` and no
    ``tool_calls``) — the same signal ``CustomSelfVerifyProcessor`` keys on.
    The reminder is appended as a single extra user message on the next
    ``on_before_model`` and does not emit any tool call, so it composes with
    the existing self-verify keepalive rather than competing with it.
    """

    _singleton_group = "numeric_crosscheck_nudge"
    _order = 91  # immediately after CustomSelfVerifyProcessor (90)

    def __init__(self) -> None:
        self._fired = False
        self._pending = False

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._pending = False
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._fired:
            self._fired = True
            self._pending = True
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending:
            yield event
            return
        self._pending = False
        msg = Message(role="user", content=_CROSSCHECK_MSG)
        yield dataclasses.replace(
            event,
            messages=event.messages + (msg,),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._pending = False
        yield event
