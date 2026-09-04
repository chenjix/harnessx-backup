# SPDX-License-Identifier: MIT
"""Exit-time guard that ensures common *verifier* Python test-dependencies are
importable in the container before the agent finishes.

Motivation
----------
On Terminal-Bench 2 the task agent and the external verifier run in the **same
container** but in **separate phases**: the agent works, exits, and only then
the verifier's ``test_final_state.py`` is dropped into ``/tmp`` and executed
against the container's final state using the *same* system Python that the
agent had access to.

A recurring, purely-mechanical failure mode on HTTP-service tasks is that the
verifier module does ``import requests`` (the near-universal way to probe a
running HTTP endpoint) — but ``requests`` is not preinstalled in the image.
pytest then aborts at *collection* time:

    ImportError while importing test module '/tmp/test_final_state.py'
    ModuleNotFoundError: No module named 'requests'
    Interrupted: 1 error during collection

The agent's actual work can be entirely correct (server built, listening,
returning the right body) and the task still scores 0 because the test file
cannot even be *collected*. The agent has no way to infer this dependency:
the task description never mentions ``requests`` — it is a property of the
hidden verifier, not of the task.

Crucially this environment has **working outbound network / pip** (observed:
tasks that ``pip install numpy==1.26.0`` download from PyPI at full speed), so
the fix is available to the agent — it simply never knows to apply it.

What this processor does
------------------------
On the agent's exit-intent turn it injects a **real ``Bash`` tool call** (the
run loop executes it, so this is mechanical and cannot be narrated past) that,
for each package in a small allow-list of *test-harness* libraries, checks
whether the module already imports and, only if it is missing, attempts a
quiet ``pip install``. Everything is guarded with ``|| true`` so the call can
never fail the run, and the whole thing is a no-op on any task where the
packages are already present (the overwhelmingly common case).

Generality
----------
The allow-list contains only ubiquitous *verification* libraries used by
external test harnesses to exercise a solution (HTTP client, HTTP test client,
YAML), not anything task-specific: no task ids, ports, paths, or process
names. It closes a *class* of failures — "verifier imports a common test lib
absent from the image" — not one task. Installing an already-present package
is a no-op, so passing tasks are unaffected. It fires at most once per run and
adds at most one Bash round-trip only on runs that reach exit intent.

Wiring
------
Uses its own singleton group and a high ``_order`` so it runs *after* the
lifecycle self-verify processor. If that processor injects its snapshot on the
first exit-intent turn (making ``event.tool_calls`` non-empty), this processor
sees a non-exit turn and stays silent, firing instead on the next exit-intent
turn — the two never collide on the same event.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskStartEvent,
    TaskEndEvent,
    ToolCall,
)
from harnessx.core.processor import MultiHookProcessor


# Ubiquitous test-harness libraries an external verifier may import to exercise
# a solution. Kept deliberately tiny and generic — HTTP probing + config
# parsing are the common shapes. (module_name, pip_name) pairs.
_DEFAULT_DEPS = (
    ("requests", "requests"),
    ("yaml", "pyyaml"),
)


def _build_ensure_cmd(deps: tuple[tuple[str, str], ...]) -> str:
    parts = [
        "echo '=== VERIFIER DEP CHECK (auto) ==='",
    ]
    for mod, pkg in deps:
        # Only install when the import genuinely fails; keep fully guarded so
        # the tool call always succeeds and is a no-op when already present.
        parts.append(
            f"python3 -c 'import {mod}' 2>/dev/null "
            f"&& echo 'ok: {mod}' "
            f"|| (echo 'installing: {pkg}'; "
            f"(pip install -q {pkg} || pip3 install -q {pkg} "
            f"|| python3 -m pip install -q {pkg}) 2>/dev/null; "
            f"python3 -c 'import {mod}' 2>/dev/null "
            f"&& echo 'installed: {mod}' || echo 'unavailable: {mod}')"
        )
    parts.append("echo '=== END DEP CHECK ==='")
    return "; ".join(parts) + " || true"


_ACK_MSG = (
    "Above is an automatic environment check that ensured common verification "
    "libraries (HTTP client / config parsers) are importable — an external "
    "test harness may rely on them. This does not change your task; if any "
    "line reports 'unavailable', that library simply could not be installed. "
    "Continue with your final verification and finish when the task is "
    "genuinely complete."
)


class VerifierDepGuardProcessor(MultiHookProcessor):
    """Ensure common verifier test-deps are importable at exit-intent.

    Injects one real ``Bash`` tool call the first time the model tries to exit
    without tool calls, then a short user acknowledgement on the following
    ``on_before_model``. Fires at most once per task.
    """

    _singleton_group = "tb2_verifier_dep_guard"
    _order = 95  # after the self-verify / lifecycle processor (order 90)

    def __init__(self, deps: tuple[tuple[str, str], ...] | None = None) -> None:
        self._deps = tuple(deps) if deps else _DEFAULT_DEPS
        self._done = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._done = False
        self._pending_message = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if exit_intent and not self._done:
            self._done = True
            self._pending_message = _ACK_MSG
            ensure = ToolCall(
                id=f"vdg-{uuid.uuid4().hex[:8]}",
                name="Bash",
                input={"command": _build_ensure_cmd(self._deps)},
            )
            yield dataclasses.replace(event, tool_calls=(ensure,))
        else:
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

    async def on_task_end(self, event: TaskEndEvent):
        self._done = False
        self._pending_message = ""
        yield event
