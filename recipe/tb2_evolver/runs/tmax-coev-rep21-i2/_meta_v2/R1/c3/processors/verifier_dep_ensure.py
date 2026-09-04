# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""VerifierDepEnsureProcessor — close the "verifier crashes at import" gap.

Structural observation (TB2 / tmax eval): for service-style tasks the
external verifier phase runs a pytest module that makes HTTP calls with the
standard Python ``requests`` library. The eval container's system Python does
NOT ship ``requests``, so ``test_final_state.py`` fails at *collection* time
with ``ModuleNotFoundError: No module named 'requests'`` — the agent's actual
solution is never scored, even when the service works correctly.

The agent cannot see or infer this: the verifier's test files are injected
*after* the agent session ends and are absent during execution (see the
tb2-playbook "Sandbox topology" note). This is therefore an *environment*
deficiency, not a model capability gap — the right place to close it is a
harness mechanism that makes the runtime robust for a whole class of tasks.

Mechanism: at task start, for tasks that look like they stand up an HTTP /
socket service (the class the verifier probes over HTTP), best-effort ensure
the ubiquitous ``requests`` client library is importable in the system Python.
``requests`` is a universal stdlib-adjacent HTTP client, not a task-specific
literal — the fix generalises to any HTTP-service task the verifier reaches.

Safety / Pareto properties:
- Gated on a service-task heuristic so non-service tasks pay nothing.
- Idempotent: if ``requests`` already imports, the install is skipped.
- Fully best-effort: every failure path (no sandbox, offline index, pip
  missing) is swallowed — the processor can only help or be neutral, it
  never blocks the run or mutates the conversation.
- Does NOT touch ``event.messages`` — contract-neutral.
"""
from __future__ import annotations

import re

from harnessx.core.events import TaskStartEvent
from harnessx.core.processor import MultiHookProcessor

# Signals that the task stands up something an HTTP/socket verifier will probe.
_SERVICE_HINT_RE = re.compile(
    r"\b("
    r"http|https|https?://|curl|wget|requests|"
    r"nginx|uwsgi|gunicorn|flask|fastapi|uvicorn|"
    r"microservice|micro-service|api\s+service|rest\s+api|endpoint|"
    r"proxy|reverse\s+proxy|upstream|"
    r"listen(?:s|ing)?\s+on|port\s+\d|127\.0\.0\.1|localhost|"
    r"unix\s+socket|socket\s+server|web\s+server|serve\b"
    r")\b",
    re.IGNORECASE,
)

# Best-effort, idempotent, quiet. If requests already imports, this is a no-op.
# The `|| true` guarantees a zero exit even when pip has no reachable index.
_ENSURE_CMD = (
    "python3 -c 'import requests' 2>/dev/null "
    "|| pip install --quiet requests >/dev/null 2>&1 "
    "|| pip3 install --quiet requests >/dev/null 2>&1 "
    "|| true"
)


class VerifierDepEnsureProcessor(MultiHookProcessor):
    """Ensure the standard HTTP client the verifier uses is importable.

    Fires once per task, only for service-shaped tasks. Best-effort and
    contract-neutral — never raises, never mutates messages.
    """

    _singleton_group = "verifier_dep_ensure"
    _order = 5

    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self._timeout = float(timeout_seconds)

    async def on_task_start(self, event: TaskStartEvent):
        try:
            desc = event.task_description or ""
            if desc and _SERVICE_HINT_RE.search(desc):
                from harnessx.sandbox.base import get_current_sandbox

                sandbox = get_current_sandbox()
                if sandbox is not None:
                    try:
                        await sandbox.exec(_ENSURE_CMD, timeout=self._timeout)
                    except Exception:
                        # Offline / pip missing / timeout — leave state as-is.
                        pass
        except Exception:
            # Never let a robustness helper break a task run.
            pass
        yield event


__all__ = ["VerifierDepEnsureProcessor"]
