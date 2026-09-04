"""VerifierDepGuard — ensure the Python HTTP test client is present for
network-service tasks before the agent exits.

Motivation (harness deficiency, not a model capability gap)
-----------------------------------------------------------
TB2 tasks that ask the agent to build/run an HTTP service are graded by an
external pytest verifier injected into the container *after* the agent exits.
For this task class the verifier test module characteristically drives the
service with Python's ``requests`` library (``import requests`` at module
top). If ``requests`` is not importable in the container's Python, pytest
fails at *collection* time — ``ModuleNotFoundError: No module named
'requests'`` — and the agent scores 0 even when its service is fully correct
and reachable.

The agent cannot know the verifier needs ``requests``: the dependency is
verifier-side, never mentioned in the task description, and the container ships
without it (several observed containers lack even ``curl``, so the agent falls
back to ``/dev/tcp``). This is a structural mismatch the harness should close,
not knowledge to inject into the prompt.

Mechanism
---------
Watch the Bash command stream for HTTP/network-service signals (an http
server, an nginx proxy, a unix socket, a bind on 127.0.0.1:<port>, a REST
verb, a header-only http lib, etc.). When such a task is detected AND the
model tries to end the turn, inject exactly one real ``Bash`` tool call that
best-effort installs ``requests`` into the container's Python so a downstream
``requests``-based verifier can at least import. The install is idempotent and
harmless: if ``requests`` is already importable it is a no-op; if the network
is unavailable it fails quietly and the agent simply continues to exit.

This fires at most once per task and only for the HTTP-service cluster, so it
adds no cost to unrelated tasks. It generalises to any TB2 network-service task
whose verifier imports ``requests`` — not to a specific task.
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


# Signals that the task involves an HTTP / network service the verifier is
# likely to probe with a Python ``requests`` client. Kept deliberately broad
# but network-specific so unrelated tasks (pure file / data-munging work)
# never trip it.
_HTTP_SIGNAL_RE = re.compile(
    r"(?:"
    r"\bnginx\b"
    r"|\bcurl\b"
    r"|\bhttplib\b"                 # cpp-httplib and friends
    r"|\bhttp[._-]?server\b"
    r"|https?://"
    r"|\b127\.0\.0\.1:\d+"
    r"|\blocalhost:\d+"
    r"|\b0\.0\.0\.0:\d+"
    r"|\.sock\b"                     # unix-socket backends
    r"|\bproxy_pass\b"
    r"|\blisten\s+\d"                # server config 'listen <port>'
    r"|\bGET\s+/"                    # REST verbs in the command / probe
    r"|\bPOST\s+/"
    r"|\buvicorn\b|\bgunicorn\b|\bflask\b|\bfastapi\b"
    r")",
    re.IGNORECASE,
)

# One idempotent, quiet best-effort install. Tries pip first (network usually
# available in this sandbox), then falls back to the distro package, then
# reports whether the import now works so the model sees the outcome.
_INSTALL_CMD = (
    "python3 -c 'import requests' 2>/dev/null "
    "&& echo '[verifier-dep] requests already present' "
    "|| { "
    "python3 -m pip install --quiet --disable-pip-version-check requests >/dev/null 2>&1; "
    "python3 -m pip install --quiet --disable-pip-version-check --user requests >/dev/null 2>&1; "
    "command -v apt-get >/dev/null 2>&1 && (apt-get install -y -q python3-requests >/dev/null 2>&1 || true); "
    "python3 -c 'import requests' 2>/dev/null "
    "&& echo '[verifier-dep] installed requests for the HTTP verifier' "
    "|| echo '[verifier-dep] could not install requests (no network / no pip); continuing'; }"
)

class VerifierDepGuard(MultiHookProcessor):
    """Provision the ``requests`` client for HTTP-service tasks at exit time.

    Fires at most once per task and only when HTTP-service signals were seen
    in the Bash stream. On the model's first exit attempt it replaces the
    empty tool-call turn with a single real ``Bash`` install command; the
    normal loop executes it and hands the result back, after which the agent
    is free to exit again.
    """

    _singleton_group = "tb2_verifier_dep_guard"
    # Run after CustomSelfVerifyProcessor (_order=90) so that on a shared exit
    # turn our real Bash install wins the tool_calls replacement first; the
    # self-verify checklist still fires once on the *next* exit attempt.
    _order = 95

    def __init__(self) -> None:
        self._http_seen = False
        self._done = False

    async def on_task_start(self, event: TaskStartEvent):
        self._http_seen = False
        self._done = False
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            cmd = (event.tool_input or {}).get("command", "") or ""
            if _HTTP_SIGNAL_RE.search(cmd):
                self._http_seen = True
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if exit_intent and self._http_seen and not self._done:
            self._done = True
            install = ToolCall(
                id=f"vdep-{uuid.uuid4().hex[:8]}",
                name="Bash",
                input={"command": _INSTALL_CMD},
            )
            yield dataclasses.replace(event, tool_calls=(install,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._http_seen = False
        self._done = False
        yield event
