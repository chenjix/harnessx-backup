# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""SubstantiveVerifyGuard — a second-stage exit guard for TB2.

Problem this closes
-------------------
The existing ``CustomSelfVerifyProcessor`` injects a one-shot verification
checklist the first time the agent tries to exit without tool calls. A weak
model frequently *rubber-stamps* that checklist: it emits a single trivial
existence check (``ls -lh some_file`` / ``cat some_file``) and immediately
exits again, without ever re-exercising the task's real acceptance behaviour.

This is a recurring, mechanism-level shape in the "done / no_tool_calls /
reward=0" cluster:

- A monitor/daemon task where the agent verified only the *quiescent final
  state* of a dynamic system (all logs 0 bytes at the end) and never measured
  the *peak/transient* metric the grader actually checks.
- A "fix the build config" task where the agent claimed the fix landed but
  its verification turn only ``ls``-ed an unrelated output file, never
  re-reading the file whose content the requirement constrains.

In both, the self-verify checklist fired and was answered with a hollow
existence check, then the agent exited. The underlying bug was *observable*
had the agent actually re-run the acceptance scenario, but the one-shot
prose checklist did not force a substantive action.

What this processor does
-------------------------
It is a *second* exit gate that fires **at most once** per task, and **only
after** the base self-verify checklist has already fired:

1. It watches Bash commands. After the base ``_tb2_self_verify`` checklist
   has fired, it classifies each Bash command as either *substantive*
   (re-executes / re-inspects the solution: runs a script, test suite, build,
   interpreter, service probe, greps a config for a required token, diffs,
   etc.) or *trivial* (a bare existence/size peek: ``ls`` / ``stat`` / ``du``
   / ``echo`` with nothing else).
2. On the agent's *next* exit-without-tool-calls after the checklist, if the
   agent ran **no** substantive verification command in the meantime, it
   injects exactly one more, sharper message that demands the agent actually
   re-run the acceptance scenario and check the specific property the task
   constrains — then lets the agent exit on the following turn regardless.

It never terminates the run, never fires more than once, and stays silent
when the agent already did substantive verification (so tasks that verify
properly — including currently-passing ones — are untouched).

Ordering: registered with ``_order`` after ``CustomSelfVerifyProcessor``
(order 90) so it observes the base checklist's keepalive tool call.
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

# The base checklist tool injected by CustomSelfVerifyProcessor. Seeing a call
# to it is our signal that the first-stage verification prompt has fired.
_BASE_SELF_VERIFY_TOOL = "_tb2_self_verify"

_GUARD_TOOL = "_tb2_substantive_verify"
_GUARD_ACK = "Verification check initiated. See the message above for instructions."

_GUARD_MSG = """\
Your verification so far only listed or peeked at a file — that does NOT confirm the task is solved.

A file existing, or a script exiting 0, does not mean the requirement holds. Before you finish, you MUST actually re-exercise the acceptance behaviour and check the specific property the task constrains:

1. **Re-read the task and name the ONE property the grader will check** (an exact value, an accuracy/size threshold, a transient/peak behaviour during execution, a string that must or must NOT appear in a file, a service that must respond).

2. **Re-run the real scenario, not a proxy.** If the task is about behaviour *during* a run (a monitor, a daemon, a service under load, a size/time limit that must never be exceeded), start the scenario fresh and measure the metric *while it runs* — the final quiescent state can look fine even when the constraint was violated mid-run. If the task is about a file's content, `grep`/`diff` the file for the exact token or value the requirement names.

3. **Compare the measured result against the requirement.** If it does not match, your solution is wrong — fix it and re-run. Do not restate that it "works".

Run the real check now. Only after the measured result matches the requirement should you finish.\
"""

# Commands that count as *substantive* verification: they re-execute or
# re-inspect the solution against the requirement rather than merely peeking
# at file existence/size. Matched on any whitespace-delimited token so that
# leading `sudo`, env-var prefixes, or pipelines still count.
_SUBSTANTIVE_TOKENS = (
    "python", "python3", "pytest", "py.test", "make", "cmake", "bash", "sh",
    "node", "npm", "pnpm", "yarn", "go", "cargo", "gcc", "g++", "clang",
    "pip", "curl", "wget", "grep", "egrep", "rg", "diff", "cmp", "awk",
    "sqlite3", "psql", "mysql", "ps", "top", "kill", "nc", "http", "java",
    "javac", "ruby", "perl", "docker", "systemctl", "service",
)
# A leading `./something` or `/abs/path/script` invocation is also substantive.
_INVOKE_RE = re.compile(r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:\./|/)\S+")


def _has_substantive_verification(cmd: str) -> bool:
    """True if the command re-runs or re-inspects the solution meaningfully.

    Conservative by design: a bare `ls`/`cat`/`stat`/`du`/`echo`/`head`/`tail`/
    `wc`/`file`/`pwd` is NOT substantive; anything that invokes an interpreter,
    a test/build tool, a service probe, a script by path, or a content search
    counts. When in doubt we treat it as substantive so we stay silent (the
    guard only fires when verification was *clearly* hollow).
    """
    if not cmd or not cmd.strip():
        return False
    lowered = cmd.strip()
    # Explicit script invocation by path (./run.sh, /home/user/x.py, etc.)
    if _INVOKE_RE.search(lowered):
        return True
    tokens = re.split(r"[\s;&|]+", lowered)
    for tok in tokens:
        base = tok.rsplit("/", 1)[-1].lower()
        if base in _SUBSTANTIVE_TOKENS:
            return True
    return False


class SubstantiveVerifyGuard(MultiHookProcessor):
    """Fires one extra, sharper verification prompt when the base self-verify
    checklist was answered with only a trivial existence check.

    At most one extra prompt per task; only after the base checklist fired;
    silent when the agent already verified substantively.
    """

    _singleton_group = "tb2_substantive_verify"
    _order = 95  # after CustomSelfVerifyProcessor (order 90)

    def __init__(self) -> None:
        self._base_checklist_fired = False
        self._substantive_since_checklist = False
        self._guard_fired = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._base_checklist_fired = False
        self._substantive_since_checklist = False
        self._guard_fired = False
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
        # Detect the base checklist firing.
        if event.tool_name == _BASE_SELF_VERIFY_TOOL:
            self._base_checklist_fired = True
            self._substantive_since_checklist = False
        # Intercept our own keepalive tool so it produces no real side effect.
        elif event.tool_name == _GUARD_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_GUARD_ACK
            )
            return
        # Track substantive verification AFTER the base checklist has fired.
        elif event.tool_name == "Bash" and self._base_checklist_fired:
            cmd = event.tool_input.get("command", "") if event.tool_input else ""
            if _has_substantive_verification(cmd):
                self._substantive_since_checklist = True
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        should_fire = (
            exit_intent
            and self._base_checklist_fired
            and not self._substantive_since_checklist
            and not self._guard_fired
        )
        if should_fire:
            self._guard_fired = True
            self._pending_message = _GUARD_MSG
            # A keepalive tool call prevents the immediate exit; it is
            # intercepted in on_before_tool and produces no real side effect.
            keepalive = ToolCall(
                id=f"sv2-{uuid.uuid4().hex[:8]}",
                name=_GUARD_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._base_checklist_fired = False
        self._substantive_since_checklist = False
        self._guard_fired = False
        self._pending_message = ""
        yield event
