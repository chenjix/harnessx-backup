# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""EscalatingSelfVerifyProcessor.

Generalises the one-shot ``CustomSelfVerifyProcessor`` used by the TB2 /
Tmax harness. That processor injects a verification checklist exactly
once when the model tries to exit with no tool call, then stays silent
forever. Trajectories show a recurring failure shape it cannot recover:

* The agent runs the checklist, its own ``ls`` **confirms a required
  output is missing or is written under the wrong path/name**, the agent
  acknowledges the miss in prose, then exits anyway — often after
  rationalising a substitute path ("the task wants X but I'll leave Y").
* Because the one-shot nudge already fired, no further intervention
  arrives; the run ends with the required artifact absent → score 0.

This class fires the standard checklist on the first exit attempt, and
on any *subsequent* exit attempt (up to ``max_fires`` total) injects a
short, escalating reminder that targets exactly this loop: if a required
output is still missing or lives at the wrong path, create it at the
exact path now — do not exit with a known-missing artifact and do not
substitute a different name. After ``max_fires`` it goes silent so a
genuinely-finished agent is never trapped (bounded to avoid burning the
step / token budget).

Only the exit path is touched; nothing fires while the agent is still
issuing tool calls. The intervention is task-agnostic — it reacts to the
agent's own declared-done behaviour, not to any specific task's paths.
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

# First exit attempt: the full checklist (kept semantically identical to
# the stock processor so we do not regress its already-working behaviour).
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

5. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""

# Second (and later) exit attempts: the agent already saw the checklist
# yet is trying to leave again. Target the "detected the miss, exited
# anyway / renamed to a substitute" loop directly and concretely.
_SELF_VERIFY_ESCALATE = """\
You already ran the verification checklist and are trying to finish again. Before you exit, resolve this hard rule:

- If ANY output the task names is still MISSING, or exists only under a DIFFERENT path/name than the task specified, that is a task failure — an artifact at the wrong path scores zero no matter how correct its contents are.
- Do NOT rationalise a substitute path or filename because the required one is inconvenient (e.g. a naming clash, an import shadow, a permissions issue). Work around the obstacle and put a correct, working artifact at the EXACT path the task requires.
- Run one `ls -lh <exact required path>` for each required output right now. For every one that is missing or misplaced, issue a Bash command that creates/moves it to the exact path, then re-verify.

Only after every required output is confirmed at its exact path should you end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class EscalatingSelfVerifyProcessor(MultiHookProcessor):
    """Repeatable, escalating self-verification nudge on exit attempts.

    Drop-in replacement for ``CustomSelfVerifyProcessor``. Shares the same
    singleton group so only one self-verify mechanism is active.
    """

    _singleton_group = "tb2_self_verify"
    _order = 90

    def __init__(self, max_fires: int = 2) -> None:
        # Total number of times the exit-verify nudge may fire in one task.
        # 1 reproduces the stock one-shot behaviour; 2 adds a single
        # escalation. Bounded so a finished agent is never trapped.
        self.max_fires = max(1, int(max_fires))
        self._fires = 0
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._fires = 0
        self._pending_message = ""
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        # Last message is a synthetic tool result (role != user) → we append
        # exactly one user message, matching the stock processor's contract.
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and self._fires < self.max_fires:
            first = self._fires == 0
            self._fires += 1
            self._pending_message = _SELF_VERIFY_MSG if first else _SELF_VERIFY_ESCALATE
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
        self._fires = 0
        self._pending_message = ""
        yield event
