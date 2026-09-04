# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""CleanSlateSelfTestReminder — de-contaminate the agent's self-test loop.

Systemic failure mode this closes
----------------------------------
Tasks that ask the agent to build a background process / daemon / network
service (a disk-quota monitor, a health monitor, a port-forwarder, a reverse
proxy, an HTTP server, ...) are verified by an *external* verifier that starts
the agent's artifact from a **cold, clean process/port state** — no leftover
processes, freed ports, fresh log directory.

During the agent phase the agent typically launches its solution repeatedly to
self-test. Because TB2 gives no process cleanup between iterations, stale
background processes and held listen-ports accumulate across those iterations.
This poisons the agent's own feedback in two symmetric ways, both observed
across distinct tasks in this benchmark:

  * FALSE POSITIVE — a *previous* iteration's still-running daemon (started when
    the workload happened to be present) keeps doing the job, so the agent's
    latest, actually-broken script "appears to work" and the agent commits it.
    (system_administration disk-quota monitor: stale `python3` monitors made a
    monitor with an immediate-exit startup bug look correct; verifier cold-start
    then measured 179 MB > 45 MB and failed.)

  * SPURIOUS FAILURE / THRASH — a stale process holds the listen port, so every
    fresh launch dies on `Address already in use`; the agent chases a phantom
    bug for dozens of steps and runs out of budget.
    (Kubernetes operator / proxy tasks: repeated `bind ... Address already in
    use` on the forward port; provisioner/health-monitor tasks: piles of
    `<defunct>` python/socat processes.)

The harness deficiency is that nothing tells the agent its self-test
environment is DIRTY and unlike the verifier's cold start. This processor
injects a ONE-TIME, generic reminder the first time the agent launches a
background process, describing the clean-slate self-test discipline. It injects
strategy, never a solution: no task ids, ports, paths, thresholds, or code.

Mechanics / contract
--------------------
* ``on_before_tool`` records the ``tool_call_id`` of the first Bash command
  that launches a background process (trailing ``&``, ``nohup``/``setsid``/
  ``disown``, or a known long-running service launcher).
* ``on_after_tool`` appends the reminder to *that* call's result string and
  disarms. Fires <= 1x per task. Mutates only ``event.result`` (same shape as
  CustomEditToolProcessor) — no message insert / drop / reorder, so it is
  contract-clean.
* Non-background tasks never see it: if the agent launches nothing in the
  background the reminder never fires.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


# A command "launches a background process" if it either backgrounds something
# with the shell (trailing & / nohup / setsid / disown) or invokes a launcher
# that is characteristically a long-running listener/daemon. These are generic
# shell/service idioms, not task-specific literals.
_TRAILING_BG_RE = re.compile(r"&\s*$|&\s*(?:#.*)?$|&\s*\n", re.MULTILINE)
_BG_KEYWORD_RE = re.compile(
    r"\b(?:nohup|setsid|disown)\b"
    r"|\bsocat\b"
    r"|\buvicorn\b|\bgunicorn\b|\bhypercorn\b"
    r"|\bhttp\.server\b"
    r"|(?:flask|django|rails)\s+run"
    r"|\bstart[_-]?(?:service|server|daemon|monitor)\b",
    re.IGNORECASE,
)


def _launches_background(command: str) -> bool:
    if not command:
        return False
    # A single trailing '&' on any line (job control) is the strongest signal.
    for line in command.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Ignore logical-and '&&'; only bare '&' backgrounds a job.
        no_and = re.sub(r"&&", "", stripped)
        if re.search(r"&\s*$", no_and):
            return True
    return bool(_BG_KEYWORD_RE.search(command))


_CLEAN_SLATE_REMINDER = (
    "\n\n[SelfTestHygiene] You just launched a process in the background. Note that the "
    "grader will start your artifact from a COLD, CLEAN state — no processes you left "
    "running, all ports free, and log/output directories fresh — and BEFORE the workload it "
    "is meant to act on exists. Your own repeated self-tests do NOT reset this state between "
    "runs, so leftover background processes or held listen-ports can silently make a broken "
    "solution look correct (an earlier run keeps doing the job) OR make a correct one look "
    "broken ('address already in use', stale/defunct processes). Before EACH self-test: "
    "(1) kill every background process you started (e.g. pkill/kill the exact ones you "
    "launched, then confirm with ps that none remain) and free any port you bound; "
    "(2) reset the working/output directory to its initial state; (3) then start your "
    "artifact fresh and, if the grader starts it before the workload, make sure it survives "
    "and keeps running through startup rather than exiting the instant it sees no work yet. "
    "Treat a self-test as valid only when it reproduces this cold-start ordering."
)


class CleanSlateSelfTestReminder(MultiHookProcessor):
    """One-time clean-slate self-test reminder on the first background launch."""

    _singleton_group = "clean_slate_self_test"
    _order = 33  # after CustomEditToolProcessor (30), before CustomSelfVerifyProcessor (90)

    def __init__(self) -> None:
        self._fired: bool = False
        self._armed_call_id: str | None = None

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._armed_call_id = None
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if (
            not self._fired
            and self._armed_call_id is None
            and event.tool_name == "Bash"
        ):
            command = event.tool_input.get("command", "") or ""
            if _launches_background(command):
                self._armed_call_id = event.tool_call_id
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if (
            not self._fired
            and self._armed_call_id is not None
            and event.tool_call_id == self._armed_call_id
        ):
            self._fired = True
            self._armed_call_id = None
            yield dataclasses.replace(
                event, result=(event.result or "") + _CLEAN_SLATE_REMINDER
            )
            return
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._armed_call_id = None
        yield event
