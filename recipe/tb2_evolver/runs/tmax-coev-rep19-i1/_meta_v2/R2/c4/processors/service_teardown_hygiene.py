# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ServiceTeardownHygieneProcessor — final process-state hygiene reminder.

Closes a systemic TB2/Tmax failure class: the agent starts a long-running
background service (a daemon, an HTTP server, an init/supervisor script) while
testing its own solution, then declares the task done without reconciling the
final process set. Many service-lifecycle tasks are graded on *final state* —
e.g. a verifier that asserts ``pgrep -f <service>`` returns nothing (no
lingering processes) after the agent exits, or conversely that exactly the
intended process is still up. The agent's functional output can be entirely
correct and the task still scores 0 because orphaned processes remain.

Concretely observed (task_000140_01c78b42): the agent fixed a Go service,
its supervisor script, and a CI pipeline; running the pipeline produced the
required log line, so the content was right. But it ran ``./vm_service &`` via
its test pipeline and never verified the process set before exiting; the
grader found three lingering ``vm_service`` processes and failed
``test_no_lingering_service_processes``.

Design — why this is a *harness* fix, not a prompt patch:

* The gap is conditional and mechanically observable: the harness can see
  from the Bash command stream whether the agent actually launched a
  background service this run. A static prompt rule ("always clean up
  processes") would fire on every task — inflating tokens and risking that
  the agent kills a service the task *requires* to keep running.
* The reminder is emitted exactly once, only when (a) a background launch was
  observed AND (b) the existing ``_tb2_self_verify`` checkpoint has fired
  (the agent's exit-verification pass). It rides that one-shot flow by
  appending to the self-verify tool result — a contract-safe mutation that
  only augments ``event.result`` and never inserts a message or hijacks
  exit-intent, so it cannot conflict with ``CustomSelfVerifyProcessor``.
* The text is advisory and non-coercive: it tells the agent to *reconcile*
  the final process set — reap test-only survivors, or, if the task requires
  the service to keep running, leave exactly the intended process(es) up. It
  names no task, service, path, PID, or command.

Content-agnostic: the background-launch detection keys on generic shell
patterns (trailing ``&`` backgrounding, ``nohup``, ``setsid``, ``disown``,
``systemctl start`` / ``service ... start``). No task-specific literals.
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

# The self-verify tool name injected by benchmarks.terminal_bench_2.harness.
# We ride its one-shot exit-verification flow rather than hijacking exit-intent
# ourselves. Kept as a parameter so this processor is not hard-coupled.
_DEFAULT_SELF_VERIFY_TOOL = "_tb2_self_verify"

# Generic patterns that indicate a *long-running* / backgrounded process was
# started. Deliberately conservative: we want a launch that plausibly outlives
# the command, not every use of '&' in shell logic.
_BG_PATTERNS = (
    # a command backgrounded with a trailing '&' (not '&&')
    re.compile(r"(?<![&\d>])&\s*(?:$|[#\n)])", re.MULTILINE),
    re.compile(r"\bnohup\b"),
    re.compile(r"\bsetsid\b"),
    re.compile(r"\bdisown\b"),
    re.compile(r"\bsystemctl\s+start\b"),
    re.compile(r"\bservice\s+\S+\s+start\b"),
)


def _looks_like_background_launch(command: str) -> bool:
    if not command:
        return False
    for pat in _BG_PATTERNS:
        if pat.search(command):
            return True
    return False


_HYGIENE_REMINDER = (
    "\n\n[ServiceHygiene] You started one or more background/long-running "
    "processes during this task (a server, daemon, or service/supervisor "
    "script). Graders often check the FINAL process state after you exit. "
    "Before finishing, reconcile it explicitly:\n"
    "  1. List what is still running that you started, e.g. "
    "`ps -ef | grep -i <service>` or `pgrep -af <service>`.\n"
    "  2. If those processes were only started to TEST your solution, stop "
    "them cleanly and confirm they are gone (send the signal, then re-check "
    "with `pgrep`; a single `kill` is asynchronous and may leave survivors — "
    "kill the whole process group / all matching PIDs and verify none "
    "remain).\n"
    "  3. If the task instead requires a service to keep running after you "
    "exit, verify that EXACTLY the intended process(es) are up and no stray "
    "duplicates from earlier test runs are left behind.\n"
    "Do not assume a script that 'ran without errors' left a clean process "
    "table — check it."
)


class ServiceTeardownHygieneProcessor(MultiHookProcessor):
    """Remind the agent to reconcile final process state when it exits.

    Fires at most once per task, and only when both hold:
      * a background/long-running process launch was observed in the Bash
        command stream this run, and
      * the self-verify checkpoint tool result flows through ``on_after_tool``
        (the agent's exit-verification pass).

    Parameters
    ----------
    tool_name:
        Which tool carries the shell commands to inspect (TB2/Tmax expose
        only ``Bash``).
    self_verify_tool:
        Name of the self-verify checkpoint tool whose result we augment.
    """

    _singleton_group = "service_teardown_hygiene"
    _order = 91  # just after CustomSelfVerifyProcessor (90); augments its result

    def __init__(
        self,
        tool_name: str = "Bash",
        self_verify_tool: str = _DEFAULT_SELF_VERIFY_TOOL,
    ) -> None:
        self.tool_name = tool_name
        self.self_verify_tool = self_verify_tool
        self._bg_launched: bool = False
        self._reminded: bool = False

    async def on_task_start(self, event: TaskStartEvent):
        self._bg_launched = False
        self._reminded = False
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == self.tool_name:
            command = event.tool_input.get("command", "") if event.tool_input else ""
            if _looks_like_background_launch(command):
                self._bg_launched = True
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        # Ride the one-shot self-verify checkpoint: append the hygiene reminder
        # to its result the first time it fires, iff a background launch was
        # seen. Never mutate any other tool's result.
        if (
            event.tool_name == self.self_verify_tool
            and self._bg_launched
            and not self._reminded
        ):
            self._reminded = True
            yield dataclasses.replace(
                event, result=(event.result or "") + _HYGIENE_REMINDER
            )
            return
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._bg_launched = False
        self._reminded = False
        yield event
