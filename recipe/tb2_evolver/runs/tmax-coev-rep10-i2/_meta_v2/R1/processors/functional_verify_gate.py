# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""FunctionalVerifyGateProcessor.

A stateful exit gate that supersedes the single-shot self-verify nudge.

Observed failure mode (R0): 23 of 29 failing tasks exited with
``exit_reason=done`` — the agent believed it was finished. The existing
single-shot ``CustomSelfVerifyProcessor`` fires exactly once; the agent
answers it with a *shallow static re-check* (``ls`` the output file,
``grep`` for a keyword, ``cat`` the script) and immediately re-exits.
The verifier then fails the task on a semantic / functional bug that a
real end-to-end run would have surfaced (wrong numeric constant, a
daemon that never actually keeps disk under quota, a query that uses the
wrong index, etc.).

This processor keeps the good checklist content but makes the gate
*functional-aware*:

- When the agent tries to exit, it is nudged to verify.
- After a nudge, the processor watches whether the agent runs a
  *substantive* command (something that actually executes / exercises the
  solution) as opposed to purely read-only inspection (``ls`` / ``cat`` /
  ``grep`` / ``head`` / ``find`` / ``stat`` / ``wc`` / ``echo``).
- If the agent tries to exit again having only done read-only inspection,
  it is nudged once more with an escalated, functional-testing demand.
- The gate never blocks more than ``max_nudges`` times, so a task that is
  genuinely done (or genuinely stuck) always terminates — no infinite
  loop, bounded extra token cost.

Class of tasks served: every TB2 task whose correctness is checked by a
functional final-state test (i.e. essentially all of them). It carries no
task-specific literals.
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
)
from harnessx.core.processor import MultiHookProcessor

_TOOL = "_tb2_functional_verify"
_ACK = "Verification check initiated. See the message above for instructions."

# Commands that only *inspect* state — running one of these is not evidence
# that the agent actually exercised its solution end to end.
_READONLY_HEAD_RE = re.compile(
    r"^\s*(ls|cat|head|tail|grep|egrep|fgrep|find|stat|wc|echo|file|"
    r"pwd|which|type|env|printenv|tree|du|df|realpath|readlink|basename|dirname)\b"
)


def _is_substantive(command: str) -> bool:
    """True if the bash command plausibly *executes / exercises* the solution.

    Heuristic: a command is substantive unless every top-level segment
    (split on ``;``, ``&&``, ``||`` and newlines) begins with a read-only
    inspection verb. This treats ``python run.py`` / ``make`` / ``./daemon &``
    / ``bash test.sh`` / ``curl localhost`` as substantive, while
    ``ls -lh out.txt && cat out.txt`` stays shallow.
    """
    if not command or not command.strip():
        return False
    segments = re.split(r"(?:;|&&|\|\||\n)+", command)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if not _READONLY_HEAD_RE.match(seg):
            return True
    # All segments were read-only inspection (or there were none).
    return False


_NUDGE_1 = """\
Before finishing, VERIFY YOUR WORK FUNCTIONALLY — do not skip this even if you already glanced at the files.

1. **Re-read the task description now.** List every concrete requirement: exact output paths, exact numeric constants/thresholds, exact formats, accuracy criteria, and any runtime behavior (a service that must stay up, a monitor that must keep something bounded, a script that must produce a specific result).

2. **Actually RUN your solution end to end against the real scenario.** Static checks are NOT verification: confirming a file exists (`ls`), that it contains a keyword (`grep`), or that a script parses is worthless — those pass even on a broken solution. Instead execute the real workflow: run the program with the real inputs, drive the actual scenario the task describes, and observe what happens.

3. **Compare observed behavior to the required behavior**, value by value. If the task states an exact constant, threshold, or expected result, recompute it independently and check it matches — do not trust your own earlier output.

4. **For services/daemons:** start them and confirm they are alive AND behave correctly under the described load right now.

Run the real functional test now, then report the observed result. When — and only when — a genuine end-to-end run confirms correctness, end your final message with:
**SUCCESS: verified by running <describe the exact command/scenario you ran and the observed result>.**\
"""

_NUDGE_2 = """\
STOP — you tried to finish again but you only inspected files (ls/cat/grep). That is not a functional test and will not catch a wrong value or broken behavior.

You must EXECUTE the solution against the task's real scenario at least once before finishing:
- If it is a script/program: run it with the real inputs and read the actual output.
- If it is a fix: reproduce the original failing case and confirm it now passes.
- If it is a service/daemon/monitor: launch the described workload and confirm the required property holds while it runs.
- Recompute any required numeric constant or threshold independently and confirm the exact match.

Do the real run now. If a genuine run confirms correctness, finish with:
**SUCCESS: verified by running <exact command/scenario> — observed <result>.**\
"""


class FunctionalVerifyGateProcessor(MultiHookProcessor):
    """Stateful, functional-aware exit gate.

    Replaces the single-shot self-verify. Nudges the agent to run a real
    end-to-end test before exiting, and re-nudges (bounded) if the agent
    tries to exit again having only done read-only inspection.
    """

    _singleton_group = "tb2_self_verify"  # same slot as the old gate
    _order = 90

    def __init__(self, max_nudges: int = 2) -> None:
        self.max_nudges = max(1, int(max_nudges))
        self._nudges_used = 0
        self._substantive_since_nudge = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._nudges_used = 0
        self._substantive_since_nudge = False
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

    async def on_before_tool(self, event: ToolCallEvent):
        # Swallow our own keepalive tool; also observe real Bash usage.
        if event.tool_name == _TOOL:
            yield dataclasses.replace(event, approved=False, synthetic_result=_ACK)
            return
        if event.tool_name == "Bash":
            command = ""
            try:
                command = event.tool_input.get("command", "") or ""
            except AttributeError:
                command = ""
            if _is_substantive(command):
                self._substantive_since_nudge = True
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if not exit_intent:
            yield event
            return

        # First exit attempt: always nudge once.
        if self._nudges_used == 0:
            self._nudges_used = 1
            self._substantive_since_nudge = False
            self._pending_message = _NUDGE_1
            yield self._keepalive(event)
            return

        # Subsequent exit attempts: allow exit if the agent has actually
        # exercised the solution since the last nudge, or if we've hit the cap.
        if self._substantive_since_nudge or self._nudges_used >= self.max_nudges:
            yield event
            return

        # Agent tried to exit again but only did read-only inspection.
        self._nudges_used += 1
        self._substantive_since_nudge = False
        self._pending_message = _NUDGE_2
        yield self._keepalive(event)

    def _keepalive(self, event: ModelResponseEvent):
        keepalive = ToolCall(
            id=f"fv-{uuid.uuid4().hex[:8]}",
            name=_TOOL,
            input={},
        )
        return dataclasses.replace(event, tool_calls=(keepalive,))

    async def on_task_end(self, event: TaskEndEvent):
        self._nudges_used = 0
        self._substantive_since_nudge = False
        self._pending_message = ""
        yield event
