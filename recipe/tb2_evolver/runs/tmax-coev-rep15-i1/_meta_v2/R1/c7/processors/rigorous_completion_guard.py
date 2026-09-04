# SPDX-License-Identifier: MIT
"""RigorousCompletionGuard — a bounded pre-exit verification hook for tasks
that self-advertise a rigorous / hidden evaluation.

Motivation
----------
Two distinct R0 failures share one harness-actionable root cause: the agent
declares the task "done" after only a *shallow* self-check (a script that
ran, or a tiny sample that matched) and never re-derives the *exact* required
output contract (key names, output path, format, accuracy thresholds) from
the authoritative source in the task description.

- A software_engineering task whose description said the hidden test suite
  would run the script on "a massive, hidden dataset" against a ">= 0.98
  accuracy threshold": the agent fit its output-schema key names to the small
  sample it was given instead of the source schema, verified only that its
  script ran on the 3-line sample, and stopped. Final accuracy: 0.34.
- A system_administration task: the agent wrote its deliverable to a
  plausible-but-wrong path and finished without re-confirming the exact
  required path from the task text.

The existing ``CustomSelfVerifyProcessor`` fires a generic checklist exactly
once. This guard is *complementary and narrower*: it fires an extra, targeted
reminder **only** on tasks whose own description signals a rigorous/hidden
evaluation, and it is bounded (``max_fires``) so it can never cause an
exit-block spiral. It does not embed any task-specific literals — the trigger
is a set of generic evaluation-rigor cue phrases, and the injected guidance is
pure strategy (re-derive the contract from source; stress-test edge cases).

Mechanism mirrors the proven ``CustomSelfVerifyProcessor`` exit-intercept path:
on an exit-intent turn we replace the (empty) tool-call set with a synthetic
keepalive tool call, ack it with ``approved=False`` + ``synthetic_result`` so
no real command runs, and inject exactly one ``user`` message on the following
``on_before_model`` turn. Net message delta per fire: +1 user (contract-safe).
"""
from __future__ import annotations

import dataclasses
import re
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskStartEvent,
    TaskEndEvent,
    ToolCall,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

_GUARD_TOOL = "_tb2_rigor_guard"
_GUARD_ACK = "Rigor check initiated. See the message above and act on it before finishing."

# Generic cue phrases that indicate the task will be graded by a rigorous /
# hidden / accuracy-thresholded evaluation rather than a trivial existence
# check. These are evaluation-rigor vocabulary, NOT task-specific literals.
_RIGOR_CUES = re.compile(
    r"\b("
    r"hidden|held[- ]?out|massive|large[- ]?scale|edge[- ]?case|"
    r"accuracy|precision|threshold|robust|robustness|property[- ]?based|"
    r"stress|adversarial|corner[- ]?case|thoroughly|exhaustive|"
    r"automated test suite|test suite will|will run your|will run the|"
    r"grading|graded|scored against|golden|ground[- ]?truth"
    r")\b",
    re.IGNORECASE,
)

_RIGOR_MSG = """\
[RigorGuard] Your task description signals that a rigorous / hidden evaluation
will judge this work — not just whether files exist. Before you finish, do a
deeper pass than a quick sample check:

1. **Re-derive the exact output contract from the AUTHORITATIVE source, not from
   samples.** If the required key names, output path, field format, or mapping
   rules come from a spec (a file, an image/OCR text, a schema, or an explicit
   sentence in the task), extract them literally from THAT source. Do NOT infer
   names or paths from the sample input's own field names — samples show shape,
   the spec defines the contract. If your OCR / spec read was noisy, re-run it
   with different settings and reconcile before committing to names.

2. **Stress-test beyond the sample.** Generate several adversarial / edge-case
   inputs yourself (unusual encodings, empty/missing fields, boundary values,
   entries that should be skipped) and confirm your program produces the
   contractually-correct output for each. A pass on the tiny provided sample is
   NOT evidence of correctness against a large hidden dataset.

3. **Confirm every required deliverable is at its EXACT path/name** as written
   in the task (run `ls -lh` on each), and that its contents are semantically
   correct — not merely present.

Fix any mismatch now. Only then stop.\
"""


class RigorousCompletionGuard(MultiHookProcessor):
    """Bounded extra pre-exit verification nudge for rigor-advertised tasks."""

    _singleton_group = "tb2_rigor_completion_guard"
    _order = 91  # just after CustomSelfVerifyProcessor (_order=90)

    def __init__(self, max_fires: int = 2) -> None:
        self.max_fires = max(1, int(max_fires))
        self._fires = 0
        self._armed = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._fires = 0
        self._pending_message = ""
        desc = event.task_description or ""
        # Only arm the guard for tasks that self-advertise a rigorous eval.
        self._armed = bool(_RIGOR_CUES.search(desc))
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        # Last message is a tool result (role != user) → append exactly +1 user.
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if self._armed and exit_intent and self._fires < self.max_fires:
            self._fires += 1
            self._pending_message = _RIGOR_MSG
            keepalive = ToolCall(
                id=f"rg-{uuid.uuid4().hex[:8]}",
                name=_GUARD_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _GUARD_TOOL:
            yield dataclasses.replace(event, approved=False, synthetic_result=_GUARD_ACK)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fires = 0
        self._pending_message = ""
        self._armed = False
        yield event


__all__ = ["RigorousCompletionGuard"]
