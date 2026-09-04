# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Correctness-focused self-verification for TB2 exit-intent turns.

Replaces the stock ``CustomSelfVerifyProcessor`` (same singleton group
``tb2_self_verify``) with a two-barrier verification protocol.

Motivation
----------
The stock self-verify fires a single existence/format checklist and the
model reliably rubber-stamps it: it re-lists the required files, declares
"format matches", and exits — while the *computed value* is wrong. This is
the dominant shape of the ``exit_reason=done, reward=0`` failure cluster
(agent thinks it finished; the verifier's numeric/threshold/adversarial
check disagrees).

Two additive mechanisms, both general (no task literals):

1. **Correctness barrier (phase 1).** On the first exit-intent turn, inject
   a checklist that forces an *adversarial* correctness audit rather than an
   existence audit: recompute any reported metric a second, independent way;
   reconcile it against the task's own stated success criterion (a threshold,
   "matches the reference", a clean final state); and test on an
   edge/adversarial input, not just the happy path.

2. **Anti-rubber-stamp barrier (phase 2).** If the model tries to exit again
   *without having run any new tool call since the audit was requested* — i.e.
   it merely re-narrated "everything looks correct" — fire a single, terminal
   nudge insisting it actually execute an independent cross-check command
   before exiting. Bounded: at most one phase-2 fire per task, and after that
   the model is free to exit so we never trap a genuinely-finished run.

The processor never blocks exit unconditionally and never inspects task
content, so it is safe across the whole benchmark and adds at most two extra
turns to any single run.
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
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

_SELF_VERIFY_TOOL = "_tb2_self_verify"
_SELF_VERIFY_ACK = "Verification check initiated. See the message above for instructions."

_CORRECTNESS_MSG = """\
Before finishing, run this verification protocol. A file that exists, a script that exited 0, and output whose FORMAT looks right do NOT mean the task is solved — the grader checks the VALUES and behaviour. Do not skip a step even if you believe you already checked it.

1. **Re-read the task description now** and write down its explicit success criterion. Many tasks state one directly: a numeric threshold (e.g. "error must be below X", "accuracy at least Y"), "the output must MATCH the reference", an exact expected string, or a required final system state (no leftover processes, a file decryptable, a service still reachable). If your result plainly contradicts that criterion — a "match" that is far off, an accuracy well under the bar, a value outside the stated range — your solution is wrong even if every file exists. Treat that as a bug to FIX, not a result to report.

2. **Independently re-derive any reported number.** If you computed a metric (deviation, accuracy, distance, count, hash), recompute it a *different* way (a second short script, a spot-check by hand on one data point, or a different tool) and confirm the two agree. A value produced by exactly one code path you also wrote is unverified.

3. **Test the real behaviour on an adversarial / edge input, not the happy path.** If the task is "detect X", feed it a crafted positive AND a crafted negative and confirm BOTH verdicts. A test that only exercises one benign case can pass on a broken implementation.

4. **Check every required output file exists at its exact path and inspect its contents:**
```bash
ls -lh /path/to/each/required/output/file && cat /path/to/each/required/output/file
```

5. **For running services / background processes:** confirm they are alive and reachable right now, and confirm no processes that should have been cleaned up are still running (`pgrep -f <name>`).

Fix anything that looks wrong before exiting. When every check genuinely passes, end your final message with:
**SUCCESS: task complete. Verified criterion: <state the criterion and the value you confirmed satisfies it>.**\
"""

_RECHECK_MSG = """\
You are trying to finish, but since the verification request above you have not run any new command that independently re-checks your result — you only re-narrated that it looks correct. Re-narration is not verification.

Run ONE concrete cross-check command now before finishing:
- recompute the key metric a second, independent way and compare, OR
- run the solution against a crafted edge/adversarial input and confirm the verdict, OR
- reconcile your output against the task's stated success criterion (threshold / expected value / required final state).

If that command confirms the result, you may finish. If it reveals a discrepancy, fix it first.\
"""


class CorrectnessSelfVerifyProcessor(MultiHookProcessor):
    """Two-barrier, correctness-focused replacement for the stock self-verify.

    Barrier 1 (``_CORRECTNESS_MSG``): fired once on the first exit-intent turn.
    Barrier 2 (``_RECHECK_MSG``): fired at most once, only when the model tries
    to exit again without having executed any *new* real tool call since
    barrier 1 (i.e. it rubber-stamped and re-narrated). After barrier 2 the
    model may exit freely — we never trap a run.
    """

    _singleton_group = "tb2_self_verify"
    _order = 90

    def __init__(self) -> None:
        self._phase1_done = False
        self._phase2_done = False
        # counts real tool calls (anything but the keepalive self-verify tool)
        # that the model has issued since barrier 1 was raised.
        self._real_calls_since_phase1 = 0
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._phase1_done = False
        self._phase2_done = False
        self._real_calls_since_phase1 = 0
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

        if not exit_intent:
            # Real work turn. If it issued genuine tool calls after barrier 1,
            # count them so barrier 2 knows the model actually re-checked.
            if self._phase1_done and event.tool_calls:
                real = [tc for tc in event.tool_calls if tc.name != _SELF_VERIFY_TOOL]
                self._real_calls_since_phase1 += len(real)
            yield event
            return

        # exit_intent == True
        if not self._phase1_done:
            self._phase1_done = True
            self._real_calls_since_phase1 = 0
            self._pending_message = _CORRECTNESS_MSG
            keepalive = ToolCall(
                id=f"sv-{uuid.uuid4().hex[:8]}",
                name=_SELF_VERIFY_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
            return

        # Already did phase 1. If the model re-narrated without running any new
        # command, fire the terminal re-check nudge exactly once.
        if not self._phase2_done and self._real_calls_since_phase1 == 0:
            self._phase2_done = True
            self._pending_message = _RECHECK_MSG
            keepalive = ToolCall(
                id=f"sv-{uuid.uuid4().hex[:8]}",
                name=_SELF_VERIFY_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
            return

        # Verified path exhausted (or the model already ran a fresh check).
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _SELF_VERIFY_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_SELF_VERIFY_ACK
            )
        else:
            yield event

    async def on_after_tool(self, event: ToolResultEvent):
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._phase1_done = False
        self._phase2_done = False
        self._real_calls_since_phase1 = 0
        self._pending_message = ""
        yield event
