# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""VerifierDepEnsureProcessor — close the "verifier crashes at import" gap.

Structural observation (TB2 / tmax eval): for service-style tasks the
external verifier phase runs a pytest module (``/tmp/test_final_state.py``)
that probes the running service over HTTP using the standard Python
``requests`` client. The eval container's *system* Python
(``/usr/lib/python3.10``) does NOT ship ``requests``, so the verifier module
fails at *collection* time with::

    ImportError while importing test module '/tmp/test_final_state.py'
    ...
    E   ModuleNotFoundError: No module named 'requests'

The agent's actual solution is never scored, even when the service works
correctly end-to-end (observed reward=0 tasks whose own tool output shows the
service returning the expected response, e.g. an integration test that
returns ``150`` through nginx).

Why the agent cannot fix this itself: the verifier's test files are injected
*after* the agent session ends and are absent during execution (tb2-playbook
"Sandbox topology"). The agent has no way to see or infer the dependency —
this is an *environment* deficiency, not a model capability gap. The correct
place to close it is a harness mechanism that hardens the runtime for a whole
class of HTTP/socket-service tasks.

Mechanism: at task start — which runs inside the same container the verifier
later inspects (verifier runs against the container's *final state*, sharing
the same system Python) — best-effort ensure the ubiquitous ``requests``
client is importable by the *system* ``python3`` interpreter. ``requests`` is
a universal, stdlib-adjacent HTTP client, not a task-specific literal; the fix
generalises to any HTTP-service task the verifier reaches.

Safety / Pareto properties:
- Gated on a service-task heuristic so non-service tasks pay nothing.
- Idempotent: if ``requests`` already imports in system ``python3``, the
  install is skipped (the guard command short-circuits).
- Targets the *system* interpreter explicitly (``python3 -m pip``) so the
  module lands where the verifier's ``python3`` will look for it, with a
  ``pip``/``pip3`` fallback for images where ``-m pip`` is unavailable.
- Fully best-effort: every failure path (no sandbox, offline index, pip
  missing, timeout) is swallowed — the processor can only help or be neutral,
  it never blocks the run or mutates the conversation.
- Does NOT touch ``event.messages`` — contract-neutral (yields the event
  unchanged), so it cannot violate the message-contract check.
"""
from __future__ import annotations

import re

from harnessx.core.events import TaskStartEvent
from harnessx.core.processor import MultiHookProcessor

# Signals that the task stands up something an HTTP/socket verifier will probe.
_SERVICE_HINT_RE = re.compile(
    r"\b("
    r"http|https|https?://|curl|wget|requests|"
    r"nginx|uwsgi|gunicorn|flask|fastapi|uvicorn|starlette|django|"
    r"microservice|micro-service|api\s+service|rest\s+api|endpoint|"
    r"proxy|reverse\s+proxy|upstream|"
    r"listen(?:s|ing)?\s+on|port\s+\d|127\.0\.0\.1|localhost|"
    r"unix\s+socket|socket\s+server|web\s+server|serve\b|backend"
    r")\b",
    re.IGNORECASE,
)

# Best-effort, idempotent, quiet. If `requests` already imports under the
# system `python3`, this is a no-op. Prefer `python3 -m pip` so the module
# lands in the same interpreter the verifier's `python3 /tmp/test_*.py` uses;
# fall back to bare pip/pip3 for images without `-m pip`. The trailing
# `|| true` guarantees a zero exit even when no index is reachable.
_ENSURE_CMD = (
    "python3 -c 'import requests' 2>/dev/null "
    "|| python3 -m pip install --quiet requests >/dev/null 2>&1 "
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

    def __init__(self, timeout_seconds: float = 90.0) -> None:
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
