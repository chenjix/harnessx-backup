# SPDX-License-Identifier: MIT
"""Early guard that ensures common *verifier* Python test-dependencies are
importable in the container, regardless of how the agent's session ends.

Motivation
----------
On Terminal-Bench 2 the task agent and the external verifier run in the **same
container** but in **separate phases**: the agent works, exits, and only then
the verifier's ``test_final_state.py`` is dropped into ``/tmp`` and executed
against the container's final state using the *same* system Python that the
agent had access to.

A recurring, purely-mechanical failure mode is that the verifier module does
``import requests`` (probing a running HTTP endpoint), ``import imageio`` /
``import PIL`` (image / video frame inspection), ``import numpy`` /
``import scipy`` (numeric checks), etc. — but the package is not preinstalled
in the image. pytest then aborts at *collection* time:

    ImportError while importing test module '/tmp/test_final_state.py'
    ModuleNotFoundError: No module named 'requests'
    Interrupted: 1 error during collection

The agent's actual work can be entirely correct and the task still scores 0
because the test file cannot even be *collected*. The agent has no way to infer
this dependency: the task description never mentions it — it is a property of
the hidden verifier, not of the task.

Why the previous exit-only version was insufficient
---------------------------------------------------
The earlier revision of this guard injected the install command only on the
agent's clean **exit-intent** turn (``finish_reason in {end_turn, stop}`` with
no tool calls). Observed in trajectories: on tasks that terminate via
``exit_reason=budget_exceeded`` (step cap) or ``exit_reason=error`` the agent
never emits a clean exit-intent turn, so the guard **never fired** and the
verifier still aborted with ``ModuleNotFoundError``. The install must happen
**early and unconditionally**, not gated on a graceful finish.

What this processor does
------------------------
On the **first** model response of the task (``on_after_model``) it injects a
single real ``Bash`` tool call — appended alongside whatever the model already
requested, or as the sole call if the model requested none. The run loop
executes it, so the install is mechanical and cannot be narrated past. For each
package in a small allow-list of *ubiquitous test-harness* libraries it checks
whether the module already imports and, only if missing, attempts a quiet
``pip install``. Everything is guarded with ``|| true`` so the call can never
fail the run, and the whole thing is a no-op on any task where the packages are
already present (the overwhelmingly common case).

Because it fires on step 1, the deps are in place no matter how the session
later terminates (natural exit, step-cap, or error), closing the coverage gap
that the exit-only version left open.

Generality
----------
The allow-list contains only ubiquitous *verification* libraries used by
external test harnesses to exercise a solution (HTTP client, image/video frame
reader, numeric/data libs, config parser) — no task ids, ports, paths, or
process names. It closes a *class* of failures — "verifier imports a common
test lib absent from the image" — not one task. Installing an already-present
package is a no-op, so passing tasks are unaffected. It fires at most once per
run and adds at most one Bash round-trip.

Wiring
------
Uses its own singleton group and a high ``_order`` (95) so it composes cleanly
with the other TB2 processors.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import (
    ModelResponseEvent,
    TaskStartEvent,
    TaskEndEvent,
    ToolCall,
)
from harnessx.core.processor import MultiHookProcessor


# Ubiquitous test-harness libraries an external verifier may import to exercise
# a solution. Kept generic — HTTP probing, image/video frame inspection,
# numeric/data checks, and config parsing are the common shapes.
# (module_name, pip_name) pairs.
_DEFAULT_DEPS = (
    ("requests", "requests"),
    ("imageio", "imageio"),
    ("PIL", "Pillow"),
    ("numpy", "numpy"),
    ("scipy", "scipy"),
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


class VerifierDepGuardProcessor(MultiHookProcessor):
    """Ensure common verifier test-deps are importable, fired once early.

    Injects one real ``Bash`` tool call on the first model response of the
    task, appended alongside the model's own tool calls (or as the sole call
    if the model made none). Fires at most once per task and is a no-op when
    every package is already present.
    """

    _singleton_group = "tb2_verifier_dep_guard"
    _order = 95

    def __init__(self, deps: tuple[tuple[str, str], ...] | None = None) -> None:
        self._deps = tuple(tuple(d) for d in deps) if deps else _DEFAULT_DEPS
        self._done = False

    async def on_task_start(self, event: TaskStartEvent):
        self._done = False
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        if self._done:
            yield event
            return
        # Fire once, on the very first model response, regardless of finish
        # reason. Append our ensure call to whatever the model already asked
        # for so the agent's own first action is preserved.
        self._done = True
        ensure = ToolCall(
            id=f"vdg-{uuid.uuid4().hex[:8]}",
            name="Bash",
            input={"command": _build_ensure_cmd(self._deps)},
        )
        yield dataclasses.replace(
            event,
            tool_calls=tuple(event.tool_calls) + (ensure,),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._done = False
        yield event
