# SPDX-License-Identifier: MIT
"""OutputContractVerifyProcessor.

A drop-in replacement for the TB2 ``CustomSelfVerifyProcessor`` that keeps the
exact same firing mechanism (one-shot keepalive tool-call injected when the
model tries to exit with no tool calls, followed by a single appended user
message) but strengthens the checklist the model receives.

Motivation
----------
The stock self-verify checklist tells the agent to "inspect contents" and
"confirm values are semantically correct", but on several tasks the agent
finished ``done``/``no_tool_calls`` fully confident and still failed on
*exact output-contract* mismatches that eyeballing does not catch:

  - an off-by-one in a computed aggregate (a boundary/self-inclusion slip),
  - a delimited file emitted with a slightly different field-quoting or
    separator convention than the spec required.

Both are format / value-contract errors: the produced file *looks* plausible
but does not match the byte-exact contract stated in the task. The generic
"does it look right" prompt is too weak to surface them.

This processor adds two general verification disciplines to the checklist:

  1. **Exact-format audit** — re-read the required output format literally and
     confirm the produced bytes match it token-for-token (delimiters, quoting,
     headers/no-headers, trailing newline, exact string forms).
  2. **Independent re-derivation** — recompute at least one expected value by a
     *different* method (hand-trace a small sub-case, an alternative query,
     a manual count) and cross-check it against the produced output, rather
     than trusting the primary method that generated it.

Both are task-agnostic strategies: they help any output-producing task, and
carry no task-specific constants, paths, or identifiers.

Contract
--------
Identical to the class it replaces: at most one appended user message
(role="user") per task run, injected on the turn following the exit attempt;
a synthetic keepalive tool call keeps the loop alive so the message can be
delivered. No system-prompt mutation, no message removal, +1 message max.
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

_VERIFY_TOOL = "_tb2_self_verify"
_VERIFY_ACK = "Verification check initiated. See the message above for instructions."

_VERIFY_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** Does your solution address every requirement, including edge cases, accuracy thresholds, and the EXACT output format?

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Audit the output format literally, byte-for-byte.** Re-read the format the task specifies and compare it character-by-character to what you actually produced (`cat`/`head`/`hexdump` the file). Confirm the exact delimiter, quoting/no-quoting of fields, presence or absence of a header row, column order, whitespace, and trailing newline. A file whose values are right but whose format differs (e.g. quoted vs. bare fields, wrong separator, extra header) is a hard failure.

4. **Independently re-derive at least one expected value.** Do not trust the single method that generated the output. Recompute one result a *different* way — hand-trace a small sub-case, run an alternative query/command, or count manually — and confirm it matches. This is the only reliable way to catch off-by-one and boundary errors (e.g. whether an entity should count itself, inclusive vs. exclusive ranges, `>=` vs. `>`).

5. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

6. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class OutputContractVerifyProcessor(MultiHookProcessor):
    """Inject a strengthened one-shot verification prompt on voluntary exit.

    Fires at most once per task run. Mirrors the TB2 self-verify mechanism
    (keepalive synthetic tool call + single appended user message) but with a
    checklist that specifically targets exact-format and value-contract
    verification.
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
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._verified:
            self._verified = True
            self._pending_message = _VERIFY_MSG
            keepalive = ToolCall(
                id=f"sv-{uuid.uuid4().hex[:8]}",
                name=_VERIFY_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _VERIFY_TOOL:
            yield dataclasses.replace(event, approved=False, synthetic_result=_VERIFY_ACK)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._verified = False
        self._pending_message = ""
        yield event
