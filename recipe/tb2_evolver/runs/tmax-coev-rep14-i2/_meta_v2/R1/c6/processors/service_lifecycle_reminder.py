# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ServiceLifecycleReminder for TB2/Tmax service-lifecycle tasks.

Closes a generalizable harness gap exposed by service / init-script /
CI-pipeline tasks: the agent frequently starts a long-lived background
service (an HTTP server, daemon, worker) while testing its solution, then
ends the session **without deciding whether that process should still be
running**. Some verifiers require the service to be *alive* for the check;
others require a *clean teardown* with no lingering processes. The stock
TB2 self-verify checklist only nudges the "still alive" direction
("For running services: confirm they are still alive and reachable right
now"), so an agent working a lifecycle/teardown task is actively steered
toward the wrong final state.

This processor does NOT decide the correct final state for the agent — it
cannot know the task's intent. It fires a single, two-sided reminder the
first time it observes the agent launching a background service, prompting
the agent to re-read the task and match its final process state to what the
task requires (leave running vs. stop cleanly / no lingering processes).

Contract-safe: mutates only the triggering tool's *result string*
(the same shape as CustomEditToolProcessor); never inserts messages, never
touches the system prompt. Fires at most once per task.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Command shapes that indicate a *long-lived* background process was launched.
# We deliberately require an explicit backgrounding / daemon signal so that
# short foreground commands (grep, ls, one-shot curl) never trip the reminder.
_BG_LAUNCH_PATTERNS = (
    r"nohup\s",                       # nohup <cmd>
    r"&\s*$",                          # trailing background operator
    r"&\s*(?:echo|disown|>)",         # background then record pid / disown
    r"\bdisown\b",
    r"\bsetsid\b",
    r"systemctl\s+start\b",
    r"service\s+\S+\s+start\b",
    r"\b(?:uvicorn|gunicorn|flask\s+run|http\.server|rails\s+server|node\s+\S+\.js)\b",
    r"start_service\b",               # invoking a lifecycle/init script
)

# Server-bind signals: a command that clearly stands up a listener. On their
# own these are only counted as a service launch when combined with a
# background/daemon signal above (handled in _looks_like_service_launch).
_BIND_PATTERNS = (
    r"ListenAndServe",
    r"\.listen\(",
    r"listen\s+\d",
    r"--port\b",
    r"-p\s+\d",
)

_REMINDER = (
    "\n\n[ServiceLifecycleReminder] You just started a long-running background "
    "process. Before you finish, re-read the task and decide the REQUIRED final "
    "state of this service:\n"
    "  - If the task/verifier expects the service to remain up, confirm it is "
    "still listening right now (e.g. re-issue a request or check the port).\n"
    "  - If the task describes a lifecycle/init script, CI pipeline, or graceful "
    "shutdown — OR you only started the process to test your solution — make sure "
    "you STOP it and leave NO lingering processes. Check with `pgrep -af <name>` "
    "(or `ps aux | grep <name>`) and terminate any strays you spawned.\n"
    "Match the final process state to what the task actually requires — do not "
    "leave test processes running by default."
)


class ServiceLifecycleReminder(MultiHookProcessor):
    """One-shot, two-sided reminder to reconcile final service/process state."""

    _singleton_group = "service_lifecycle_reminder"
    _order = 35  # after edit-detector (30), before self-verify (90)

    def __init__(self) -> None:
        self._fired: bool = False
        self._armed_calls: set[str] = set()

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._armed_calls.clear()
        yield event

    @staticmethod
    def _looks_like_service_launch(command: str) -> bool:
        if not command:
            return False
        bg = any(re.search(p, command) for p in _BG_LAUNCH_PATTERNS)
        if bg:
            return True
        # A bind signal alone is not enough (could be a foreground blocking
        # server the agent is inspecting); require it to be backgrounded.
        bind = any(re.search(p, command) for p in _BIND_PATTERNS)
        return bind and re.search(r"&\s*$", command) is not None

    async def on_before_tool(self, event: ToolCallEvent):
        if not self._fired and event.tool_name == "Bash":
            command = event.tool_input.get("command", "") if event.tool_input else ""
            if self._looks_like_service_launch(command):
                self._armed_calls.add(event.tool_call_id)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if event.tool_call_id in self._armed_calls and not self._fired:
            self._armed_calls.discard(event.tool_call_id)
            self._fired = True
            yield dataclasses.replace(event, result=(event.result or "") + _REMINDER)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._armed_calls.clear()
        yield event
