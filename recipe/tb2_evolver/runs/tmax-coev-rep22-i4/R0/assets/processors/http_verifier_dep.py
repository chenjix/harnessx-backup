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
  observes a command that starts an HTTP service / binds a local port
  (Flask / FastAPI / uvicorn / gunicorn / ``http.server`` / an explicit
  ``127.0.0.1:<port>`` or ``0.0.0.0:<port>`` bind, etc.).
* On the *next* model turn after that flag is set, it injects exactly one
  user message reminding the agent that the external integration test drives
  the service with the ``requests`` client, so the agent must make ``requests``
  importable by the system Python before finishing.

Firing on the turn *after* the server starts (rather than at exit-intent) is
deliberate: it decouples this hook from ``CustomSelfVerifyProcessor``'s
exit-intent keepalive rewrite, and gives the agent the rest of the session to
install the dependency and keep working.

It fires at most once per task and only on tasks where a server was actually
started, so non-service tasks (the majority) are never touched. It only
*appends* a single message — it never blocks a tool call, never mutates the
system prompt, and never rewrites existing messages — mirroring the append
pattern of ``CustomSelfVerifyProcessor`` / ``TaskTimeReminderProcessor``.
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

# Signals that the agent has started (or is starting) an HTTP service that the
# external verifier will query. Kept deliberately generic so it matches any
# language/framework serving over HTTP, not one task's stack.
_SERVICE_SIGNALS = re.compile(
    r"""(?ix)
    (?:
        \bflask\b
      | \bfastapi\b
      | \buvicorn\b
      | \bgunicorn\b
      | \bhttp\.server\b
      | \bhttpd\b
      | \bapp\.run\s*\(
      | \.listen\s*\(
      | \blisten\s+\d{2,5}\b
      | (?:127\.0\.0\.1|0\.0\.0\.0|localhost)\s*[:]\s*\d{2,5}
      | \b--port\b
      | \bhost\s*=\s*['"]?(?:127\.0\.0\.1|0\.0\.0\.0)['"]?
    )
    """
)

_HTTP_VERIFIER_DEP_MSG = (
    "[HttpVerifierReminder] You have started an HTTP service. The automated "
    "integration test that grades this task runs in a separate phase and "
    "drives your service using the standard Python `requests` HTTP client "
    "(its test module does `import requests`). If `requests` is not importable "
    "by the system Python, the test fails at collection time before it ever "
    "reaches your service — a fully correct, running service still scores 0.\n\n"
    "Before you finish, make `requests` importable by the same interpreter the "
    "grader uses, e.g.:\n"
    "```bash\n"
    "python3 -c 'import requests' 2>/dev/null || python3 -m pip install requests\n"
    "python3 -c 'import requests; print(\"requests OK\", requests.__version__)'\n"
    "```\n"
    "Do this regardless of which client you used for your own testing "
    "(urllib, sockets, etc.). Keep your service running afterwards."
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
