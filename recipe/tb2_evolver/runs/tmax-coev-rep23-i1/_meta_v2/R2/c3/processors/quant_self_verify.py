# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""QuantitativeSelfVerifyProcessor — a drop-in, stronger self-verify checklist.

Motivation (benchmark-agnostic)
-------------------------------
The stock ``CustomSelfVerifyProcessor`` injects a one-shot checklist when the
model tries to exit without tool calls. Its steps are strong on *existence*
("does each required file exist at its exact path?") and *method validity*
("did your test exercise real behaviour?"), but they contain **no step that
forces the model to confront the numeric/threshold acceptance criteria of the
task against the actual output its own test already produced.**

This is a recurring, cross-task failure mode: the agent runs a test that
*prints evidence of failure* — a peak value, a max size, a count, a latency,
an accuracy number that violates a stated bound — but then declares success by
looking only at the *final* state (e.g. "the directory is empty now") instead
of the *observed extremum during the run* (e.g. "the peak size exceeded the
threshold at some point"). The task's success criterion is quantitative and
was violated *in the model's own scrollback*, yet the checklist never asked it
to check.

Concretely: a task may require that some quantity stay under / over / equal to
a stated bound *at all times* or *on average* or *to N significant figures*.
The final snapshot can look clean while the required invariant was breached
transiently. A monitor/daemon/rate-limiter/quota task is the canonical case,
but the same gap applies to any task with a numeric acceptance target
(accuracy thresholds, byte/latency/count limits, tolerances).

What this processor changes
---------------------------
It is a **behaviour-preserving drop-in** for ``CustomSelfVerifyProcessor``:

* same ``_singleton_group`` ("tb2_self_verify") and ``_order`` (90), so it
  occupies the same pipeline slot and cannot double-fire alongside the stock
  one;
* same mechanics — fires **at most once per task**, only on a no-tool-call
  exit turn, via the same keepalive-tool + one-shot ``on_before_model`` user
  message pattern (strict +1 message contract);
* same existence / method-validity steps as the stock checklist, **plus** one
  new step that makes the model (a) restate every numeric acceptance criterion
  from the task in its own words, and (b) re-read the actual output its own
  test/run already produced and confirm each number satisfies its bound —
  including peaks/maxima/minima observed *during* the run, not just the final
  snapshot.

The added guidance is pure *strategy*: it names no task, no path, no constant,
no threshold value. It would help an agent on any unseen task whose success is
judged by a number.
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

5. **Confront the numbers.** List every *quantitative* acceptance criterion in the task in your own words — every threshold, limit, bound, count, size, timing, tolerance, or accuracy target, and whether it must hold *at all times*, *on average*, or only *at the end*. Then re-read the actual output your own test/run produced and check each number against its bound. Pay special attention to **peaks and extrema observed DURING the run**, not just the final snapshot: a value that transiently exceeded a "must stay under X" bound (or dipped below a "must stay above X" bound) is a FAILURE even if the final state looks clean. If any observed number violates its criterion, your solution is NOT done — diagnose and fix the root cause (timing/granularity, off-by-one, wrong units, race conditions) and re-test until the observed numbers satisfy every bound.

6. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class QuantitativeSelfVerifyProcessor(MultiHookProcessor):
    """Inject a one-shot verification prompt (with a quantitative-criteria step)
    when the model tries to exit without tool calls.

    Drop-in replacement for ``CustomSelfVerifyProcessor``: same singleton group,
    same order, same fire-once mechanics. Fires at most once per task run; on the
    next no-tool-call turn it stays silent.
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
