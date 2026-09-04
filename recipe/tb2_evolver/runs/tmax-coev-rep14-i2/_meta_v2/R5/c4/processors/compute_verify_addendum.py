# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ComputeVerifyAddendum — strengthen the self-verify checklist for numeric-compute tasks.

Closes a systemic failure mode observed across the scientific_computing /
data_science compute cluster: the agent writes ONE plausible implementation,
runs it once, sees a self-consistent number ("the values make sense", "this is
in the format"), declares done, and exits after only a handful of steps. The
committed value is deterministically wrong because of a spec-interpretation or
implementation edge case the agent never probed (seed / draw-order, index
convention, search boundary / off-by-one, a data-reading edge case, rounding /
precision). The verifier then compares the number against an exact oracle and
scores reward=0.

The stock TB2 self-verify checklist (CustomSelfVerifyProcessor, _SELF_VERIFY_MSG)
only ever says, generically, "confirm the values are semantically correct" —
which a fast-exiting compute agent satisfies by eyeballing. It contains NO step
that forces an *independent* recomputation (ideally with a different tool /
language) or an explicit sweep of the spec ambiguities that produce wrong
numbers.

This processor is a narrow, mechanism-based augmentation of that existing
checkpoint. It is NOT a broad system-prompt rewrite:

* It arms only on tasks whose description describes a numeric-compute task that
  writes a computed result to an output file (keyword predicate over the task
  description — no task-specific literals).
* It fires at most once per task, and ONLY on the turn where the stock
  self-verify checklist message is actually present as the trailing user
  message (sentinel-gated). On non-compute tasks, or if self-verify never
  fires, it is a complete no-op.
* It appends a generic compute-verification addendum to that one user message.
  It never inserts, drops, or reorders messages (contract-clean, same shape as
  the sibling self-verify-editing processors).

The addendum describes a *strategy* (recompute independently; enumerate and
test the spec ambiguities), never an answer, constant, or task-specific code.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Stable substring of benchmarks.terminal_bench_2.harness._SELF_VERIFY_MSG.
# Used only to detect that the trailing user message IS the self-verify
# checklist, so we augment the right message and nothing else.
_SELF_VERIFY_SENTINEL = "run through this checklist"

# Generic strategy addendum. No task literals, constants, or code.
_COMPUTE_ADDENDUM = (
    "\n\n6. **This task commits a computed numeric result — do NOT trust a single "
    "run.** A program that runs without error can still emit a deterministically "
    "wrong number. Before you finish:\n"
    "   - Recompute the key result an INDEPENDENT way — ideally a different tool or "
    "language than your main solution (e.g. a short Python/numpy check against a "
    "C++ result, or a hand computation on a small slice). If the two disagree, your "
    "main solution has a bug; find it.\n"
    "   - Enumerate the spec ambiguities that most often flip a numeric answer and "
    "confirm your choice matches the wording exactly: random seed and the ORDER in "
    "which draws are consumed; 0- vs 1-based indexing and percentile index rounding; "
    "search-range boundaries and whether endpoints are inclusive; off-by-one in "
    "loops/counts; which axis is the regressor; rounding / precision / float "
    "formatting of the final value.\n"
    "   - Sanity-check that you read EVERY input record (count them) and that no rows "
    "were dropped or misparsed.\n"
    "   Only commit the value once an independent recomputation agrees with it."
)


def _is_compute_task(description: str) -> bool:
    """Arm on numeric-compute tasks that write a computed result to a file.

    Requires BOTH a compute signal AND an output-file signal so that generic
    non-numeric file-writing tasks (build a service, edit a config) are not
    armed. Keyword predicate over the task description only — no task literals.
    """
    if not description:
        return False
    d = description.lower()

    compute_kw = (
        "regression", "least squares", "ols", "bootstrap", "confidence interval",
        "percentile", "monte carlo", "monte-carlo", "simulation", "simulate",
        "optimize", "optimal", "minimiz", "maximiz", "gradient", "centroid",
        "cluster", "mean of", "variance", "standard deviation", "std dev",
        "correlation", "eigen", "integral", "integrat", "numerical", "coefficient",
        "compute the", "calculate the", "estimate the", "fit a", "curve fit",
        "probability", "distribution", "residual",
    )
    output_kw = (
        "result.txt", "output.txt", "report", "write the", "save the result",
        "save the results", "output file", ".csv", ".json", "write to", "output to",
        "print the", "the format", "decimal place",
    )
    has_compute = any(k in d for k in compute_kw)
    has_output = any(k in d for k in output_kw)
    return has_compute and has_output


class ComputeVerifyAddendum(MultiHookProcessor):
    """Append an independent-recomputation directive to the self-verify checklist
    on numeric-compute tasks. Fires <=1x/task, sentinel-gated, contract-clean."""

    _singleton_group = "compute_verify_addendum"
    # After CustomSelfVerifyProcessor (_order=90) so the self-verify message is
    # already present as the trailing user message when we run.
    _order = 92

    def __init__(self) -> None:
        self._armed: bool = False
        self._fired: bool = False

    async def on_task_start(self, event: TaskStartEvent):
        self._armed = False
        self._fired = False
        desc = ""
        # Best-effort description extraction across event shapes.
        for attr in ("task_description", "description", "instruction", "prompt"):
            val = getattr(event, attr, None)
            if isinstance(val, str) and val:
                desc = val
                break
        if not desc:
            task = getattr(event, "task", None)
            if task is not None:
                for attr in ("description", "instruction", "prompt", "text"):
                    val = getattr(task, attr, None)
                    if isinstance(val, str) and val:
                        desc = val
                        break
        if not desc:
            msgs = getattr(event, "messages", None) or ()
            for m in msgs:
                if getattr(m, "role", None) == "user":
                    c = getattr(m, "content", None)
                    if isinstance(c, str) and c:
                        desc = c
                        break
        self._armed = _is_compute_task(desc)
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._armed or self._fired:
            yield event
            return
        msgs = list(event.messages)
        if not msgs:
            yield event
            return
        last = msgs[-1]
        content = getattr(last, "content", None)
        if (
            getattr(last, "role", None) == "user"
            and isinstance(content, str)
            and _SELF_VERIFY_SENTINEL in content
            and _COMPUTE_ADDENDUM not in content
        ):
            self._fired = True
            msgs[-1] = dataclasses.replace(last, content=content + _COMPUTE_ADDENDUM)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._armed = False
        self._fired = False
        yield event
