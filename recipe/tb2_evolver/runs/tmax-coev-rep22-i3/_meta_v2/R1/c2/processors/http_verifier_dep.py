# SPDX-License-Identifier: MIT
"""HttpVerifierDepProcessor — ensure the grading HTTP client lib is present.

Structural fact about this benchmark (see tb2-playbook): HTTP-service tasks
are graded by an *external* verifier that queries the agent's service with the
standard Python ``requests`` client. The verifier's test files are injected
*after* the agent session ends, so the agent cannot see that the test does
``import requests``. Several service containers ship without ``requests``
installed, which makes the verifier's ``test_final_state.py`` fail at
*collection* time (``ModuleNotFoundError: No module named 'requests'``) even
when the agent's service is fully correct and running.

This processor closes that gap with a Control hook, not a prompt rule:

* It watches the agent's ``Bash`` commands and flips a per-task flag when it
  observes a command that starts an HTTP/network service *or probes a local
  service endpoint*. The detector is deliberately generic — it matches any
  language/framework/binary serving over a TCP port or UNIX socket, plus the
  common bash/CLI idioms an agent uses to verify its own running service.
* On the *next* model turn after that flag is set, it injects exactly one
  user message reminding the agent that the external integration test drives
  the service with the ``requests`` client, so the agent must make ``requests``
  importable by the system Python before finishing.

Firing on the turn *after* the server starts (rather than at exit-intent) is
deliberate: it decouples this hook from ``CustomSelfVerifyProcessor``'s
exit-intent keepalive rewrite, and gives the agent the rest of the session to
install the dependency and keep working.

It fires at most once per task and only on tasks where a server was actually
started (or probed), so non-service tasks (the majority) are never touched. It
only *appends* a single message — it never blocks a tool call, never mutates
the system prompt, and never rewrites existing messages — mirroring the append
pattern of ``CustomSelfVerifyProcessor`` / ``TaskTimeReminderProcessor``.

Evolve-set evidence for widening the detector (see journal Round 1): the
narrow framework-keyword regex missed both ``task_000028`` (nginx + a compiled
C++ socket server started via ``nohup /app/server &``; the agent probed it
with the bash ``/dev/tcp/127.0.0.1/8080`` idiom) and ``task_001857`` (a
compiled C++ daemon started via ``nohup <bin> &`` and probed with a Python
``socket.connect((host, port))`` call). Neither used flask/fastapi/uvicorn,
so the reminder never fired and both scored 0 purely on the ``requests``
collection error. The widened detector below catches the generic
service-start and self-probe shapes that those trajectories share with any
future HTTP-service task.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Signals that the agent has started (or is starting / probing) a network
# service that the external verifier will query with ``requests``. Kept
# deliberately generic so it matches any language / framework / compiled
# binary serving over HTTP, plus the CLI/bash idioms an agent uses to verify
# its own running service. Carries no task-specific constants.
_SERVICE_SIGNALS = re.compile(
    r"""(?ix)
    (?:
        # --- Web frameworks / app servers ---
        \bflask\b
      | \bfastapi\b
      | \buvicorn\b
      | \bgunicorn\b
      | \bhypercorn\b
      | \bwaitress\b
      | \bstreamlit\b
      | \bhttp\.server\b
      | \bhttpd\b
      | \bapp\.run\s*\(
      | \.listen\s*\(
      # --- Web / proxy servers started by name or via init ---
      | \bnginx\b
      | \bapache2?\b
      | \bcaddy\b
      | \blighttpd\b
      | \b(?:systemctl|service)\s+(?:start|restart|enable)\b
      | \bsystemd-run\b
      # --- Generic daemonize idiom: nohup/setsid ... &  (compiled binaries
      #     run as a background service; require the trailing & so one-shot
      #     `nohup make` style builds do not trip the detector) ---
      | \b(?:nohup|setsid)\b[^\n]*&\s*(?:$|[#;])
      | \b(?:nohup|setsid)\b[^\n]*&\s*\n
      # --- Explicit port / bind on a loopback or wildcard address ---
      | \blisten\s+\d{2,5}\b
      | (?:127\.0\.0\.1|0\.0\.0\.0|localhost)\s*[:]\s*\d{2,5}
      | \b--port\b
      | \bhost\s*=\s*['"]?(?:127\.0\.0\.1|0\.0\.0\.0)['"]?
      # --- Self-probe idioms (agent verifying its own service) ---
      | /dev/tcp/[\w.\-]+/\d{2,5}      # bash pseudo-device TCP probe
      | \b(?:curl|wget|http|https)\b[^\n]*\bhttps?://
      | \bsocket\.connect\s*\(         # python socket probe
      | \bsocket\.socket\s*\(
      | \bnc\s+-\w*\b                  # netcat probe
      # --- Listening-port inspection tools ---
      | \b(?:ss|netstat|lsof)\b[^\n]*\b(?:-\w*l\w*|LISTEN|:\d{2,5})
    )
    """
)

_HTTP_VERIFIER_DEP_MSG = (
    "[HttpVerifierReminder] You have started (or are probing) a network "
    "service. The automated integration test that grades this task runs in a "
    "separate phase and drives your service using the standard Python "
    "`requests` HTTP client (its test module does `import requests`). If "
    "`requests` is not importable by the system Python, the test fails at "
    "collection time before it ever reaches your service — a fully correct, "
    "running service still scores 0.\n\n"
    "Before you finish, make `requests` importable by the same interpreter the "
    "grader uses, e.g.:\n"
    "```bash\n"
    "python3 -c 'import requests' 2>/dev/null || python3 -m pip install requests\n"
    "python3 -c 'import requests; print(\"requests OK\", requests.__version__)'\n"
    "```\n"
    "Do this regardless of which client you used for your own testing "
    "(urllib, sockets, /dev/tcp, curl, etc.). Keep your service running "
    "afterwards."
)


class HttpVerifierDepProcessor(MultiHookProcessor):
    """Nudge service tasks to install the `requests` grading client once."""

    _singleton_group = "http_verifier_dep"
    _order = 91  # after CustomSelfVerifyProcessor (90), so its ack lands first

    def __init__(self) -> None:
        self._service_started = False
        self._fired = False

    async def on_task_start(self, event: TaskStartEvent):
        self._service_started = False
        self._fired = False
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if not self._service_started and event.tool_name == "Bash":
            cmd = ""
            try:
                cmd = event.tool_input.get("command", "") or ""
            except Exception:
                cmd = ""
            if cmd and _SERVICE_SIGNALS.search(cmd):
                self._service_started = True
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        # Fire once, on the first model turn after a service was detected.
        if not self._service_started or self._fired:
            yield event
            return
        self._fired = True
        yield dataclasses.replace(
            event,
            messages=event.messages
            + (Message(role="user", content=_HTTP_VERIFIER_DEP_MSG),),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._service_started = False
        self._fired = False
        yield event
