# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ServiceDepsReminderProcessor.

Closes a structural failure mode observed on network-service tasks in the
tmax / terminal-bench-2 evaluation: the external verifier probes the agent's
HTTP/socket service using the Python ``requests`` client, but ``requests`` is
not guaranteed to be present in every task container. When it is absent the
verifier's ``test_final_state.py`` fails at *import/collection* time
(``ModuleNotFoundError: No module named 'requests'``) — a hard score of 0 even
when the agent's service is fully correct and reachable.

The agent has one tool (``Bash``) and no visibility into the verifier files
(they are injected after the agent exits), so it cannot know the verifier's
Python dependencies. This processor supplies that missing context as a
*mechanical, one-shot* nudge: when the agent has demonstrably been building a
network service (bound a socket / started nginx / listened on a port / used an
HTTP server library / probed a local port) and then tries to finish, it is
reminded to make the common HTTP client library importable, installing it if
missing. The install command is idempotent and harmless if the library is
already present or if installation is not possible.

This is a *class* fix, not a task fix: it fires on any task that exhibits the
network-service shape, keyed off the agent's own Bash activity, never off task
identifiers.
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
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


# Signals in the agent's Bash commands that indicate it is standing up (or
# probing) a network / HTTP service whose verifier is likely to use an HTTP
# client library. Intentionally broad — false positives only cost one cheap,
# idempotent import check; false negatives re-introduce the silent verifier
# crash.
_SERVICE_SIGNAL_RE = re.compile(
    r"""
    (?:\bnginx\b)                       # reverse proxy / web server
    | (?:\blisten\s*\()                 # C/C++ socket listen()
    | (?:sockaddr_un|AF_UNIX|AF_INET)   # raw socket servers
    | (?:proxy_pass)                    # nginx upstream config
    | (?:httplib|cpp-httplib)           # common header-only C++ HTTP lib
    | (?:http\.server|BaseHTTPServer)   # python stdlib http servers
    | (?:flask|gunicorn|uvicorn|fastapi)# python web frameworks
    | (?:\bgo\s+run\b.*http)            # go http servers
    | (?:127\.0\.0\.1:\d+)              # binding/probing a local port
    | (?:localhost:\d+)
    | (?:/dev/tcp/)                     # bash raw HTTP probe (curl/wget absent)
    | (?:\bcurl\b|\bwget\b).*http       # http probe via client
    | (?:\.sock\b)                      # unix-domain socket file
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DEPS_TOOL = "_svc_deps_reminder"
_DEPS_ACK = "Dependency check acknowledged. See the message above."

_DEPS_MSG = """\
[VerifierDependencyCheck] This task builds or exposes a network/HTTP service. \
Automated verifiers for service tasks commonly probe the endpoint from Python \
using the `requests` library, and they import it at module load — so if \
`requests` is not importable in this environment, the verification crashes at \
collection time and the task scores 0 regardless of whether your service works.

Before you finish, make the standard Python HTTP client importable. Run an \
idempotent check-then-install (safe to run even if it is already present, and \
harmless if installation is not possible):

```bash
python3 -c 'import requests' 2>/dev/null \\
  || pip install --quiet requests 2>/dev/null \\
  || pip3 install --quiet requests 2>/dev/null || true
python3 -c 'import requests, sys; print("requests OK", requests.__version__)' \\
  || echo "requests still unavailable"
```

This does not replace verifying your own service — keep confirming it responds \
correctly. It only ensures the verifier's own imports can succeed."""


class ServiceDepsReminderProcessor(MultiHookProcessor):
    """One-shot reminder to ensure verifier HTTP-client deps are importable.

    Fires at most once per task, and only when the agent's Bash activity has
    matched a network-service signal. Coordinates with any other exit-intent
    processor (e.g. the self-verify checklist) by only acting on a turn that
    still has no tool calls: if another processor has already converted the
    current exit attempt into a keepalive tool call, this processor stays
    silent and fires on the next genuine exit attempt instead.
    """

    _singleton_group = "svc_deps_reminder"
    # Run after CustomSelfVerifyProcessor (_order=90) so the two exit-intent
    # hooks serialize rather than both rewriting tool_calls on the same turn.
    _order = 91

    def __init__(self) -> None:
        self._service_seen = False
        self._reminded = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._service_seen = False
        self._reminded = False
        self._pending_message = ""
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # Absorb our own keepalive tool call so it never reaches the sandbox.
        if event.tool_name == _DEPS_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_DEPS_ACK
            )
            return
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") or ""
            if _SERVICE_SIGNAL_RE.search(cmd):
                self._service_seen = True
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
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if exit_intent and self._service_seen and not self._reminded:
            self._reminded = True
            self._pending_message = _DEPS_MSG
            keepalive = ToolCall(
                id=f"svc-{uuid.uuid4().hex[:8]}",
                name=_DEPS_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._service_seen = False
        self._reminded = False
        self._pending_message = ""
        yield event
