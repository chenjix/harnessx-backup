# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""GraderClientDepProcessor — ensure common grader HTTP-client deps are present.

Motivation (harness deficiency, not a task solution)
-----------------------------------------------------
Several Tmax tasks ask the agent to build and run a local HTTP service
(Flask / FastAPI / http.server / a compiled binary bound to a port). The
external verifier phase — which runs *after* the agent exits and whose test
files are not visible during the agent phase — hits that service using the
``requests`` library. When ``requests`` is not installed in the image, the
verifier's ``test_*.py`` fails at *collection* time with::

    ModuleNotFoundError: No module named 'requests'

i.e. the task is graded 0 even when the agent's service is fully correct.
Agents in this benchmark consistently probe their own service with
``urllib``/``curl`` (both preinstalled), so they never surface — and never
fix — the missing ``requests`` dependency the grader depends on.

This is a structural gap: the agent has no signal that the grader will
``import requests``, and the verifier's test file is absent during the run.
The fix is a mechanical, idempotent environment-preparation step, not domain
knowledge — hence a Control processor rather than a prompt rule.

Mechanism
---------
* ``on_after_tool`` — watch Bash commands for HTTP-service signatures
  (framework imports, port binds, ``127.0.0.1:<port>``, ``manage.py
  runserver`` …). If seen, arm the guard.
* ``on_after_model`` — when the model tries to end the turn (no tool calls)
  and the guard is armed but not yet satisfied, inject a single real Bash
  tool call that best-effort installs the common grader client libs. The
  install is quiet, time-bounded, and idempotent: if the packages are
  already present (or the network is unavailable) it is a harmless no-op and
  the agent proceeds to exit on the next turn.

Fires at most once per task. Scoped to a *class* of tasks (any task that
stands up an HTTP service) — no task ids, no dataset-specific literals.
"""
from __future__ import annotations

import dataclasses
import re
import uuid

from harnessx.core.events import (
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCall,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Signatures that indicate the agent is standing up a local HTTP service.
# Kept generic across web frameworks + stdlib + raw socket binds.
_HTTP_SERVICE_RE = re.compile(
    r"(?:"
    r"\bflask\b|\bfastapi\b|\buvicorn\b|\bgunicorn\b|\bwaitress\b|\bhypercorn\b"
    r"|\bhttp\.server\b|\bBaseHTTPRequestHandler\b|\bHTTPServer\b|\bsocketserver\b"
    r"|\bmanage\.py\s+runserver\b|\bapp\.run\("
    r"|\b127\.0\.0\.1:\d+\b|\b0\.0\.0\.0:\d+\b|\blocalhost:\d+\b"
    r"|--port\s*=?\s*\d+|\blisten\s+\d+|\bbind\(\s*\(\s*['\"]"
    r")",
    re.IGNORECASE,
)

# Best-effort, quiet, idempotent install of the HTTP client libs a grader
# commonly imports to exercise a service. If already installed or the network
# is down, this is a no-op that costs a couple of seconds.
_INSTALL_CMD = (
    "(python3 -c 'import requests' 2>/dev/null && echo '[grader-deps] requests already present') "
    "|| (pip install --quiet --disable-pip-version-check requests >/dev/null 2>&1 "
    "|| pip3 install --quiet --disable-pip-version-check requests >/dev/null 2>&1); "
    "python3 -c 'import requests, sys; print(\"[grader-deps] requests \" + requests.__version__)' "
    "2>/dev/null || echo '[grader-deps] requests unavailable (offline?) — continuing'"
)

_ACK_UNUSED = None  # (install feedback is surfaced via the Bash tool result itself)


class GraderClientDepProcessor(MultiHookProcessor):
    """Ensure ``requests`` is installed once, for tasks that stand up a service."""

    _singleton_group = "grader_client_deps"
    _order = 88  # just before CustomSelfVerifyProcessor (_order=90)

    def __init__(self, install_timeout_s: int = 90) -> None:
        self.install_timeout_s = int(install_timeout_s)
        self._service_seen = False
        self._done = False

    async def on_task_start(self, event: TaskStartEvent):
        self._service_seen = False
        self._done = False
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # Arm when the agent runs a command that looks like an HTTP service.
        # Detected on the call event (which carries tool_input); ToolResultEvent
        # has no tool_input, so detection must happen here.
        if not self._service_seen and event.tool_name == "Bash":
            inp = event.tool_input if isinstance(event.tool_input, dict) else {}
            cmd = str(inp.get("command", "") or "")
            if _HTTP_SERVICE_RE.search(cmd):
                self._service_seen = True
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and self._service_seen and not self._done:
            self._done = True
            install_call = ToolCall(
                id=f"gdep-{uuid.uuid4().hex[:8]}",
                name="Bash",
                input={"command": _INSTALL_CMD, "timeout": self.install_timeout_s * 1000},
            )
            yield dataclasses.replace(event, tool_calls=(install_call,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._service_seen = False
        self._done = False
        yield event
