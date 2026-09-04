# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""NumericCrossCheckNudgeProcessor — prompt an independent recompute on
numeric/scientific-computing tasks before the agent commits its answer.

Motivation (harness deficiency, not a task solution)
-----------------------------------------------------
A recurring failure shape in the Tmax ``scientific_computing`` cluster:
the agent writes plausible, textbook-looking numerical code (OLS fit,
bootstrap CI, Monte-Carlo mean, percentile bounds, seeded RNG …), runs it
once, sees a well-formed numeric result file, and declares the task done —
without ever re-deriving the key quantity by an *independent* path. The
external verifier phase is hidden during the agent phase, so the agent gets
no in-loop correctness signal. When the code has a subtle bug (wrong
regression axis, off-by-one in a percentile index, noise added in the wrong
order, a parsing/precision slip), the single self-consistent run looks
"correct" and the agent exits on a value that misses the reference by a few
percent (e.g. slope 2.5056 vs 2.5997; m=0.048 vs 0.050).

The existing ``CustomSelfVerifyProcessor`` fires a generic checklist about
file existence and format, but nothing directs the agent to *independently
recompute* a numeric result and reconcile the two. That gap is mechanical and
general (it recurs across distinct compute tasks with different inputs), so it
is addressed with a Control hook, not by embedding any task's answer.

Mechanism
---------
* ``on_before_tool`` — inspect each Bash command for a *numeric-computation
  signature*: a compiled/interpreted program being run AND/OR a
  numeric-analysis vocabulary (regression / bootstrap / percentile /
  monte-carlo / seeded RNG / curve fit …) together with a write to a
  ``result``/``output`` style file. Record the pending call id when matched.
* ``on_after_tool`` — when that matched call returns successfully, append a
  single, general cross-validation nudge to the tool result text. The nudge
  asks the agent to recompute the key numeric quantity with an independent
  implementation (a different language/library, or a hand check on a small
  subset) and to reconcile any discrepancy before finishing. It contains **no
  task-specific values, algorithms, or answers** — only a general verification
  strategy.

Fires at most once per task. Purely additive to a tool result string
(contract-safe: no message insertion, no system-prompt mutation). Scoped to a
*class* of tasks (numeric/scientific compute) — no task ids, no dataset
literals. Non-numeric tasks are never touched.
"""
from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Vocabulary that indicates a numerical / scientific computation is being run.
# Generic across languages and libraries; no task-specific tokens.
_NUMERIC_VOCAB_RE = re.compile(
    r"(?:"
    r"regress|least[\s_-]*squares|\bols\b|polyfit|curve[\s_-]*fit|lstsq"
    r"|bootstrap|resampl|percentile|confidence[\s_-]*interval|\bci[\s_]*(?:lower|upper)\b"
    r"|monte[\s_-]*carlo|mt19937|mersenne|random[\s_.]*seed|srand|mt_rand"
    r"|std(?:::)?mt19937|uniform_int_distribution|normal_distribution|numpy\.random"
    r"|\bmean\b|\bvariance\b|\bstddev\b|standard[\s_-]*deviation|\bslope\b|\bintercept\b"
    r"|eigen|integrat|simulat|\bfft\b|convolut|correlat"
    r")",
    re.IGNORECASE,
)

# A program actually being executed (compile+run or interpreter run) — the
# moment a numeric answer is produced. Kept generic across toolchains.
_RUN_SIGNATURE_RE = re.compile(
    r"(?:"
    r"\bg\+\+\b|\bgcc\b|\bclang\+?\+?\b|\bcargo\s+run\b|\bgo\s+run\b|\bjavac?\b"
    r"|\bpython3?\b|\bnode\b|\bRscript\b|\bjulia\b|\bmake\b"
    r"|\./[\w./-]+"  # running a local compiled binary
    r")",
    re.IGNORECASE,
)

# A write to a results/output artifact — the thing the verifier reads.
_RESULT_FILE_RE = re.compile(
    r"(?:>{1,2}\s*|/)[\w./-]*"
    r"(?:result|output|answer|solution|fit|analysis|metrics|report)"
    r"[\w./-]*\.(?:txt|csv|json|dat|out|tsv|yaml|yml)",
    re.IGNORECASE,
)

_CROSSCHECK_NUDGE = (
    "\n\n[numeric-verify] This looks like a numeric/scientific computation that"
    " produces a result the grader will check for exact-ish accuracy. A single"
    " self-consistent run is NOT proof of correctness — a subtle bug (wrong"
    " formula/axis, off-by-one percentile index, RNG draw order or seed misuse,"
    " parsing/precision slip) yields a clean-looking but wrong number.\n"
    "Before you finish, independently re-derive the key quantities and reconcile:\n"
    "  1. Recompute the same result a SECOND way — e.g. a short script in a"
    " different language/library (numpy/scipy/pandas/awk) or a hand check on a"
    " small subset — reading the SAME input.\n"
    "  2. Compare the two results digit-by-digit. If they disagree, do NOT pick"
    " one — debug until you understand which is right and fix the primary program.\n"
    "  3. Re-read the task for exact requirements: which variable is regressed on"
    " which, required seed/parameters and the exact order operations are applied,"
    " percentile index convention, rounding, and the exact output format/path.\n"
    "  4. Confirm determinism where a seed is specified: re-run and check the"
    " output is byte-identical.\n"
    "Only finish once both derivations agree and match every stated requirement."
)


class NumericCrossCheckNudgeProcessor(MultiHookProcessor):
    """Append a one-shot independent-recompute nudge on numeric compute tasks."""

    _singleton_group = "numeric_crosscheck_nudge"
    _order = 32  # right after CustomEditToolProcessor (_order=30); result-append only

    def __init__(self) -> None:
        self._fired = False
        self._pending_ids: set[str] = set()

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._pending_ids = set()
        yield event

    def _is_numeric_run(self, cmd: str) -> bool:
        if not cmd:
            return False
        has_vocab = bool(_NUMERIC_VOCAB_RE.search(cmd))
        has_run = bool(_RUN_SIGNATURE_RE.search(cmd))
        has_result = bool(_RESULT_FILE_RE.search(cmd))
        # Arm when the agent is running a program that either speaks numeric
        # vocabulary or writes a result artifact. Require an actual run so we
        # nudge at the point an answer is produced, not while editing source.
        return has_run and (has_vocab or has_result)

    async def on_before_tool(self, event: ToolCallEvent):
        if not self._fired and event.tool_name == "Bash":
            inp = event.tool_input if isinstance(event.tool_input, dict) else {}
            cmd = str(inp.get("command", "") or "")
            if self._is_numeric_run(cmd):
                self._pending_ids.add(event.tool_call_id)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        armed = event.tool_call_id in self._pending_ids
        self._pending_ids.discard(event.tool_call_id)
        if armed and not self._fired:
            self._fired = True
            yield dataclasses.replace(
                event, result=(event.result or "") + _CROSSCHECK_NUDGE
            )
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._pending_ids = set()
        yield event
