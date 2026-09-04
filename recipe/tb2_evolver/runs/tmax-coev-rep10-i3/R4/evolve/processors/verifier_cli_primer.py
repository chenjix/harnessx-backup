"""VerifierCliPrimerProcessor — ensure the CLI tools the external verifier
invokes via ``subprocess`` are present in the container.

## Why this is a harness deficiency, not a capability gap

This benchmark runs the *task agent* and the *verifier* in separate phases
against the same container. The verifier's pytest module is injected **after**
the agent exits. A recurring, non-recoverable failure shape is a verifier test
that shells out to a standard command-line tool with
``subprocess.run(["curl", ...])`` / ``subprocess.run(["ss", ...])`` and the
binary is simply **not installed** in the base image:

    E   FileNotFoundError: [Errno 2] No such file or directory: 'curl'
    E   FileNotFoundError: [Errno 2] No such file or directory: 'ss'

When that happens the test raises at call time and the task scores 0
**regardless of whether the agent's solution is correct**. The agent itself
cannot know the verifier will reach for these tools — during the agent phase
it hits ``curl: command not found`` and simply works around it (wget / python
stdlib), so it never installs the CLI client. That is an environment gap the
harness can close, exactly analogous to the accepted ``requests`` pip primer,
just for the OS-level CLI layer instead of the Python-import layer.

## Mechanism

Deterministically inject a single, quiet, idempotent, time-boxed install of a
small generic set of standard networking CLI tools on the first model turn of
every task. The command:

  * is a fast no-op when every tool is already present (``command -v`` guard),
  * is wrapped in ``timeout`` so a blocked/slow package index can never stall
    the run,
  * suppresses its own output and ends in ``|| true`` so it can never fail the
    turn or corrupt an otherwise-correct solution,
  * only touches system package state / ``/usr/bin`` — never a task output
    path,
  * persists in the container into the verifier phase.

This is general environment hardening for the class of service / networking
tasks whose verifier probes state with a shell CLI; it is not keyed to any
task id, path, or literal drawn from the training trajectories.

The processor fires **exactly once per task**, appending its install call to
whatever the model emits on its first turn, so it never suppresses the model's
own first action.
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

# Small, generic set of standard networking CLI tools that external test
# harnesses commonly shell out to. ``iproute2`` provides ``ss``. Nothing here
# is task-specific — these are the ubiquitous probes a networking verifier
# reaches for.
_DEFAULT_APT_PACKAGES = "curl iproute2"

# Overall wall-clock cap for the whole install attempt. If the package index
# is unreachable the attempt is abandoned quickly and the turn proceeds.
_INSTALL_TIMEOUT_S = 90


def _build_cmd(apt_packages: str) -> str:
    # Map apt package names to the binaries we actually need to guarantee, so
    # the guard can short-circuit when they are already present.
    #   curl     -> curl
    #   iproute2 -> ss
    # The guard treats "all required binaries already resolvable" as a no-op.
    return (
        # Fast idempotent guard: if both probes resolve, do nothing.
        "if command -v curl >/dev/null 2>&1 && command -v ss >/dev/null 2>&1; "
        "then true; else "
        # Time-boxed, quiet, non-fatal best-effort install. apt-get update is
        # needed before install on Debian/Ubuntu base images.
        f"timeout {_INSTALL_TIMEOUT_S} sh -c "
        f"'apt-get update >/dev/null 2>&1; "
        f"DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        f"{apt_packages} >/dev/null 2>&1' || true; "
        "fi; true"
    )


class VerifierCliPrimerProcessor(MultiHookProcessor):
    """Inject a one-shot, idempotent install of standard networking CLI tools
    (``curl``, ``ss``) so a post-agent verifier that shells out to them does
    not die with ``FileNotFoundError`` before it can assert on correct work.

    Fires at most once per task, on the first model response.
    """

    _singleton_group = "verifier_cli_primer"
    # Sit just after the pip dep primer (_order=32) so the two environment
    # hardeners are adjacent and both run late, out of the way of the
    # message-shaping processors.
    _order = 33

    def __init__(self, apt_packages: str | None = None) -> None:
        self.apt_packages = apt_packages or _DEFAULT_APT_PACKAGES
        self._cmd = _build_cmd(self.apt_packages)
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
            id=f"vcp-{uuid.uuid4().hex[:8]}",
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
