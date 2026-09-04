# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Strengthened one-shot self-verification for TB2-style tasks.

This is a drop-in replacement for the stock ``CustomSelfVerifyProcessor``.
It keeps the exact same fire-once exit-intent mechanism (inject a single
user message on the first no-tool-call turn, then stay silent) so the
message-contract behaviour is identical, but broadens the checklist to
cover two verification blind spots observed across TB2 trajectories:

1. **Runtime / state invariants.** Many tasks grade on a condition that
   must hold *during* or *after* execution (a size/latency threshold, a
   process being gone, a state transition), not merely on a file
   existing or a script exiting 0. Agents routinely declare success
   after a script "ran without error" while their own test output shows
   the invariant being violated. The checklist now forces the agent to
   restate the task's success condition in measurable terms and confirm
   its own end-to-end test actually observed that condition holding.

2. **Stray helper processes.** When an agent spawns background processes
   or services to test its solution, leaving them running can itself
   fail the grader (lingering-process checks) or mask a broken solution.
   The checklist now forces an explicit accounting of any process the
   agent started.

The guidance is intentionally task-agnostic: it describes *how* to
verify a class of correctness conditions, embedding no task IDs,
constants, paths, or algorithms.
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

_SELF_VERIFY_TOOL = "_tb2_invariant_self_verify"
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

4. **State the task's success condition as a measurable check, then prove it holds.** If the task defines any runtime or end-state invariant — a threshold or limit that must never be exceeded, a bound on size/time/memory, a process that must be gone, a state that must have changed — a script merely "running without error" is NOT proof. Re-run your solution end to end and read your own test output: does the observed value actually satisfy the stated bound? If your own output shows the value drifting past the limit, growing without bound, or the condition never being reached, the task is NOT solved — fix the mechanism, do not exit.

5. **Validate your verification method.** Did your test exercise the real behavior under realistic load, not a trivial or empty case? A test that checks only syntax, importability, or exit code 0 can pass on a broken implementation and is NOT valid evidence.

6. **Account for every process you started.** List any background jobs, daemons, or services you launched while testing (`ps aux` / `jobs`). If the task requires a service to keep running, confirm it is still alive now. Otherwise, terminate helper/test processes you spawned so they do not linger into the final state — lingering processes you started can themselves fail the check.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class InvariantSelfVerifyProcessor(MultiHookProcessor):
    """Inject a one-shot, invariant-aware verification prompt on exit intent.

    Fires at most once per task run. On the next no-tool-call turn it stays
    silent so the agent can actually finish.
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
