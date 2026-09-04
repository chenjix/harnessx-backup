# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandGuard — break identical-Bash-command thrash loops.

TB2 exposes only ``Bash``.  A recurring pathological failure shape in the
R0 trajectories is the agent re-issuing the *exact same* Bash command many
times in a single run without changing state or making progress
(e.g. ``debugfs ... ls -l / ... EOF`` run 17x, a ``sleep; ps aux | grep``
poll run 10x, a ``pytest`` invocation run 10x).  These runs burn the entire
step budget (``exit_reason=budget_exceeded``) or stall out with no
deliverable.

The existing ``CustomEditToolProcessor`` only counts *file-write* commands
(redirects / ``sed -i`` / ``tee``); it does not see read/poll/debug commands
that produce the same output every time.  This processor closes that gap: it
counts how many times each *exact* command string has been executed in the
current task and, once a command crosses ``threshold`` executions, appends a
one-shot nudge to that tool's result telling the agent the command is not
changing state and to try a fundamentally different approach.  The per-command
counter is then reset so the guard can fire again if the loop persists.

Design choices to protect already-passing runs:
- **Exact-match only.** Only literally identical command strings are counted.
  Iterative debugging (each attempt slightly different) is not penalised.
- **High threshold.** Default 5 identical executions — well above normal
  retry/poll behaviour observed in passing runs, so benign short polls are
  untouched.
- **Nudge, not block.** The command still runs and its real output is
  preserved; the guard only *appends* advisory text. It never drops or
  rewrites messages, so it cannot regress a run that was going to succeed.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


_REPEAT_WARN = (
    "\n\n[RepeatedCommandGuard] You have now run this exact command "
    "{count} times in this task and its output is not advancing you toward "
    "the goal. Repeating an identical command does not change the system "
    "state. STOP re-running it. Instead: (1) re-read the task requirements, "
    "(2) diagnose *why* the result is unchanged (wrong path, missing "
    "dependency, a process that never started, a flawed assumption), and "
    "(3) take a fundamentally different action. If you believe the work is "
    "already done, verify each required output file with `ls -lh` and `cat` "
    "instead of re-running this command."
)


class RepeatedCommandGuard(MultiHookProcessor):
    """Nudge the model when it re-issues the same Bash command too many times."""

    _singleton_group = "repeated_command_guard"
    _order = 31  # right after CustomEditToolProcessor (_order=30)

    def __init__(self, threshold: int = 5) -> None:
        self.threshold = int(threshold)
        self._counts: dict[str, int] = {}
        self._pending: dict[str, str] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._counts.clear()
        self._pending.clear()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            cmd = (event.tool_input or {}).get("command", "")
            if isinstance(cmd, str):
                norm = cmd.strip()
                if norm:
                    self._pending[event.tool_call_id] = norm
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        norm = self._pending.pop(event.tool_call_id, None)
        if norm is None:
            yield event
            return
        count = self._counts.get(norm, 0) + 1
        self._counts[norm] = count
        if count >= self.threshold:
            self._counts[norm] = 0  # reset so the guard can re-fire if loop persists
            warn = _REPEAT_WARN.format(count=count)
            yield dataclasses.replace(event, result=(event.result or "") + warn)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._counts.clear()
        self._pending.clear()
        yield event
