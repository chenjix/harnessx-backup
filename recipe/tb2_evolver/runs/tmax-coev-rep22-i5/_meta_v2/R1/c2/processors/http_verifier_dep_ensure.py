# SPDX-License-Identifier: MIT
"""HttpVerifierDepEnsureProcessor — *guarantee* the grading HTTP client is present.

Structural fact about this benchmark (see tb2-playbook): HTTP-service tasks are
graded by an *external* verifier that queries the agent's service with the
standard Python ``requests`` client. The verifier's test files are injected
*after* the agent session ends, so the agent cannot see that the test does
``import requests``. Several service containers ship without ``requests``
installed, which makes the verifier's ``test_final_state.py`` fail at
*collection* time (``ModuleNotFoundError: No module named 'requests'``) even
when the agent's service is fully correct and running — a fully correct,
running service scores 0.

Why this processor exists (evidence)
------------------------------------
The prior ``HttpVerifierDepProcessor`` closed this gap with a *soft* nudge: it
appended a single ``user`` reminder message on the model turn after a service
was detected, then relied on the model to run ``pip install requests`` itself.
On ``task_000028_7fe033ac`` (an nginx + C++ unix-socket service) that soft nudge
produced **zero** behavioural change — the trajectory contains no
``pip install requests`` and no ``import requests`` check, the service returned
a correct ``200 OK`` body, and the verifier still failed at collection with
``ModuleNotFoundError: No module named 'requests'`` → reward 0. A reminder the
model can silently ignore is not a reliable fix for a structural grading
dependency.

Design: structural guarantee, not a nudge
------------------------------------------
This processor detects that a service task is in play (broad, framework-agnostic
signals) and, at the moment the agent tries to *exit*, injects a single **real**
``Bash`` tool call that makes ``requests`` importable by the system Python. The
run loop executes injected tool calls directly (``approved=True``), so the
install happens regardless of whether the model would have complied with a text
reminder. The injected command is idempotent and cheap:

    python3 -c 'import requests' 2>/dev/null || python3 -m pip install --quiet requests \
        || pip install --quiet requests || true; \
    python3 -c 'import requests, sys; sys.stdout.write("requests OK\n")' 2>&1 || true

It fires **at most once per task** and **only on tasks where a service was
detected**, so non-service tasks (the majority) are never touched. Firing on
exit-intent (rather than mid-task) means the service is already up and the agent
is done, so the extra tool round is the last useful action of the run.

Interaction with ``CustomSelfVerifyProcessor`` (``_order = 90``)
----------------------------------------------------------------
That processor injects a one-shot ``_tb2_self_verify`` keepalive tool call on the
*first* exit-intent. When it does, ``event.tool_calls`` is non-empty, so this
processor (higher ``_order``) treats the turn as "not an exit" and stays silent —
the self-verify checklist runs first. On the *next* exit-intent the keepalive is
spent, ``tool_calls`` is empty again, and this processor injects the install.
Thus the two never collide on the same turn.

Carries no task-specific constants — no task id, path, port, or framework unique
to any one task. The service signals match any HTTP-serving stack (Flask /
FastAPI / uvicorn / gunicorn / nginx / node / http.server / a bind to a local
port / a ``--port`` flag / an HTTP client probe against ``127.0.0.1:<port>``).
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

# Signals that the agent is standing up / probing an HTTP service that the
# external verifier will query with ``requests``. Deliberately generic so it
# matches any language / framework serving over HTTP, not one task's stack.
_SERVICE_SIGNALS = re.compile(
    r"""(?ix)
    (?:
        \bflask\b
      | \bfastapi\b
      | \buvicorn\b
      | \bgunicorn\b
      | \bhypercorn\b
      | \bwaitress\b
      | \bhttp\.server\b
      | \bhttpd\b
      | \bnginx\b
      | \bapache2?\b
      | \bnode\b
      | \bapp\.run\s*\(
      | \.listen\s*\(
      | \blisten\s+\d{2,5}\b
      | (?:127\.0\.0\.1|0\.0\.0\.0|localhost)\s*[:]\s*\d{2,5}
      | \b--port\b
      | \bhost\s*=\s*['"]?(?:127\.0\.0\.1|0\.0\.0\.0)['"]?
      | \bproxy_pass\b
      | \bcurl\b
      | \bwget\b
    )
    """
)

# Idempotent: no-op if requests is already importable; otherwise install it via
# whichever pip entrypoint exists. Never fails the step (`|| true`) so a
# no-network container cannot turn this into an `exit_reason=error`.
_ENSURE_REQUESTS_CMD = (
    "python3 -c 'import requests' 2>/dev/null "
    "|| python3 -m pip install --quiet requests 2>/dev/null "
    "|| pip install --quiet requests 2>/dev/null "
    "|| pip3 install --quiet requests 2>/dev/null "
    "|| true; "
    "python3 -c 'import requests, sys; sys.stdout.write(\"[requests-ensure] importable: \" + requests.__version__ + \"\\n\")' 2>&1 "
    "|| echo '[requests-ensure] WARNING: requests still not importable'"
)

_ENSURE_FOLLOWUP = (
    "[HttpVerifierDep] The command above ensured the standard Python `requests` "
    "client is importable by the system Python. The automated integration test "
    "that grades this task runs in a separate phase and does `import requests`; "
    "if it were missing, that test would fail at collection time before ever "
    "reaching your service. Your service should still be running — do not kill "
    "it. If the ensure command reported a WARNING, install `requests` another "
    "way now; otherwise you may finish."
)


class HttpVerifierDepEnsureProcessor(MultiHookProcessor):
    """On exit-intent of a service task, run a real Bash install of `requests` once."""

    _singleton_group = "http_verifier_dep_ensure"
    # After CustomSelfVerifyProcessor (90) and the legacy nudge (91) so the
    # self-verify keepalive lands first and we fire on a later exit-intent.
    _order = 93

    def __init__(self) -> None:
        self._service_seen = False
        self._ensured = False
        self._pending_followup = False

    async def on_task_start(self, event: TaskStartEvent):
        self._service_seen = False
        self._ensured = False
        self._pending_followup = False
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if not self._service_seen and event.tool_name == "Bash":
            cmd = ""
            try:
                cmd = event.tool_input.get("command", "") or ""
            except Exception:
                cmd = ""
            if cmd and _SERVICE_SIGNALS.search(cmd):
                self._service_seen = True
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        # Append the one-line context note exactly once, on the turn right
        # after we injected the install tool call (its result is now visible).
        if not self._pending_followup:
            yield event
            return
        self._pending_followup = False
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=_ENSURE_FOLLOWUP),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if (
            exit_intent
            and self._service_seen
            and not self._ensured
        ):
            self._ensured = True
            self._pending_followup = True
            install = ToolCall(
                id=f"reqdep-{uuid.uuid4().hex[:8]}",
                name="Bash",
                input={"command": _ENSURE_REQUESTS_CMD},
            )
            yield dataclasses.replace(event, tool_calls=(install,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._service_seen = False
        self._ensured = False
        self._pending_followup = False
        yield event
