# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ServerLaunchGuard — break the self-inflicted background-server thrash loop.

Terminal-Bench-style tasks that ask the agent to "leave an API running in the
background so our integration tests can query it" repeatedly trap the agent in
a loop:

  1. It launches the server backgrounded (``python app.py &``) WITHOUT
     redirecting stdout/stderr to a file, so when the endpoint returns a 500
     the traceback is invisible.
  2. To debug, it launches the server AGAIN — which fails with
     "Address already in use" because its OWN prior background process still
     holds the port.
  3. It misreads the self-caused port conflict as the problem and thrashes on
     kill/restart/switch-port/rewrite-file until the step or time budget is
     exhausted.

This processor fires two mechanical, task-agnostic nudges through ``Bash`` I/O:

  * ``on_after_tool`` on a backgrounded server launch that did NOT redirect
    output to a log file: append a one-shot note telling the agent to capture
    ``> /tmp/server.log 2>&1`` and ``cat`` it after ``sleep`` so the real
    startup / 500 error is visible.
  * ``on_after_tool`` when a tool result contains an "address already in use" /
    "port ... in use" string: append a one-shot note explaining the conflict is
    almost certainly the agent's own prior background server, and to free the
    port once (``fuser -k <port>/tcp`` or ``kill`` the listener) or simply reuse
    the already-running instance — NOT restart on a new port.

It never blocks a tool call; it only appends advisory text, so non-server tasks
are unaffected. Each distinct nudge fires at most a small bounded number of
times per task to avoid flooding context.
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

# A command that starts a long-running listener/server process. Matches common
# web frameworks and stdlib servers regardless of the specific task's app name.
_SERVER_LAUNCH_RE = re.compile(
    r"(?:"
    r"app\.run\(|"
    r"\buvicorn\b|\bgunicorn\b|\bhypercorn\b|\bwaitress\b|"
    r"flask\s+run\b|"
    r"\bhttp\.server\b|socketserver\.|HTTPServer\(|TCPServer\(|"
    r"manage\.py\s+runserver|"
    r"\.serve_forever\(|"
    r"node\s+[^\n]*server|npm\s+(?:run\s+)?start\b"
    r")",
    re.IGNORECASE,
)

# Standalone background operator: a trailing '&' that is NOT '&&', not '>&' /
# '2>&1'. Same shape as BgInstallGuard's detector.
_BACKGROUND_RE = re.compile(r"(?<![&>0-9])&(?!&)")

# Redirection to a file (so the agent can read the server log later).
_REDIRECT_RE = re.compile(r"(?:>>?|&>)\s*\S+|tee\b|nohup\b[^\n]*>|/tmp/\S*\.log")

# A port-in-use failure surfaced in tool output.
_PORT_IN_USE_RE = re.compile(
    r"address already in use|port\s+\d+\s+is\s+in\s+use|"
    r"already in use|EADDRINUSE|bind:\s*address already",
    re.IGNORECASE,
)

# Try to recover the port number so the nudge can name it concretely.
_PORT_NUM_RE = re.compile(r"port\s+(\d{2,5})|:(\d{2,5})\b|EADDRINUSE[^\d]*(\d{2,5})")


_LOG_NUDGE = (
    "\n\n[ServerLaunchGuard] You backgrounded a server but did not capture its "
    "output. If it errors (e.g. a 500 on a request), the traceback is invisible. "
    "Re-launch it capturing logs, then read them, e.g.:\n"
    "    python3 <app>.py > /tmp/server.log 2>&1 &\n"
    "    sleep 2 && cat /tmp/server.log\n"
    "When a request returns 500, the real cause is in that log — read it and fix "
    "the app instead of restarting the server."
)

_PORT_NUDGE_TEMPLATE = (
    "\n\n[ServerLaunchGuard] '{addr}' after you just launched a server almost "
    "always means YOUR OWN previous background server still holds the port — it "
    "is not a separate problem to work around. Do ONE of:\n"
    "  (a) reuse the server that is already running (test it directly), or\n"
    "  (b) free the port once and relaunch on the SAME required port:\n"
    "        fuser -k {port}/tcp 2>/dev/null; sleep 1\n"
    "        # or: kill $(lsof -t -i:{port}) 2>/dev/null\n"
    "Do NOT switch to a different port — the task requires the specified port. "
    "Do NOT enter a kill/restart loop; free the port at most once, then debug the "
    "app's actual error from its captured log."
)


class ServerLaunchGuard(MultiHookProcessor):
    """Append task-agnostic corrective nudges around background-server launches."""

    _singleton_group = "server_launch_guard"
    _order = 16  # right after BgInstallGuard (15), before edit detector (30)

    def __init__(self, max_log_nudges: int = 2, max_port_nudges: int = 2) -> None:
        self.max_log_nudges = max_log_nudges
        self.max_port_nudges = max_port_nudges
        # tool_call_id -> True when the launching command was a backgrounded
        # server without output redirection.
        self._pending_launch: dict[str, bool] = {}
        self._log_nudges = 0
        self._port_nudges = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._pending_launch.clear()
        self._log_nudges = 0
        self._port_nudges = 0
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            command = event.tool_input.get("command", "") or ""
            is_bg_server = (
                bool(_SERVER_LAUNCH_RE.search(command))
                and bool(_BACKGROUND_RE.search(command))
                and not _REDIRECT_RE.search(command)
            )
            if is_bg_server:
                self._pending_launch[event.tool_call_id] = True
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        was_undirected_launch = self._pending_launch.pop(event.tool_call_id, False)
        result_text = event.result or ""

        additions: list[str] = []

        # (1) Port-in-use in the tool output → self-inflicted-conflict nudge.
        if _PORT_IN_USE_RE.search(result_text) and self._port_nudges < self.max_port_nudges:
            self._port_nudges += 1
            port = self._extract_port(result_text)
            additions.append(
                _PORT_NUDGE_TEMPLATE.format(
                    addr="Address already in use",
                    port=port or "<PORT>",
                )
            )
        # (2) Backgrounded server launch with no log capture → capture-logs nudge.
        # Skip if we already emitted a port nudge this turn (avoid double noise).
        elif was_undirected_launch and self._log_nudges < self.max_log_nudges:
            self._log_nudges += 1
            additions.append(_LOG_NUDGE)

        if additions:
            yield dataclasses.replace(event, result=result_text + "".join(additions))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._pending_launch.clear()
        self._log_nudges = 0
        self._port_nudges = 0
        yield event

    @staticmethod
    def _extract_port(text: str) -> str | None:
        m = _PORT_NUM_RE.search(text)
        if not m:
            return None
        for g in m.groups():
            if g:
                return g
        return None
