# SPDX-License-Identifier: MIT
"""ProactiveVerifierDepGuardProcessor — ensure common *verifier* Python
test-dependencies are importable in the container regardless of how the agent's
run terminates.

Motivation
----------
On Terminal-Bench 2 the task agent and the external verifier run in the **same
container** but in **separate phases**: the agent works, exits, and only then
the verifier's ``test_final_state.py`` is dropped into ``/tmp`` and executed
against the container's final state using the *same* system Python the agent
had. A recurring, purely-mechanical failure mode on HTTP-service tasks is that
the verifier module does ``import requests`` (the near-universal way to probe a
running HTTP endpoint) but ``requests`` is not preinstalled in some images.
pytest then aborts at *collection* time::

    ImportError while importing test module '/tmp/test_final_state.py'
    ModuleNotFoundError: No module named 'requests'
    Interrupted: 1 error during collection

The agent's work can be entirely correct (server built, listening, returning
the right body) and the task still scores 0 because the test file cannot even
be *collected*. The dependency is a property of the hidden verifier, never of
the task description, so the agent has no way to infer it.

Why the exit-intent-only guard was insufficient
------------------------------------------------
The prior ``VerifierDepGuardProcessor`` fired the dependency-ensure only at
*exit intent* (``finish_reason=end_turn/stop`` with no tool call). But the
exact tasks that need it most — long HTTP-service builds — routinely exhaust
the step budget (``exit_reason=budget_exceeded`` at the step cap) and therefore
**never reach exit intent**. On this benchmark three separate HTTP-service
tasks failed at verifier *collection* with ``ModuleNotFoundError: No module
named 'requests'`` while sitting at ``exit_reason=budget_exceeded, steps=80``;
the exit-intent guard never fired on any of them (confirmed: the dep-check
banner appears zero times in their transcripts). ``pip`` reaches PyPI in this
environment (an unrelated task installed flask + its full dependency chain from
PyPI at full speed), so the install itself is available — it simply never runs.

What this processor does
------------------------
It ensures the dependency-ensure runs **exactly once per task**, whichever of
two triggers fires first:

1. **Proactive** — the first model turn at or after ``proactive_step`` that
   emits at least one tool call. The ensure command is *prepended* to that
   turn's tool calls as an additional real ``Bash`` call (the run loop executes
   every tool call in a turn), so it runs long before the step budget can be
   exhausted. This covers ``budget_exceeded`` runs that never exit cleanly.
2. **Exit-intent fallback** — if the task finishes cleanly before
   ``proactive_step`` (short tasks), the ensure is injected as a standalone
   ``Bash`` call on the first no-tool-call exit turn, preserving the original
   guard's behaviour.

The ensure command checks whether each module already imports and only then
attempts a quiet ``pip install``; everything is guarded with ``|| true`` so the
call can never fail the run and is a no-op when the packages are already
present (the overwhelmingly common case — passing tasks are unaffected).

Generality
----------
The allow-list contains only ubiquitous *verification* libraries used by
external test harnesses to exercise a solution (HTTP client, config parser) —
no task ids, ports, paths, or process names. It closes a *class* of failures
("verifier imports a common test lib absent from the image"), not one task.
Installing an already-present package is a no-op; it fires at most once per run
and adds at most one Bash round-trip.
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
    "Continue with your work and finish when the task is genuinely complete."
)


class ProactiveVerifierDepGuardProcessor(MultiHookProcessor):
    """Ensure common verifier test-deps are importable, fired proactively.

    Fires the dependency-ensure exactly once per task, whichever comes first:
    a proactive prepend onto a mid-run tool-call turn (covers budget_exceeded
    runs), or a standalone exit-intent injection (covers short clean runs).

    Args:
        proactive_step: the first ``step_id`` at/after which the ensure is
            prepended to a tool-call turn. Small enough to run well before the
            step cap, large enough to let the agent survey the task first.
        deps: optional override of the ``(module, pip_pkg)`` allow-list.
    """

    _singleton_group = "tb2_verifier_dep_guard"
    _order = 95  # after the TB2 self-verify processor (order 90)

    def __init__(
        self,
        proactive_step: int = 4,
        deps: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        self._deps = tuple(tuple(d) for d in deps) if deps else _DEFAULT_DEPS
        self._proactive_step = max(1, int(proactive_step))
        self._done = False
        self._pending_message = ""

    def _reset(self) -> None:
        self._done = False
        self._pending_message = ""

    def _ensure_call(self) -> ToolCall:
        return ToolCall(
            id=f"vdg-{uuid.uuid4().hex[:8]}",
            name="Bash",
            input={"command": _build_ensure_cmd(self._deps)},
        )

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        if self._done:
            yield event
            return

        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )

        # Proactive path: mid-run turn that already carries tool calls. Prepend
        # our ensure call so it executes alongside the model's own call. The run
        # loop executes every tool call in a turn, so the model's work proceeds.
        if event.tool_calls and event.step_id >= self._proactive_step:
            self._done = True
            yield dataclasses.replace(
                event,
                tool_calls=(self._ensure_call(),) + tuple(event.tool_calls),
            )
            return

        # Exit-intent fallback: short task finished before proactive_step.
        if exit_intent:
            self._done = True
            self._pending_message = _ACK_MSG
            yield dataclasses.replace(event, tool_calls=(self._ensure_call(),))
            return

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
        self._reset()
        yield event
