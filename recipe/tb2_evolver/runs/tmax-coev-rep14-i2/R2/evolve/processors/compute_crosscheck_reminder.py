# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ComputeCrossCheckReminder — a TB2 Control-lever processor.

Problem it addresses
--------------------
On computational tasks (regression, bootstrap/CI, clustering/centroids,
matrix/DP recurrences, optimisation search, Monte-Carlo simulation, …) the
agent frequently writes ONE implementation, runs it ONCE, sees a
plausible-looking number, and exits after a *format-only* self-verification.
The committed value is self-consistent but numerically wrong (deterministic
computation bug, off-by-one, seed/draw-order, parsing, precision). The stock
``CustomSelfVerifyProcessor`` checklist nudges "confirm the values are
semantically correct" but never asks the agent to *re-derive the key result
with an independent method* — the one discipline that would surface a
deterministic computation bug (e.g. an OLS slope that any second correct
implementation would disagree with).

Mechanism
---------
This processor rides on top of the existing self-verify mechanism instead of
competing with it for exit-intent. When the ``_tb2_self_verify`` synthetic
ACK tool-result flows through ``on_after_tool`` on an *armed* task, it appends
one extra cross-validation directive to that result string and disarms.

Contract-safety: it mutates ONLY the tool-result ``result`` string (same shape
as ``CustomEditToolProcessor``); it inserts no messages, touches no system
prompt, and fires at most once per task. Arming is by generic
numeric-computation keywords in the task description — no task-specific
literals, ids, constants, paths, or code.

Retroactive intent: corrective. On a deterministic computation (OLS slope,
DP-matrix value, centroid) an *independent* recompute disagrees with the buggy
committed value, giving the agent a concrete reason to look for the bug before
exiting rather than declaring "the values make sense".
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

# The synthetic self-verify tool name used by CustomSelfVerifyProcessor.
_SELF_VERIFY_TOOL = "_tb2_self_verify"

# Generic signals that the task commits a *computed numeric result*. Kept
# deliberately broad-but-numeric so pure text/formatting tasks are not armed.
# No task-specific literals — these are domain-agnostic computation terms.
_COMPUTE_TERMS = (
    "regression",
    "least squares",
    "ols",
    "slope",
    "coefficient",
    "bootstrap",
    "confidence interval",
    "percentile",
    "quantile",
    "monte carlo",
    "monte-carlo",
    "resample",
    "simulation",
    "simulate",
    "integrat",          # integrate / integrator / integration
    "residual",
    "centroid",
    "cluster",
    "k-means",
    "kmeans",
    "distance metric",
    "similarity",
    "eigen",
    "gradient",
    "optimal",
    "optimi",            # optimise / optimize / optimisation
    "minimi",            # minimise / minimize
    "maximi",            # maximise / maximize
    "correlation",
    "covariance",
    "probability",
    "statistic",
    "numeric",
    "numerical",
    "matrix",
    "dynamic programming",
    "recurrence",
    "compute the",
    "calculate the",
)

# Require *some* evidence the result is written/reported as a numeric value,
# so we don't arm on tasks that merely mention e.g. "matrix" in prose.
_RESULT_TERMS = (
    "round",           # "round to N decimal places"
    "decimal",
    "precision",
    "4 decimal",
    "result.txt",
    "report",
    "output",
    "write",
    "save",
    "value",
    "mean",
    "median",
    "sum",
    "average",
    "score",
    "ci_",
    "lower",
    "upper",
)

_CROSSCHECK_NUDGE = (
    "\n\n[CrossCheck — computed results] This task commits a computed numeric "
    "result. A syntactically correct program that runs without error can still "
    "produce the WRONG number. Before you finish:\n"
    "1. Re-derive the key value(s) using an INDEPENDENT method or library "
    "(e.g. a short Python/numpy recomputation, or a hand check on a small "
    "slice) and confirm it agrees with your committed output to the required "
    "precision. A second computation that merely reruns the SAME code is not "
    "independent.\n"
    "2. If the two disagree, do NOT exit — probe the usual culprits: off-by-one "
    "/ indexing, data parsing (column order, header, delimiter, float "
    "precision), seed and per-iteration draw order, rounding, and search "
    "boundaries — then fix the discrepancy.\n"
    "3. Sanity-check input record counts and intermediate ranges match the "
    "task's stated sizes before trusting the final number."
)


class ComputeCrossCheckReminder(MultiHookProcessor):
    """Append an independent-recompute nudge to the self-verify ACK on
    numeric-computation tasks. Fires at most once per task; mutates only the
    tool-result string (contract-safe)."""

    _singleton_group = "tb2_compute_crosscheck"
    _order = 95  # after CustomSelfVerifyProcessor (_order=90) so the ACK exists

    def __init__(self, min_result_terms: int = 1) -> None:
        self.min_result_terms = min_result_terms
        self._armed = False
        self._fired = False

    @staticmethod
    def _is_compute_task(desc: str, min_result_terms: int) -> bool:
        d = desc.lower()
        has_compute = any(term in d for term in _COMPUTE_TERMS)
        if not has_compute:
            return False
        n_result = sum(1 for term in _RESULT_TERMS if term in d)
        return n_result >= min_result_terms

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._armed = self._is_compute_task(
            event.task_description or "", self.min_result_terms
        )
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if (
            self._armed
            and not self._fired
            and event.tool_name == _SELF_VERIFY_TOOL
        ):
            self._fired = True
            yield dataclasses.replace(
                event, result=(event.result or "") + _CROSSCHECK_NUDGE
            )
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._armed = False
        self._fired = False
        yield event
