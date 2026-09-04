# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""StrictSelfVerifyProcessor — a stronger drop-in for CustomSelfVerifyProcessor.

Closes a systemic failure mode observed across many ``no_tool_calls``/``done``
trajectories that still score 0: the agent commits with an implementation that
satisfies its own loose, prose-level reasoning but violates a *literal*
requirement stated in the task text (exact output format, exact count of
lines/rows, an explicit behavioural verb like "append" / "idempotent" /
"exactly N", a precise numeric/format spec). The existing self-verify checklist
asks the agent to "address every requirement", but it lets the agent *assert*
compliance in prose without ever constructing a concrete test that exercises the
specific literal claim.

Representative failures the generic checklist did not catch:
  * a deploy script told to *append* to a log used ``>`` (truncate); its own
    "idempotency" check saw one line after a second run and declared success —
    the harness expected the log to grow by one line per run.
  * a CSV task required values "rounded to 2 decimal places"; the agent emitted
    ``33.0`` instead of ``33.00`` and asserted the format was correct without
    diffing against the literal spec.

This processor keeps the one-shot, keepalive-driven mechanics of the original
CustomSelfVerifyProcessor unchanged (fires at most once, on the first no-tool
exit attempt) but injects a checklist that forces two things the generic
version did not:

  1. **Enumerate the task's literal requirements** — copy out each exact
     format / count / keyword / threshold verbatim from the task text.
  2. **Prove each one with a command whose output can be compared to the spec**
     — re-run idempotent/append/stateful operations the number of times the
     task implies and inspect the delta; byte-compare formatted output against
     the literal format; count lines/rows/fields against the exact number
     required. A single happy-path run is explicitly rejected as proof.

The message is deliberately domain-agnostic: it names *classes* of literal
requirement, never any task-specific value, path, or constant.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCall,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

_SELF_VERIFY_TOOL = "_tb2_self_verify"
_SELF_VERIFY_ACK = "Verification check initiated. See the message above for instructions."

_STRICT_SELF_VERIFY_MSG = """\
Before finishing, run this verification protocol. A solution that merely "ran without \
errors" is NOT verified — do every step, even if you think you already checked it.

STEP 1 — Enumerate the LITERAL requirements.
Re-read the task description and list, verbatim, every requirement that has an exact, \
checkable form. In particular copy out:
  - exact output FORMATS (field separators, quoting, decimal places, trailing zeros, \
whitespace, key order, header lines) — e.g. a spec that says "2 decimal places" means \
`33.00`, not `33.0`;
  - exact COUNTS ("exactly N lines/rows/files", "one line", "no duplicates");
  - explicit BEHAVIOURAL verbs and their precise meaning:
      * "append" means add to existing content (`>>`), NOT overwrite (`>`);
      * "idempotent" means running it again leaves persistent state unchanged — but \
side-effect logs/outputs may still be told to grow; do not conflate the two;
      * "exactly once", "each time", "in place", "atomic", "sorted", "deduplicated" \
each impose a testable condition;
  - exact THRESHOLDS, tolerances, and expected VALUES the task states or lets you derive.

STEP 2 — Confirm the output artifacts exist at their exact paths.
Run `ls -lh` on every required output path. A script exiting 0 does NOT prove a file \
was written.

STEP 3 — Prove EACH literal requirement from STEP 1 with a command whose output you \
compare against the spec. One happy-path run is NOT proof. Concretely:
  - For "append"/"idempotent"/stateful/"each time" behaviour: reset to a known state, \
run the operation the number of times the task implies (usually twice), and inspect \
the DELTA — which files grew, which stayed identical — and check that against what the \
task requires for BOTH the persistent state and the side-effect outputs.
  - For exact formats: `cat`/`head` the real output and byte-compare it to the literal \
format (count decimals, check separators and trailing zeros, verify header/quoting).
  - For exact counts: `wc -l` / count rows/fields and compare to the stated number.
  - For computed values: recompute the expected result independently (by hand or a tiny \
script) and compare — do not assume your program's output is the reference.

STEP 4 — Reject weak evidence.
Tests that only check importability, syntax, exit code 0, or file existence are NOT \
valid — they pass on broken implementations. Every claim of correctness must trace to a \
command output you compared to the spec in this session.

STEP 5 — For running services, confirm they are alive and reachable right now.

Fix anything that mismatches, then re-run the failing check. When every literal \
requirement in STEP 1 has a matching proof, end your final message with:
**SUCCESS: task complete. Verified requirements: [briefly list each literal requirement \
you proved and the check that proved it].**\
"""


class StrictSelfVerifyProcessor(MultiHookProcessor):
    """Inject a strict, literal-requirement verification prompt on the first
    attempt to exit without tool calls.

    Fires at most once per task run (same one-shot keepalive mechanics as the
    stock ``CustomSelfVerifyProcessor``); on any later no-tool-call turn it
    stays silent so it cannot block a genuine exit.
    """

    _singleton_group = "tb2_self_verify"
    _order = 90

    def __init__(self) -> None:
        self._verified = False
        self._pending_message: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._verified = False
        self._pending_message = ""
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        # last message is a tool result (role != user) -> append exactly +1 user
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._verified:
            self._verified = True
            self._pending_message = _STRICT_SELF_VERIFY_MSG
            keepalive = ToolCall(
                id=f"sv-{uuid.uuid4().hex[:8]}",
                name=_SELF_VERIFY_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _SELF_VERIFY_TOOL:
            yield dataclasses.replace(event, approved=False, synthetic_result=_SELF_VERIFY_ACK)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._verified = False
        self._pending_message = ""
        yield event
