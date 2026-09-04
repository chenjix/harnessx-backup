# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""NumericRecomputeGate — force an *independent second computation* on the
self-verify turn of computational tasks that emit a small numeric answer.

Motivation (verified in Tmax r0 trajectories)
----------------------------------------------
A recurring, harness-fixable failure shape on scientific_computing /
data_science tasks: the agent writes a program, it compiles/runs cleanly,
prints a plausible-looking numeric answer, and the agent commits it after a
handful of steps (``finished=no_tool_calls``, 7-19 steps). The verifier then
rejects the answer because it is *close but wrong* — the code is structurally
fine but a subtle algorithmic/interpretation choice differs from the
reference, so the number is off by a small amount:

* ``task_000111`` OLS+bootstrap: committed slope ``m=2.5056`` vs expected
  ``2.5997`` (final_pytest ``abs(2.5056-2.5997) > 0.001``).
* ``task_001330`` Monte-Carlo fit: committed ``m=0.048`` vs expected
  ``0.050`` (verifier: "Expected m=0.050, but found m=0.048").
* ``task_001937`` grid optimisation: reported 60 vs expected 50.
* ``task_001035`` primer optimisation: committed ``GCGC`` vs expected
  ``GCAT``.

Why the built-in self-verify turn does not catch it
----------------------------------------------------
The benchmark's built-in ``CustomSelfVerifyProcessor`` fires a one-shot
checklist on exit-intent, but the observed self-verify turns degenerate into
*re-reading the same output file* and re-confirming the format. Re-running the
same program reproduces the same wrong number, so "re-check your output" does
nothing. Worse, in the one passing analogue (``task_000011``) the agent
actually *noticed* a discrepancy between its own hand-computation and its
program output during self-verify, then explicitly talked itself out of
investigating ("let me trust the server output... my manual calculation was
wrong") — it lacked a forcing function to resolve the disagreement before
committing.

What this processor does (distinct from a "re-read the spec" nudge)
-------------------------------------------------------------------
It piggybacks on the existing self-verify turn (the synthetic
``_tb2_self_verify`` tool result routed through ``on_after_tool``). When the
session looks numeric-computational (a short numeric-answer signature appeared
in a real tool result), it appends ONE directive whose payload is a concrete
*action*, not just advice: **compute the answer a second, independent way and
only commit if the two agree.** The second path must not reuse the first
program's code — a fresh one-off Python/awk snippet, a different library, or a
hand-checkable subset. If the two disagree, the agent must find and fix the
cause before finishing rather than trusting the first number.

This is deliberately complementary to a spec-interpretation audit: the audit
tells you *where* to look; this gate makes you *act* — run an independent
cross-check — which is the step every cited trajectory skipped. It contains
**no task-specific literals, constants, algorithms, or file paths**; it
generalises to any task whose deliverable is a small set of numbers (or a
short discrete token chosen by a numeric objective).

Contract safety
---------------
The processor only augments ``event.result`` inside ``on_after_tool``; it
never inserts messages, never mutates the message list, and never touches the
system prompt. Same contract-safe pattern as ``RepeatedCommandBreaker`` and
``CustomEditToolProcessor``. Fires at most once per task.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# The synthetic tool name the built-in CustomSelfVerifyProcessor injects on
# exit-intent. Matching it lets us extend that one-shot verification turn.
_SELF_VERIFY_TOOL = "_tb2_self_verify"

# A value-agnostic "numeric result signature": we detect the *shape* of a short
# result line dominated by numbers (CSV-style "2.5056,1.2262,..." or key=value
# "m=0.048, c=40.237"), never any specific value.
_NUMBER_RE = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?|[-+]?\d+")


def _looks_numeric(text: str) -> bool:
    """True when a tool result contains a short numeric-answer signature.

    Heuristic (value-agnostic): at least one non-empty, short line that after
    stripping contains >= 2 numbers and is mostly numeric/separator characters
    — a genuine result echo, not a big log or a source-code dump.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 200:
            continue
        nums = _NUMBER_RE.findall(line)
        if len(nums) < 2:
            continue
        numeric_chars = sum(c.isdigit() or c in ".,+-eE= \t" for c in line)
        if numeric_chars / max(len(line), 1) >= 0.6:
            return True
    return False


_RECOMPUTE_DIRECTIVE = (
    "\n\n[NumericRecomputeGate] Your deliverable is a computed numeric result. "
    "The single most common cause of a silent WRONG answer on this kind of task "
    "is code that runs cleanly but encodes a subtly different interpretation of "
    "the method than the one intended — so the value is close but off. Re-reading "
    "the same output file (or re-running the same program) CANNOT catch this: it "
    "reproduces the same number. Before you finish you MUST perform ONE "
    "independent cross-check:\n"
    "  1. Recompute the answer a SECOND way that does NOT reuse your first "
    "program's code — e.g. a short one-off Python/awk snippet, a different "
    "library or built-in, or a hand-checkable subset of the input. Run it now.\n"
    "  2. Compare the two results. If they AGREE to the required precision, you "
    "are done. If they DISAGREE, do not commit the first number — the "
    "disagreement is a real bug: re-read the task's method and audit your "
    "interpretation choices (seed/RNG draw order, 0- vs 1-indexing, "
    "row-vs-column orientation, which variable is regressed on which, "
    "inclusive-vs-exclusive bounds, parsing of the first/last input row, "
    "estimator/rounding/percentile-index convention) until you find and fix the "
    "cause, then re-run both paths and confirm they match.\n"
    "Do NOT rationalise a disagreement away by 'trusting' the program output — a "
    "clean run is not evidence of a correct interpretation."
)


class NumericRecomputeGate(MultiHookProcessor):
    """Append an independent-recomputation forcing directive to the self-verify
    turn on numeric-computation tasks.

    Fires at most once per task (bound to the one-shot self-verify tool) and
    only when the session produced a numeric-result signature. Purely augments
    the synthetic ``_tb2_self_verify`` tool result — contract-safe.

    Parameters
    ----------
    self_verify_tool:
        Name of the synthetic verification tool to piggyback on. Defaults to
        the TB2 built-in ``_tb2_self_verify``.
    """

    _singleton_group = "numeric_recompute_gate"
    # After CustomSelfVerifyProcessor (_order=90) so the synthetic self-verify
    # result already exists by the time we augment it.
    _order = 96

    def __init__(self, self_verify_tool: str = _SELF_VERIFY_TOOL) -> None:
        self.self_verify_tool = self_verify_tool
        self._seen_numeric = False
        self._fired = False

    async def on_task_start(self, event: TaskStartEvent):
        self._seen_numeric = False
        self._fired = False
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        # 1. When the self-verify turn fires, augment it once if the run looked
        #    numeric-computational.
        if event.tool_name == self.self_verify_tool:
            if self._seen_numeric and not self._fired:
                self._fired = True
                yield dataclasses.replace(
                    event, result=(event.result or "") + _RECOMPUTE_DIRECTIVE
                )
                return
            yield event
            return

        # 2. Otherwise, watch real tool results for a numeric-answer signature.
        if not self._seen_numeric and event.result and not event.error:
            if _looks_numeric(event.result):
                self._seen_numeric = True
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._seen_numeric = False
        self._fired = False
        yield event
