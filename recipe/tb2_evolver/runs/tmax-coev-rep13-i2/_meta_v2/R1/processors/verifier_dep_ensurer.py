# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""VerifierDepEnsurer — make the standard HTTP-client dependency importable on
HTTP-service tasks so the external verifier's test module can be *collected*.

## The gap this closes (Control lever, corrective)

On this benchmark the verifier runs `pytest` against the container's final
state in a phase the agent never sees. For the whole class of tasks that ask
the agent to "write / start an HTTP server exposing endpoint X on port Y", the
verifier's `test_final_state.py` drives those endpoints with the third-party
`requests` library — its very first lines are `import requests`. When the base
image does not already have `requests` installed, pytest fails at *collection
time*:

    ModuleNotFoundError: No module named 'requests'
    Interrupted: 1 error during collection

Because collection aborts, **every** test in the module errors out at once,
including the tests the agent's solution would actually have passed. The agent
has no way to anticipate this: it tests its own server perfectly well with the
standard library (`urllib.request`) or `curl`, so it never has a reason to
install `requests`, and the verifier's test file does not exist during the
agent phase (so it cannot be inspected). Observed on 7 distinct tasks spanning
6 domains, every one an "HTTP server + endpoint/port" task, every one blocked
by exactly this `import requests` collection error.

Note this is *not* a network-blocked environment — `pip install` succeeds here
(numpy / scipy / flask / SpeechRecognition were all fetched successfully by
other tasks in the same run), so ensuring the dependency is a viable fix.

## Mechanism

Purely mechanical and scoped to the HTTP-service task class:

1. `on_task_start` inspects `task_description`. If it names an HTTP service the
   verifier will drive (an HTTP/REST server AND a concrete endpoint/port/listen
   surface), the task is armed. Non-service tasks are never touched.

2. On the *first* approved Bash call of an armed task, the command is rewritten
   to be prefixed with a single, idempotent, silent guard:

       python3 -c 'import requests' 2>/dev/null || pip install -q requests >/dev/null 2>&1 || true ; <original>

   - It runs at most once per task (a latch clears after the first rewrite).
   - It is a no-op when `requests` already imports (the common `||` short
     circuits before any install), so it costs nothing on images that already
     ship it.
   - It can never make the agent's own command fail: the guard is fully
     `|| true`-terminated, then the agent's original command runs unchanged
     after the `;`. The agent's stdout/stderr semantics are preserved.

The processor never mutates message history, so it cannot violate the message
contract — it only rewrites the `tool_input` of an approved `ToolCallEvent`,
the same interception surface used by the existing pipeline processors.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import ToolCallEvent, TaskEndEvent, TaskStartEvent
from harnessx.core.processor import MultiHookProcessor

# An HTTP-service task: the description must reference an HTTP/REST server-like
# surface AND a concrete listening surface (endpoint / port / listen). Both
# halves are required so that generic "make a request" or "download" tasks that
# merely mention http:// URLs are NOT armed.
_SERVICE_RE = re.compile(
    r"\b(http\s+server|https?\s+service|rest\s+api|web\s+server|web\s+service|"
    r"http\s+service|api\s+server|api\s+endpoint|flask|fastapi|uvicorn|gunicorn|"
    r"http\.server|start\s+.{0,20}server)\b",
    re.IGNORECASE,
)
_LISTEN_RE = re.compile(
    r"\b(endpoint|listen(?:ing)?|port\s*\d{2,5}|:\d{3,5}\b|GET\s*/|POST\s*/|/query|serve)\b",
    re.IGNORECASE,
)

# Idempotent, silent, best-effort dependency guard. Ordered so it can never
# abort the agent's own command: the whole guard is `|| true`-terminated, and
# the agent's original command follows the `;`.
_GUARD = (
    "python3 -c 'import requests' 2>/dev/null "
    "|| pip install -q requests >/dev/null 2>&1 || true ; "
)


class VerifierDepEnsurer(MultiHookProcessor):
    """Ensure `requests` is importable on HTTP-service tasks (verifier dep)."""

    _singleton_group = "verifier_dep_ensurer"
    _order = 10  # run early, before other before-tool processors rewrite/execute

    def __init__(self) -> None:
        self._armed = False
        self._done = False

    async def on_task_start(self, event: TaskStartEvent):
        desc = event.task_description or ""
        self._armed = bool(_SERVICE_RE.search(desc) and _LISTEN_RE.search(desc))
        self._done = False
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if (
            not self._armed
            or self._done
            or event.tool_name != "Bash"
            or not event.approved
        ):
            yield event
            return
        command = (event.tool_input or {}).get("command", "")
        if not isinstance(command, str) or not command.strip():
            # Empty / malformed call — don't consume the latch on it.
            yield event
            return
        self._done = True  # fire exactly once per task
        new_input = dict(event.tool_input or {})
        new_input["command"] = _GUARD + command
        yield dataclasses.replace(event, tool_input=new_input)

    async def on_task_end(self, event: TaskEndEvent):
        self._armed = False
        self._done = False
        yield event
