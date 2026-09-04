"""VerifierDepPrimerProcessor — ensure the external verifier's standard
Python HTTP-client dependency is present in the container.

## Why this is a harness deficiency, not a capability gap

In this benchmark the *task agent* and the *verifier* run in separate
phases against the same container. The verifier's test module is injected
**after** the agent exits and frequently does ``import requests`` to probe
any HTTP/network service the task asked the agent to build. If ``requests``
is not importable, the test module fails to even *collect* — pytest returns
rc=2 (``ModuleNotFoundError: No module named 'requests'``) and the task
scores 0 **regardless of whether the agent's service is correct**.

Agents in these HTTP-service tasks almost never install ``requests``
themselves because they implement the *server* (in C/C++/Go/Node or with
Python stdlib ``http.server``) and probe it with ``curl`` — they have no
reason to pull in the Python client. So a fully-correct solution silently
scores 0 purely because of a missing test-side dependency. That is an
environment gap the harness can close, not a reasoning error by the model.

## Mechanism

Deterministically inject a single, quiet, idempotent ``pip install`` of the
standard Python HTTP-client stack (``requests`` + its transitive deps) on
the first model turn of every task. The command:

  * is a no-op when the packages are already present (``pip`` reports
    "already satisfied" in <1s),
  * writes nothing to any task output path,
  * suppresses its own output and can never fail the turn
    (``|| true``), so it cannot corrupt an otherwise-correct solution,
  * persists in the container for the verifier phase.

This is general environment hardening for the whole class of
service/network tasks whose verifier probes the agent's endpoint from
Python; it is not keyed to any task id, path, or literal drawn from the
training trajectories.

The processor fires **exactly once per task** and appends its install call
to whatever the model emits on its first turn, so it never suppresses the
model's own first action.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import (
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCall,
)
from harnessx.core.processor import MultiHookProcessor

# Standard Python HTTP client used by external verifiers to probe services.
# Kept intentionally small and generic — only the client library a network
# test harness needs; nothing task-specific.
_PRIMER_PACKAGES = "requests"

# Quiet + idempotent + non-fatal. Runs against whatever python3/pip the
# verifier will use. Never touches a task output path.
_PRIMER_CMD = (
    "python3 -m pip install --quiet --disable-pip-version-check "
    f"{_PRIMER_PACKAGES} >/dev/null 2>&1 || true"
)


class VerifierDepPrimerProcessor(MultiHookProcessor):
    """Inject a one-shot, idempotent install of the standard HTTP client so
    the post-agent verifier's ``import requests`` collection step succeeds.

    Fires at most once per task, on the first model response.
    """

    _singleton_group = "verifier_dep_primer"
    # After tool-call correction / self-verify ordering; a large order keeps
    # it out of the way of the message-shaping processors.
    _order = 32

    def __init__(self, packages: str | None = None) -> None:
        # Allow override, but default to the generic HTTP client stack.
        self.packages = packages or _PRIMER_PACKAGES
        self._cmd = (
            "python3 -m pip install --quiet --disable-pip-version-check "
            f"{self.packages} >/dev/null 2>&1 || true"
        )
        self._primed = False

    async def on_task_start(self, event: TaskStartEvent):
        self._primed = False
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        if self._primed:
            yield event
            return
        self._primed = True
        primer = ToolCall(
            id=f"vdp-{uuid.uuid4().hex[:8]}",
            name="Bash",
            input={"command": self._cmd},
        )
        # Append the install to whatever the model already asked for so we
        # never suppress its own first action. If the model emitted no calls
        # (rare), the install still runs.
        yield dataclasses.replace(event, tool_calls=event.tool_calls + (primer,))

    async def on_task_end(self, event: TaskEndEvent):
        self._primed = False
        yield event
