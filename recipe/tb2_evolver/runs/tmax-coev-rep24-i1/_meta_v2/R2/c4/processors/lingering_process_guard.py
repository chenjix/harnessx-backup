"""Lingering-background-process exit guard for TB2 / tmax.

Root-cause motivation (harness deficiency, not domain knowledge)
----------------------------------------------------------------
A recurring TB2 ``system_administration`` failure mode: the graded invariant
is the *state of the process table after the agent exits* (e.g. "no lingering
service processes must be running", "the daemon must still be answering").
The agent's very last actions launch a background process (a service, a
supervisor script, a pipeline that starts a daemon) and then it declares
success and exits — leaving processes it started running (or, symmetrically,
having just killed the daemon the task wanted alive) without ever inspecting
the live process table to reconcile it against the task's required end state.

The existing one-shot self-verify checklist *advises* cleanup, but the model
routinely re-runs its end-to-end workflow as its final act (spawning yet more
background processes) and then quits — so the checklist text alone does not
close the gap: the unclean process table is created *after* the checklist
fired, on a later exit turn.

This is a mechanical exit-gate gap that recurs across a whole *class* of
lifecycle/daemon/process-cleanup tasks, so it belongs in a Control hook, not
in the system prompt as task-specific knowledge. The guard:

* is **non-destructive** — it never kills anything itself (which process
  should linger vs. be reaped is task-dependent: some tasks want the daemon
  alive for the verifier, others want it gone). It injects one focused nudge
  and lets the agent reconcile the table against the task's stated end state.
* is **one-shot** — fires at most once per task, and only on an exit turn.
* is **narrowly triggered** — only when the session actually started
  background processes AND the agent's most recent shell command was itself a
  background-process launch (the strong signal that it exited immediately
  after starting a service instead of reconciling the table).

Contains no task IDs, paths, thresholds, or identifiers lifted from any
trajectory; the trigger is a generic shell-shape heuristic.
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

_GUARD_TOOL = "_tb2_lingering_process_guard"
_GUARD_ACK = "Process-table reconciliation check initiated. See the message above for instructions."

# Heuristic: does this shell command launch a background / detached process?
# - a trailing "&" that is not "&&" (job backgrounding)
# - nohup / setsid / disown / start-stop-daemon / systemctl start / service ... start
# - running a supervisor/launcher script whose job is to start a service
_BG_LAUNCH_RE = re.compile(
    r"""
    (?:                      # any of:
        &\s*$                       |  # trailing job-control ampersand
        &\s*(?:\#|\n)               |  # trailing & before comment/newline
        \bnohup\b                   |
        \bsetsid\b                  |
        \bdisown\b                  |
        \bstart-stop-daemon\b       |
        \bsystemctl\s+start\b       |
        \bservice\s+\S+\s+start\b
    )
    """,
    re.VERBOSE | re.MULTILINE,
)

# Cheap guard against false positives: "&&" is command chaining, not
# backgrounding. We strip "&&" before scanning for a lone trailing "&".
_AND_CHAIN_RE = re.compile(r"&&")


def _starts_background(cmd: str) -> bool:
    if not cmd:
        return False
    sanitized = _AND_CHAIN_RE.sub("  ", cmd)
    return bool(_BG_LAUNCH_RE.search(sanitized))


_GUARD_MSG = """\
Before you finish, reconcile the **live process table** against the end state this task requires — the external verifier grades the process table as it finds it *after you exit*, not the console output you saw earlier.

Your recent actions started one or more background processes. Do NOT exit yet. Run through this:

1. **List what is actually running now:**
```bash
ps -ef | grep -v grep | grep -Ei '<the service/daemon/process name(s) this task involves>'
```
Enumerate every matching PID, including duplicates left over from earlier test runs — re-running a start script multiple times spawns a *new* background process each time, and a kill that only targets the latest saved PID leaves the earlier ones lingering.

2. **Decide the required end state from the task description:**
   - If the task requires that **no such process is left running** (cleanup / graceful-shutdown / "no lingering processes" style), terminate *every* matching PID (e.g. `pkill -f <name>` then re-check), not just the one whose PID you saved. Confirm the list is empty afterward.
   - If the task requires the service to **stay alive / keep answering**, confirm it is still up right now (one live PID, still responding on its port) and that you did not accidentally kill it.

3. **Re-check after acting** — run the same `ps` (or a port/health probe) again and confirm the process table matches the required end state exactly before you exit.

Fix the process table now, then finish. When it matches the required end state, end your final message confirming the reconciled process state.\
"""


class LingeringProcessGuard(MultiHookProcessor):
    """One-shot, non-destructive exit gate for process-table end-state tasks.

    Fires at most once per task, on an exit turn (finish_reason in
    {end_turn, stop}, no tool calls), and only when the session started a
    background process and the agent's most recent shell command was itself a
    background launch. Injects a keepalive tool call + a focused nudge to
    reconcile the live process table against the task's required end state.
    """

    _order = 92  # after CustomSelfVerify / BehavioralSelfVerify (order 90)

    def __init__(self) -> None:
        self._fired = False
        self._bg_started = False
        self._last_cmd_was_bg = False
        self._pending_message: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._bg_started = False
        self._last_cmd_was_bg = False
        self._pending_message = ""
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _GUARD_TOOL:
            # Intercept our own keepalive call: never actually execute it,
            # inject the ACK so the loop continues into on_before_model.
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_GUARD_ACK
            )
            return
        if event.tool_name == "Bash":
            cmd = ""
            try:
                cmd = event.tool_input.get("command", "") or ""
            except AttributeError:
                cmd = ""
            is_bg = _starts_background(cmd)
            self._last_cmd_was_bg = is_bg
            if is_bg:
                self._bg_started = True
        else:
            # Any other real tool call means the exit is no longer immediately
            # preceded by a background launch.
            self._last_cmd_was_bg = False
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
        should_fire = (
            exit_intent
            and not self._fired
            and self._bg_started
            and self._last_cmd_was_bg
        )
        if should_fire:
            self._fired = True
            self._pending_message = _GUARD_MSG
            keepalive = ToolCall(
                id=f"lpg-{uuid.uuid4().hex[:8]}",
                name=_GUARD_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._bg_started = False
        self._last_cmd_was_bg = False
        self._pending_message = ""
        yield event
