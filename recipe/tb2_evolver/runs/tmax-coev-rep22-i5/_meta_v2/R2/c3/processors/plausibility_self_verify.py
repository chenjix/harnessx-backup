# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""PlausibilitySelfVerifyProcessor.

An augmented drop-in replacement for the stock TB2 ``CustomSelfVerifyProcessor``.

Motivation (harness deficiency, not task knowledge)
---------------------------------------------------
The stock one-shot exit checklist makes the agent confirm that output
files *exist*, are *non-empty*, and are *syntactically* well formed. That
catches missing-file and wrong-path failures, but it is blind to the most
insidious data-processing failure mode: a silent parsing / type / units bug
that produces output which is well-formed and plausible-looking yet
numerically wrong. On such tasks the agent's own program "runs cleanly",
its self-check passes ("file exists, JSON valid, N windows"), and it exits
``done`` with reward 0.

The generalizable signal the stock checklist misses is *premise
consistency*: a task usually states, in its own framing, the qualitative
shape of the correct answer (e.g. "a batch was heavily corrupted",
"most rows are duplicates", "the service must reject malformed input").
When the agent's computed result contradicts that stated premise
(e.g. the task is premised on severe corruption but the agent detected
almost none), that mismatch is a strong red flag that an upstream parsing
or conversion assumption is wrong — and it is visible to the agent from
the task description alone, before the verifier ever runs.

This processor keeps the stock keep-alive / one-shot mechanics verbatim
(so the hook contract is unchanged) and only enriches the injected
checklist with two extra, fully task-agnostic reasoning steps that push
the agent to (a) restate what magnitude / shape of answer the task's
framing implies, and (b) re-examine its data-loading assumptions if the
computed result contradicts that framing. It hardcodes no task IDs, no
constants, no algorithms — every added instruction is a general
verification habit that helps on any unseen data-processing task.
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

_SELF_VERIFY_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** Does your solution address every requirement, including edge cases, accuracy thresholds, and exact output format?

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Inspect the actual file contents** — `cat` or `head` each output file and confirm the values are semantically correct, not just that the file exists or is non-empty.

4. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

5. **Premise consistency — the most common silent failure.** Restate, in one sentence, the qualitative shape the task's own framing implies the answer should have (e.g. how severe the described problem is, roughly how many records/segments/items it should affect, whether a value should be extreme or negligible). Now compare that expectation against your actual computed numbers. If your result CONTRADICTS the task's stated premise — the description implies a large/severe/dominant effect but you measured a tiny or negligible one, or vice versa — treat that as a strong signal that an upstream assumption is wrong. Do NOT rationalize the discrepancy away.

6. **If step 5 flagged a mismatch, re-examine your data-loading layer before anything else.** Silent bugs most often live in how raw bytes/records are decoded, not in the math on top: signed-vs-unsigned integers, endianness, off-by-one field offsets, header/row skipping, encoding, units, or column selection. Add a quick diagnostic (print a handful of raw decoded values and check they fall in a sane range) rather than re-reading the same values you already trust.

7. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class PlausibilitySelfVerifyProcessor(MultiHookProcessor):
    """Inject an enriched one-shot verification prompt when the model tries to exit.

    Behaviourally identical to the stock ``CustomSelfVerifyProcessor`` — fires
    at most once per task run, uses the same synthetic keep-alive tool call and
    the same ``on_before_model`` +1-user-message injection — but the injected
    checklist adds two task-agnostic steps that catch silent parsing/units bugs
    (premise-consistency + data-loading-layer re-examination).
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
            self._pending_message = _SELF_VERIFY_MSG
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
