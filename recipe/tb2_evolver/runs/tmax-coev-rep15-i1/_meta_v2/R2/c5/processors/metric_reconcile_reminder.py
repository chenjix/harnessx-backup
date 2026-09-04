# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""MetricReconcileReminderProcessor.

Closes a recurring "silent wrong-metric exit" failure mode observed in the
tmax / terminal-bench-2 evaluation. A large slice of the ``exit_reason=done``,
``finished=no_tool_calls``, ``reward=0`` cluster shares one mechanical shape:

  1. The task states a *quantitative* acceptance criterion for a value the
     agent must compute and write to an exact output file (a maximum deviation
     that must be small, a centroid/distance that must match a reference, a
     leaked-bytes count that must equal an expected number, an accuracy that
     must exceed a threshold, "match the analytical reference", etc.).
  2. The agent computes *a* value, writes it to the required file, and then —
     because the stock self-verify checklist is existence/format oriented —
     its final pass only re-confirms the file exists and the *format* looks
     right. It never reconciles the *actual number* against the task's stated
     acceptance bound.
  3. The agent exits. The verifier then asserts the value against the bound and
     the run scores 0 even though the agent frequently *already narrated doubt*
     ("the deviation is quite high", "the amplitude is growing — not correct",
     "the value changed between runs").

The stock ``CustomSelfVerifyProcessor`` fires in every one of these cases and
the agent rubber-stamps it. This processor supplies the missing discipline as a
*mechanical, one-shot* nudge delivered exactly at the exit decision point: when
the task text carries a numeric-acceptance-criterion framing AND the agent's own
Bash activity shows it wrote a computed value into an output/metric file, and it
then tries to finish, it is reminded to reconcile the reported number against
the stated bound before exiting — and, critically, NOT to exit if its own prior
analysis already flagged that the result looks wrong.

This is a *class* fix, not a task fix: it arms off generic
acceptance-criterion vocabulary in the task description and off the agent's own
metric-writing Bash activity, never off task identifiers. It is additive (new
singleton group), runs after the self-verify processor so the two exit-intent
hooks serialize, fires at most once per task, and always yields to a genuine
exit on the following turn — a finished run is never trapped.
"""

from __future__ import annotations

import dataclasses
import re
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


# Vocabulary in the TASK DESCRIPTION indicating a quantitative acceptance
# criterion: the graded deliverable is a number (or a small set of numbers)
# that must fall within a range / match a reference / equal an expected value /
# clear a threshold. Intentionally broad — a false positive only costs one
# cheap advisory on a genuine-exit turn; a false negative re-introduces the
# silent wrong-metric exit.
_CRITERION_RE = re.compile(
    r"""
      (?:\bmatch(?:es|ing|ed)?\b)          # "match the reference"
    | (?:\bdiverge)                         # "diverges and fails to match"
    | (?:\bwithin\b)                        # "within the expected range"
    | (?:\bdeviation\b|\bdiscrepanc)        # deviation / discrepancy
    | (?:\btoleranc)                        # tolerance
    | (?:\bthreshold\b)                     # threshold
    | (?:\baccuracy\b|\bprecision\b|\brecall\b|\bf1\b)
    | (?:\bexpected\s+(?:value|result|output|count|number))
    | (?:\bexact(?:ly)?\b)                  # exact match / exactly N
    | (?:\breference\s+(?:data|dataset|solution|output|value))
    | (?:\bmaximum\s+(?:absolute\s+)?(?:error|deviation))
    | (?:\bmean\s+(?:squared\s+)?error|\bmse\b|\brmse\b)
    | (?:\bmust\s+(?:equal|be\s+(?:equal|less|greater|within|below|above)))
    | (?:\bcorrect(?:ly)?\s+comput)         # "correctly computes"
    | (?:\bleak(?:ed|s)?\s+bytes|\bbytes\s+(?:lost|leaked))
    | (?:\bnumber\s+of\b.*\b(?:bytes|records|rows|lines|errors))
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Signals in the agent's own Bash commands that it computed a value and wrote it
# into an output / metric / report / log file (as opposed to only creating
# source code or data files). Broad on purpose for the same reason as above.
_METRIC_WRITE_RE = re.compile(
    r"""
      (?:>\s*\S*(?:validation|result|report|metric|output|deviation|error|
                    accuracy|score|leak|audit|summary)\S*
             \.(?:log|txt|json|csv|dat|out))   # redirect into a metric-ish file
    | (?:\.(?:log|txt|json|csv|dat|out)\s*['"]?\s*,\s*['"]?w) # open(...,'w')
    | (?:\bprintf\b.*>\s*\S+\.(?:log|txt|json|csv|dat|out))
    | (?:\becho\b.*>\s*\S+\.(?:log|txt|json|csv|dat|out))
    | (?:\bwith\s+open\()                       # python file write in a heredoc
    | (?:\bf\.write\()
    | (?:\btee\b\s+\S+\.(?:log|txt|json|csv|dat|out))  # tee into a metric file
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TOOL = "_metric_reconcile_reminder"
_ACK = "Metric-reconciliation check acknowledged. See the message above."

_MSG = """\
[MetricReconciliationCheck] This task states a *quantitative* acceptance \
criterion (a value that must match a reference, fall within a range, equal an \
expected number, or clear a threshold), and you have written a computed value \
into an output/metric file. File existence and correct FORMAT are not enough — \
the grader will assert the actual NUMBER against the stated bound.

Before you finish, reconcile the reported value against the task's own success \
criterion, not just its existence:

1. Restate the acceptance bound the task gives (e.g. "within (0.0, 0.1)", \
   "matches the reference", "equals the expected count"). Read your written \
   value back and check it actually satisfies that bound.
2. If your value plainly fails the bound, or if your own earlier analysis \
   already noted the result "looks wrong" / "is too high" / "keeps changing" / \
   "does not match" — DO NOT exit. That is the task's stated failure state, not \
   a passing state. Trace it to a root cause: is the computation/comparison \
   itself wrong (e.g. mismatched grids/keys, wrong formula, unstable tooling), \
   or is an upstream artifact still buggy? Fix the root cause and recompute.
3. Sanity-check the number independently — recompute it a second way, or on a \
   trivially known input — before trusting it.

Only declare done once the written value genuinely satisfies the task's stated \
criterion. If after honest reconciliation the value already satisfies the \
bound, finishing is correct."""


class MetricReconcileReminderProcessor(MultiHookProcessor):
    """One-shot exit-time nudge to reconcile a written metric against the bound.

    Arms only when (a) the task description carries numeric-acceptance-criterion
    vocabulary and (b) the agent's Bash activity shows it wrote a computed value
    into an output/metric file. Fires at most once per task, on an exit-intent
    turn that still has no tool calls, so it serializes cleanly behind any other
    exit-intent processor (self-verify, service-deps) — if another processor has
    already converted this exit attempt into a keepalive tool call, this one
    stays silent and fires on the next genuine exit attempt instead.
    """

    _singleton_group = "metric_reconcile_reminder"
    # Run after CustomSelfVerifyProcessor (_order=90) and the service-deps
    # reminder (_order=91) so the exit-intent hooks serialize rather than all
    # rewriting tool_calls on the same turn.
    _order = 93

    def __init__(self) -> None:
        self._criterion_task = False
        self._metric_written = False
        self._reminded = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._criterion_task = False
        self._metric_written = False
        self._reminded = False
        self._pending_message = ""
        text = self._task_text(event)
        if text and _CRITERION_RE.search(text):
            self._criterion_task = True
        yield event

    @staticmethod
    def _task_text(event: TaskStartEvent) -> str:
        # Be defensive about where the task description lives across versions.
        for attr in ("task_description", "description", "instruction", "prompt"):
            val = getattr(event, attr, None)
            if isinstance(val, str) and val.strip():
                return val
        task = getattr(event, "task", None)
        if task is not None:
            for attr in ("description", "instruction", "prompt", "text"):
                val = getattr(task, attr, None)
                if isinstance(val, str) and val.strip():
                    return val
        return ""

    async def on_before_tool(self, event: ToolCallEvent):
        # Absorb our own keepalive tool call so it never reaches the sandbox.
        if event.tool_name == _TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_ACK
            )
            return
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") or ""
            if _METRIC_WRITE_RE.search(cmd):
                self._metric_written = True
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if (
            exit_intent
            and self._criterion_task
            and self._metric_written
            and not self._reminded
        ):
            self._reminded = True
            self._pending_message = _MSG
            keepalive = ToolCall(
                id=f"mrr-{uuid.uuid4().hex[:8]}",
                name=_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._criterion_task = False
        self._metric_written = False
        self._reminded = False
        self._pending_message = ""
        yield event
