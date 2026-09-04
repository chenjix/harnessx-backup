# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""NumericResultAuditProcessor — steer self-verification toward *interpretation*
audit on computational tasks that emit a numeric result.

Motivation (from Tmax r0 trajectories)
--------------------------------------
A recurring failure class on scientific_computing / data_science tasks: the
agent writes a program, it compiles/runs cleanly, produces a plausible-looking
numeric answer, and the agent exits after very few steps (``no_tool_calls``,
7-15 steps). The verifier then rejects the answer because it is *close but
wrong* — the number came from a subtle misread of the algorithmic
specification, e.g.:

* OLS + bootstrap: slope 2.5056 vs expected 2.5997 (task_000111) — the
  computation was structurally correct but the data/index interpretation
  differed from the reference;
* Monte-Carlo trajectory fit: m=0.048 vs expected 0.050 (task_001330) — the
  order / application of the seeded RNG draws differed from the reference;
* grid optimisation: reported 60 vs expected 50 (task_001937).

The benchmark's built-in ``CustomSelfVerifyProcessor`` already fires a
one-shot checklist on exit-intent, but that checklist is oriented toward
*file existence and output format* ("does the file exist at the exact path",
"is the format right"). In every cited trajectory the agent's self-verify
turn degenerated into re-``cat``-ing the file and re-confirming the format —
it never re-examined whether its *interpretation of the method* was the one
the task intended. Re-running the same program reproduces the same wrong
number, so a "re-check your output" nudge alone does not help; what these
tasks needed was a nudge to re-read the *ambiguous algorithmic points* and
consider the alternative interpretation.

What this processor does
------------------------
It piggybacks on the existing self-verify turn. When the built-in
``CustomSelfVerifyProcessor`` fires its synthetic ``_tb2_self_verify`` tool,
the run loop routes that synthetic result through ``on_after_tool``. This
processor appends an *additional* computational-interpretation audit
directive to that result — but only when the session actually looks
numeric-computational (a short numeric-looking result was produced during
the run). On non-numeric tasks it stays completely silent, so it adds no
nagging surface to the already-passing clusters.

This is a strategy-level directive (re-read the spec; question ordering,
indexing, seed/RNG usage, parsing edge cases; sanity-check against a tiny
hand-computed case). It contains **no task-specific literals, constants,
algorithms, or file paths** — it generalises to any computational task whose
answer is a small set of numbers.

Contract safety
---------------
The processor only augments ``event.result`` inside ``on_after_tool``; it
never inserts messages, never touches the system prompt, and never mutates
the message list. This is the same contract-safe pattern used by
``RepeatedCommandBreaker`` and ``CustomEditToolProcessor``.
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

# A "numeric result signature": a tool output line that is dominated by
# floating-point / integer numbers, optionally comma- or key=value-separated
# (e.g. "2.5056,1.2262,3.9742,6.2925" or "m=0.048, c=40.237"). We detect the
# *shape*, not any specific value.
_NUMBER_RE = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?|[-+]?\d+")


def _looks_numeric(text: str) -> bool:
    """True when a tool result contains a short numeric-answer signature.

    Heuristic (value-agnostic): at least one non-empty line that, after
    stripping, contains >= 2 numbers and is short (a result line, not a big
    data dump). This catches CSV-style and key=value result echoes without
    firing on large logs or source-code echoes.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 200:
            continue
        nums = _NUMBER_RE.findall(line)
        if len(nums) < 2:
            continue
        # Fraction of the line that is numeric/separator characters — a genuine
        # result line is mostly digits, dots, commas, signs, and separators.
        numeric_chars = sum(c.isdigit() or c in ".,+-eE= \t" for c in line)
        if numeric_chars / max(len(line), 1) >= 0.6:
            return True
    return False


_AUDIT_DIRECTIVE = (
    "\n\n[NumericResultAudit] Your output is a computed numeric result. A "
    "program that runs cleanly and prints a plausible number is the most "
    "common source of a silent WRONG answer: the code is fine but your "
    "*interpretation of the method* differs from what the task intended, so "
    "the value is close but not exact. Re-running the same code will reproduce "
    "the same wrong number, so do NOT just re-read the output file. Instead, "
    "before you finish, re-read the task's method description and explicitly "
    "audit each interpretation choice:\n"
    "  1. ORDER OF OPERATIONS / RNG: if a random seed or a fixed number of "
    "draws is specified, confirm the seed is set exactly once at the specified "
    "point and that you draw random numbers in the exact order and grouping "
    "the task implies — a different draw order changes seeded results.\n"
    "  2. INDEX / COORDINATE / AXIS CONVENTIONS: confirm 0- vs 1-indexing, "
    "row-vs-column (x=column, y=row) orientation, inclusive-vs-exclusive "
    "bounds, and which variable is regressed on which.\n"
    "  3. PARSING EDGE CASES: confirm every input row was read (check the "
    "first and last lines, trailing newline/blank line, any header) and that "
    "none were silently skipped or mis-split.\n"
    "  4. ROUNDING / PRECISION / FORMULA VARIANT: confirm you used the exact "
    "estimator/formula and rounding the task asked for (e.g. population vs "
    "sample, percentile index convention).\n"
    "  5. SANITY CHECK: recompute the answer a second, independent way (a "
    "different library or a tiny hand-checkable subset) and confirm the two "
    "agree. If the task states or implies an expected magnitude, verify yours "
    "is consistent.\n"
    "If any interpretation is ambiguous, try the alternative reading and see "
    "whether it changes the result before committing."
)


class NumericResultAuditProcessor(MultiHookProcessor):
    """Append a computational-interpretation audit to the self-verify turn.

    Fires at most once per task (bound to the one-shot self-verify tool) and
    only when the session produced a numeric-result signature. Purely augments
    the synthetic ``_tb2_self_verify`` tool result — contract-safe.

    Parameters
    ----------
    self_verify_tool:
        Name of the synthetic verification tool to piggyback on. Defaults to
        the TB2 built-in ``_tb2_self_verify``.
    """

    _singleton_group = "numeric_result_audit"
    # After CustomSelfVerifyProcessor (_order=90) so the synthetic self-verify
    # result already exists by the time we augment it.
    _order = 95

    def __init__(self, self_verify_tool: str = _SELF_VERIFY_TOOL) -> None:
        self.self_verify_tool = self_verify_tool
        self._seen_numeric = False
        self._fired = False

    async def on_task_start(self, event: TaskStartEvent):
        self._seen_numeric = False
        self._fired = False
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        # 1. When the self-verify turn fires, augment it (once) if the run
        #    looked numeric-computational.
        if event.tool_name == self.self_verify_tool:
            if self._seen_numeric and not self._fired:
                self._fired = True
                yield dataclasses.replace(
                    event, result=(event.result or "") + _AUDIT_DIRECTIVE
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
