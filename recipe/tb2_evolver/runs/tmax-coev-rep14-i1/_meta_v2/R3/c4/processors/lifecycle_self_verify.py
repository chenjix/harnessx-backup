# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LifecycleSelfVerifyProcessor — a drop-in replacement for
``benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor``.

Mechanically identical to the stock self-verify processor: on a genuine
no-tool-call exit intent it fires exactly once, emitting a synthetic
keepalive tool call (so the run loop does not terminate) and appending a
single user-role checklist message on the next model turn. Same singleton
group, same order, same +1-user message contract.

The ONLY behavioural difference is checklist item 5. The stock checklist is
one-sided about service lifecycle — it says "for running services, confirm
they are still ALIVE and reachable" — which is the wrong polarity for any
task whose required end state is that a service / background process be
STOPPED (clean shutdown, no lingering processes, resources released). That
wording actively nudges the agent away from cleanup on teardown-polarity
tasks. This replacement makes the item polarity-neutral: reconcile the
final process/service state against whichever end state the task specifies
(persist OR tear down), and if the task requires a stopped/clean state,
confirm no test-run processes were left behind (a self-run test harness
often leaves the very process the grader inspects).

No task ids, paths, service names, or other task-specific literals are
embedded — the checklist is entirely task-agnostic.
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

5. **Reconcile the final process/service state with what the task requires.** Decide the required end state from the task text, then confirm it holds right now — not what was true earlier:
   - If a service/process must keep RUNNING, confirm it is still alive and reachable now.
   - If the task requires a clean/stopped end state (graceful shutdown, teardown, "no lingering processes", resources released), confirm it is actually gone now — e.g. `ps`/`pgrep` for the process by name and verify nothing remains. Any process you started while TESTING (including from a self-run pipeline or test script) still counts: reap it if the required end state is "stopped", because the grader inspects the final live state, not your test logs.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class LifecycleSelfVerifyProcessor(MultiHookProcessor):
    """Inject a one-shot verification prompt when the model tries to exit without tool calls.

    Fires at most once per task run. On the next no-tool-call turn it stays silent.
    Behaviourally identical to ``CustomSelfVerifyProcessor`` except the checklist
    item on service/process state is polarity-neutral (persist OR teardown).
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
