# SPDX-License-Identifier: MIT
"""NumericCrossCheckProcessor — nudge deterministic-compute tasks to
independently re-derive their committed numeric result once.

Observed failure shape (see journal round on scientific_computing /
data_science): the agent is asked to compute a deterministic numeric
quantity (regression coefficients, a centroid, a confidence interval,
a distance, a checksum, ...), writes a single self-authored program in
one language, runs it *once*, gets a plausible-looking number, and
commits it to the required output file without ever checking that
number against an independent method. The value is often wrong by an
amount far larger than any float tolerance (e.g. a regression slope
or a geometric centroid that lands well outside the grader's rounding
tolerance). The existing self-verify checklist tells the agent
to confirm files exist and "look correct", but with only one
implementation the agent has no independent yardstick to notice a
wrong number, so it re-reads the task, re-`cat`s the file, and exits.

This is a Control gap, not a prompt-knowledge gap: the standard
remediation — recompute the same quantity a second way (a few lines of
Python/numpy, or a hand calculation on a small slice of the input) and
reconcile any discrepancy — is well within the model's capability and
the container's toolchain, but the agent never *reaches for it* at the
decisive moment. So we fire one task-agnostic nudge, keyed on two
observable runtime signals:

  1. the agent compiled/ran a compute program AND wrote a numeric
     result to an output file (detected from its own Bash commands),
  2. the agent is about to finish (exit-intent, same trigger the
     self-verify keepalive uses).

The nudge asks the agent to re-derive the key numeric quantity by an
*independent* second method and reconcile before finishing. It never
names a task, a constant, a path, or an algorithm — it describes the
general cross-validation discipline only. It fires at most once per
task and only on tasks that actually produced a numeric artifact, so
the majority of tasks (services, text edits, config files) are never
touched.

Mechanics mirror the append-only pattern of
``CustomSelfVerifyProcessor`` / ``HttpVerifierDepProcessor``: it only
*appends* a single user message on a model turn; it never blocks a
tool call, never rewrites existing messages, and never mutates the
system prompt.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Signal 1: the agent is running a deterministic numeric computation.
# Kept generic across the common compute stacks in this benchmark
# (compiled C/C++/Fortran/Rust, or interpreted Python/R/Julia numeric
# libs). Matches the *act of computing numbers*, not any one task.
_COMPUTE_SIGNALS = re.compile(
    r"""(?ix)
    (?:
        \bg\+\+\b | \bgcc\b | \bclang\+\+?\b | \bgfortran\b | \brustc\b | \bcargo\s+build\b
      | \bimport\s+numpy\b | \bimport\s+scipy\b | \bimport\s+pandas\b
      | \bnp\.(?:polyfit|mean|std|percentile|linalg|corrcoef|cov)\b
      | \bstd::(?:accumulate|sort)\b
      | \bmt19937\b | \bpolyfit\b | \bols\b | \blinear\s+regression\b
      | \bpercentile\b | \bbootstrap\b | \bcentroid\b | \bcovariance\b
    )
    """
)

# Signal 2: a numeric result is being written to an output file the
# grader will read. Matches a redirect / tee / open-for-write to a
# result-ish artifact, or an ofstream/open() in freshly written code.
_NUMERIC_OUTPUT_SIGNALS = re.compile(
    r"""(?ix)
    (?:
        (?:>>?|tee\s+(?:-a\s+)?)\s*[^\s]*\.(?:txt|csv|json|dat|out)\b
      | \bofstream\b
      | \bopen\s*\(\s*['"][^'"]*\.(?:txt|csv|json|dat|out)['"]\s*,\s*['"][wa]
      | \bto_csv\s*\( | \bjson\.dump\b | \bnp\.savetxt\b
      | \bresult\.(?:txt|csv|json) | \bresults\.(?:txt|csv|json)
    )
    """
)

_NUMERIC_CROSSCHECK_MSG = (
    "[NumericCrossCheck] You computed a numeric result with a single "
    "implementation and wrote it to an output file. A program that "
    "compiles and runs without error can still emit a WRONG number — a "
    "sign slip, an off-by-one index, a wrong accumulation order, a "
    "misparsed column, or a subtly different formula than the one the "
    "task specifies. Re-reading your own code or re-`cat`-ing the output "
    "file cannot catch this, because you have nothing independent to "
    "compare against.\n\n"
    "Before you finish, RE-DERIVE the key numeric quantity a second, "
    "INDEPENDENT way and reconcile it with what you wrote:\n"
    "  - Recompute it in a different language/library than your main "
    "solution (e.g. a few lines of `python3 -c` with numpy/scipy if your "
    "solution was C/C++, or a plain hand-loop if you used a library).\n"
    "  - Or verify against a closed form / a known property (e.g. "
    "residuals of a fit sum to ~0, a probability lies in [0,1], a "
    "distance is non-negative, a percentile lies between the min and "
    "max of the sample).\n"
    "  - Or hand-check the computation on a small slice of the input "
    "where you can work out the expected value by hand.\n\n"
    "If the two methods disagree beyond rounding, your primary result is "
    "the suspect — find the bug and re-run before writing the final "
    "value. Do NOT commit a number you have only produced one way."
)


class NumericCrossCheckProcessor(MultiHookProcessor):
    """Nudge deterministic-compute tasks to cross-verify once on exit."""

    _singleton_group = "numeric_crosscheck"
    _order = 92  # after CustomSelfVerify (90) and HttpVerifierDep (91)

    def __init__(self) -> None:
        self._compute_seen = False
        self._numeric_output_seen = False
        self._fired = False
        self._pending = False

    async def on_task_start(self, event: TaskStartEvent):
        self._compute_seen = False
        self._numeric_output_seen = False
        self._fired = False
        self._pending = False
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            cmd = ""
            try:
                cmd = event.tool_input.get("command", "") or ""
            except Exception:
                cmd = ""
            if cmd:
                if not self._compute_seen and _COMPUTE_SIGNALS.search(cmd):
                    self._compute_seen = True
                if not self._numeric_output_seen and _NUMERIC_OUTPUT_SIGNALS.search(cmd):
                    self._numeric_output_seen = True
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        # Arm the nudge on exit-intent, only when both signals fired.
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if (
            exit_intent
            and not self._fired
            and self._compute_seen
            and self._numeric_output_seen
        ):
            self._pending = True
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending or self._fired:
            yield event
            return
        self._pending = False
        self._fired = True
        yield dataclasses.replace(
            event,
            messages=event.messages
            + (Message(role="user", content=_NUMERIC_CROSSCHECK_MSG),),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._compute_seen = False
        self._numeric_output_seen = False
        self._fired = False
        self._pending = False
        yield event
