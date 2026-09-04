# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""NumericCrossCheckNudge — one-shot pre-exit reminder to independently
re-derive a *computed numeric* deliverable before committing it.

Motivation (Tmax/TB2 ``scientific_computing`` cluster)
------------------------------------------------------
On this round every ``scientific_computing`` task scored 0. The recurring
*shape* — not the recurring *bug* — is this: the agent writes a plausible
implementation, runs it once, obtains a specific number (a regression slope, a
Monte-Carlo mean, a numerical integral, an optimal grid size), verifies only
that the output FILE exists and is correctly *formatted*, and exits after very
few steps. Two assigned/adjacent trajectories terminated in 7 steps having
never cross-checked the *value* by any independent route:

* ``task_000111`` — hand-rolled OLS produced slope 2.5056; the reference
  expected 2.5997. The code was textbook and self-consistent, so the agent had
  no internal signal that it was off.
* ``task_001330`` — Monte-Carlo line fit committed on the first run with no
  reconciliation of the polyfit argument order / noise-application convention
  against a second formulation.

The verifier's test files are injected only *after* the agent exits (TB2
sandbox topology), so the agent can never read the grader. Its only defence
against a spec-noncompliant number is to *reconcile the value against an
independent second method of its own* before committing — e.g. a manual
normal-equations slope cross-checked with ``numpy.polyfit``; a manual Riemann /
trapezoid integral cross-checked with ``scipy.integrate``; a closed-form mean
cross-checked with the empirical one. When two independent routes disagree
beyond rounding, the implementation (or its reading of the spec) is wrong and
the agent still has budget to investigate.

Why this is not task-specific knowledge
---------------------------------------
The nudge names no task, no dataset, no algorithm, no constant, and no file
path. It encodes a single general strategy — *cross-validate a required
computed number by a second independent method before finishing* — that
transfers to any task whose deliverable is a specific computed value. It is the
computational analogue of the existing file-existence checklist.

Composition with the existing pipeline
---------------------------------------
``CustomSelfVerifyProcessor`` (order 90) already intercepts the first
exit-intent turn, converts it into a keepalive tool call, and appends a
file/format verification checklist on the following ``on_before_model``. This
processor is deliberately ordered *after* it (order 91) and keys on the *same*
exit-intent signal, appending its numeric-reconciliation addendum as one extra
user message on that same pre-model turn. It fires **at most once per task**
and is a no-op on the exit turn if it never sees an exit-intent, so a run that
dies at the step cap is unaffected. It adds no tool calls of its own, so it
cannot interfere with the keepalive mechanism.
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
    "a count)? If so, a file that exists and is correctly formatted is NOT "
    "enough — the number itself must match the intended calculation, and the "
    "grader is hidden from you, so a self-consistent-but-wrong implementation "
    "will pass all of your own checks and still fail.\n"
    "Do this now, once, before exiting:\n"
    "1. Re-derive each required numeric result by a SECOND, independent method "
    "and compare. Examples of independent second routes: a hand-rolled formula "
    "cross-checked against a library primitive (or vice-versa); a closed-form "
    "value against an empirical/simulated one; a different but equivalent "
    "algebraic formulation. If two independent routes agree to the required "
    "precision, trust the value; if they disagree beyond rounding, your "
    "implementation or your reading of the spec is wrong — investigate before "
    "committing.\n"
    "2. Re-read the specification for every convention that silently changes "
    "the number: which variable is regressed on which, exact seed and RNG "
    "call order, sample size, percentile-index convention, rounding rule, "
    "0- vs 1-indexing, and units. A number that is 'close' is still a failure "
    "if such a convention is off.\n"
    "3. Sanity-check magnitude and sign against the described physical/"
    "statistical meaning of the quantity.\n"
    "Only after the value survives an independent cross-check should you "
    "finalize the output file. If it is not a computed-number task, ignore "
    "this and proceed."
)


class NumericCrossCheckNudge(MultiHookProcessor):
    """Inject a one-shot numeric-reconciliation reminder on the exit-intent turn.

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
