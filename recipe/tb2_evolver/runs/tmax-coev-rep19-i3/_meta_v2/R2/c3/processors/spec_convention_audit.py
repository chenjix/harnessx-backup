# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""SpecConventionAuditNudge — one-shot pre-exit reminder to adversarially
re-read every value-changing spec *convention* against a computed numeric
(or exact-string) deliverable before committing it.

Motivation (Tmax/TB2 ``scientific_computing`` cluster)
------------------------------------------------------
On this benchmark the entire ``scientific_computing`` cluster scored 0, and
the verifier tails all show the SAME shape: a self-consistent implementation
that is off by a *specification convention*, committed after a single run.
Representative failures (mechanism, not task knowledge):

* OLS slope 2.5056 vs an expected 2.5997 — deterministic regression, textbook
  code, yet ~3.7% off: a convention deviation the agent never audited.
* "regress x on y (not y on x)", ``numpy.random.seed(42)``, exact frame /
  brightest-pixel tie-break — committed m=0.048 vs 0.050; the grader message
  literally blamed the seed/parameter convention.
* A numerical-integration result off by an endpoint/bin-boundary convention.
* An exact output string shifted by one character (off-by-one window offset).
* An optimization result that stopped on the wrong search convention.

The dominant bug is a **convention misread**, not an arithmetic error. That
matters because the naive remedy — "re-derive the number by a second
independent method and compare" — is *weak* here: two independent methods that
share the same misread convention (same axis choice, same seed/RNG-call order,
same off-by-one, same endpoint rule) agree with each other and stay equally
wrong. The agent's own cross-check gives it false confidence.

Why this is not task-specific knowledge
---------------------------------------
The nudge names no task, no dataset, no algorithm, no constant, no expected
value, and no file path. It encodes a single general strategy — *before
committing an exact computed deliverable, adversarially re-read every spec
convention that silently changes the value, and treat "close but not exact" as
a convention-mismatch signal* — that transfers to any task whose deliverable is
a specific computed value or exact string. It is the computational analogue of
the file-existence checklist.

The TB2 sandbox injects the verifier only *after* the agent exits, so the
agent can never read the grader; its only defence against a spec-noncompliant
value is to audit its own reading of the spec while it still has budget.

Composition with the existing pipeline
---------------------------------------
This supersedes the R1 ``NumericCrossCheckNudge`` in the same pipeline slot
(so the two do not double-inject competing pre-exit reminders).
``CustomSelfVerifyProcessor`` (order 90) intercepts the first exit-intent turn,
converts it into a keepalive tool call, and appends a file/format checklist on
the following ``on_before_model``. This processor is ordered *after* it
(order 91) and keys on the *same* exit-intent signal, appending its
convention-audit addendum as one extra user message on that same pre-model
turn. It fires **at most once per task**, is a no-op on runs that never reach
an exit-intent turn, and emits no tool call of its own, so it cannot interfere
with the keepalive mechanism.
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

_AUDIT_MSG = (
    "[SpecConventionAudit] Before you finish: does this task require an EXACT "
    "computed value or exact string as its deliverable (a fitted coefficient, "
    "a mean, a confidence-interval bound, a numerical integral, an optimized "
    "parameter, a count, a sequence)? If so, a file that exists and is "
    "correctly formatted is NOT enough. The grader is hidden from you and "
    "checks the value to fixed precision, and the most common way a "
    "textbook-correct, self-consistent implementation still fails is a "
    "SPECIFICATION-CONVENTION mismatch: a value that is 'close but not exact' "
    "is a failure, and it usually means you followed a subtly different "
    "convention than the one the spec dictated.\n"
    "Do this now, once, before exiting:\n"
    "1. ADVERSARIAL CONVENTION RE-READ (do this first). Re-read the spec "
    "line by line and, for EACH value-changing convention, confirm your "
    "implementation matches it exactly — do not assume the textbook default:\n"
    "   - which variable is regressed / mapped onto which (axis and order);\n"
    "   - exact random seed AND the exact order/count of RNG draws;\n"
    "   - indexing and off-by-one: 0- vs 1-based, inclusive vs exclusive "
    "bounds, percentile-index rule, window/offset start;\n"
    "   - endpoint / boundary handling for sums, integrals, bins, ranges;\n"
    "   - exact sample size / iteration count / stopping rule;\n"
    "   - rounding rule and number of decimal places;\n"
    "   - units and any stated scaling or normalization.\n"
    "   For each one, point to the exact spec sentence that fixes it and to "
    "the line of your code that implements it. A mismatch here is the most "
    "likely cause of a close-but-wrong value.\n"
    "2. Only after the convention audit, cross-check the number by a SECOND, "
    "independent route (a hand-rolled formula vs a library primitive, a "
    "closed-form vs an empirical value). If the two routes agree but you have "
    "NOT verified every convention above, they may be agreeing on the same "
    "misread convention — trust agreement only once step 1 is clean.\n"
    "3. Sanity-check magnitude and sign against the described physical / "
    "statistical meaning of the quantity.\n"
    "If any convention is off, fix it and re-run before finalizing the output "
    "file. If this is not an exact-value / exact-string task, ignore this and "
    "proceed."
)


class SpecConventionAuditNudge(MultiHookProcessor):
    """Inject a one-shot convention-audit reminder on the exit-intent turn.

    Fires at most once per task, on the first turn where the model tries to
    stop without a tool call (``finish_reason in {end_turn, stop}`` and no
    ``tool_calls``) — the same signal ``CustomSelfVerifyProcessor`` keys on.
    The reminder is appended as a single extra user message on the next
    ``on_before_model`` and emits no tool call, so it composes with the
    existing self-verify keepalive rather than competing with it.
    """

    _singleton_group = "spec_convention_audit_nudge"
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
        msg = Message(role="user", content=_AUDIT_MSG)
        yield dataclasses.replace(
            event,
            messages=event.messages + (msg,),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._pending = False
        yield event
