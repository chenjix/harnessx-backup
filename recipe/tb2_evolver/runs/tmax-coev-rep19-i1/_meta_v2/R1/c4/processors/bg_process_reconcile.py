# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""BackgroundProcessReconcileProcessor.

A TB2 Control processor that closes a structural failure mode of the
benchmark: the task container stays alive for an *external verifier phase*
that runs after the agent's session ends. Any process the agent spawned in
the background (``&``, ``nohup``, ``setsid``, ``disown``, ``Popen``,
``systemctl start``, ``service ... start``, ``start-stop-daemon`` …) is still
alive when the verifier inspects final state.

Two symmetric failure shapes result:

1. The task expects the service to be **cleaned up** at the end (e.g. a
   CI/CD pipeline that must gracefully shut the service down), but the agent
   ran the pipeline once *for its own testing*, orphaned that process, and
   left it running -> a "no lingering processes" assertion fails on the
   agent's own leftover test process.
2. The task expects the service to stay **alive**, but the agent killed it
   in a cleanup step -> a "process is running" assertion fails.

This processor does not know which shape a given task wants — that is task
knowledge the agent must supply. What it *does* know structurally is that
background processes survive into the verifier phase, so the moment the agent
tries to finish it injects a one-shot reminder to reconcile the *current*
process state with what the task requires. The reminder is generic (no task
IDs, paths, or process names) and fires at most once per task, only when the
session actually spawned a background process.
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

_KEEPALIVE_TOOL = "_tb2_bg_reconcile"
_KEEPALIVE_ACK = "Background-process reconciliation reminder delivered. See the message above."

# Patterns that indicate a command spawned a detached / long-lived process
# whose lifetime crosses the agent-exit boundary. Kept deliberately broad but
# anchored on real backgrounding syntax so plain foreground commands (which
# cannot linger) do not trip it.
_BG_PATTERNS = (
    re.compile(r"&\s*$"),                       # trailing & (backgrounding)
    re.compile(r"&\s*(?:#|$)", re.MULTILINE),    # trailing & on any line
    re.compile(r"\bnohup\b"),
    re.compile(r"\bsetsid\b"),
    re.compile(r"\bdisown\b"),
    re.compile(r"\bstart-stop-daemon\b"),
    re.compile(r"\bsystemctl\s+start\b"),
    re.compile(r"\bservice\s+\S+\s+start\b"),
    re.compile(r"subprocess\.Popen"),
    re.compile(r"\bPopen\("),
)

# Backgrounding a command is common and legitimate; a bare `&&` (logical AND)
# or `2>&1` (fd dup) must NOT be treated as backgrounding. Strip those before
# scanning for a trailing single `&`.
_LOGICAL_AND = re.compile(r"&&")
_FD_DUP = re.compile(r"\d*>&\d*|&>")

_RECONCILE_MSG = (
    "[BackgroundProcessCheck] During this session you started one or more "
    "background / detached processes. This container stays alive after you "
    "finish, and an external verifier inspects the FINAL process state — any "
    "process you left running (including ones you started only to test your "
    "own solution) is still visible to it.\n\n"
    "Before finishing, reconcile the current process state with what the task "
    "actually requires:\n"
    "  - Run `ps -ef` (or `pgrep -af <name>`) to see exactly what is still "
    "running right now.\n"
    "  - If the task requires the service to keep running, confirm exactly the "
    "intended process(es) are alive and nothing crashed.\n"
    "  - If the task's own workflow is supposed to stop/clean up the service "
    "(e.g. a pipeline that shuts it down at the end), make sure NO stray copies "
    "from your test runs are left behind — a single leftover test process will "
    "fail a 'no lingering processes' check. Re-running a start script can "
    "orphan the previous instance and overwrite its recorded PID, so verify by "
    "process name, not just by the last PID file.\n"
    "Match the final state to the task requirement, then finish."
)


def _has_backgrounding(cmd: str) -> bool:
    if not cmd:
        return False
    # Remove logical-AND and fd-dup tokens so they don't masquerade as `&`.
    scrubbed = _FD_DUP.sub(" ", _LOGICAL_AND.sub(" ", cmd))
    for pat in _BG_PATTERNS:
        if pat.search(scrubbed):
            return True
    return False


class BackgroundProcessReconcileProcessor(MultiHookProcessor):
    """Inject a one-shot 'reconcile final process state' reminder at exit
    intent, but only when the session actually spawned a background process.

    Ordered to run *after* the self-verify keepalive (order 90) so its
    reminder lands on the same finishing turn without competing for the
    exit-intent signal.
    """

    _singleton_group = "tb2_bg_process_reconcile"
    _order = 95

    def __init__(self) -> None:
        self._spawned_bg = False
        self._reminded = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._spawned_bg = False
        self._reminded = False
        self._pending_message = ""
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") if event.tool_input else ""
            if _has_backgrounding(cmd):
                self._spawned_bg = True
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if exit_intent and self._spawned_bg and not self._reminded:
            self._reminded = True
            self._pending_message = _RECONCILE_MSG
            # Force one more model turn so the reminder is actually read, then
            # let the agent decide whether any cleanup/verification is needed.
            keepalive = ToolCall(
                id=f"bg-{uuid.uuid4().hex[:8]}",
                name=_KEEPALIVE_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
            return
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _KEEPALIVE_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_KEEPALIVE_ACK
            )
        else:
            yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        new_msg = Message(role="user", content=msg)
        yield dataclasses.replace(
            event,
            messages=event.messages + (new_msg,),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._spawned_bg = False
        self._reminded = False
        self._pending_message = ""
        yield event
