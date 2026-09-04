# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""BackgroundProcessPersistenceGuard for Tmax (and TB2-style) agents.

Closes a *structural* failure mode invisible in the task text: each Bash
tool call is dispatched as a fresh, independent ``docker exec bash -lc``
invocation. A process launched with a bare ``&`` (or ``nohup ... &``) in
one tool call is a child of *that* ``bash -lc`` and is torn down / reaped
when the exec session exits. It therefore does **not** survive into the
next tool call, so a follow-up ``ps``/``pgrep`` in a later call finds
nothing.

Observed thrash (task_000118 and similar): the agent starts a daemon with
``python3 monitor.py &``, then spends dozens of steps re-checking ``ps aux``
(always empty), re-launching, and re-narrating "the monitor isn't running"
until the step budget is exhausted (``exit_reason=budget_exceeded``). The
root cause is not a logic bug in the daemon — it is that background jobs do
not persist across separate tool-call shells, which the agent never infers
from the environment.

This processor watches Bash tool calls. When a command *launches* a
background job (a trailing ``&`` on a long-running command, or ``nohup``),
it appends a concise one-time advisory to that tool call's result
explaining the persistence boundary and the two correct patterns:
detach with ``setsid`` (+ redirect + ``disown``), or run the launcher and
the workload that depends on it inside a *single* Bash command. It fires at
most a small, bounded number of times per task so it cannot inflate token
cost or spam the transcript.

Lever: Control. It only mutates ``ToolResultEvent.result`` (``on_after_tool``);
it never touches ``event.messages``, so it is contract-neutral.
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

# A command that backgrounds a job: a trailing ``&`` (not ``&&``), or nohup.
# We deliberately look for the *launch* shape, not for any ``&`` anywhere.
_TRAILING_AMP = re.compile(r"(?<![&>])&\s*(?:#.*)?$", re.MULTILINE)
_NOHUP = re.compile(r"\bnohup\b")
_SETSID = re.compile(r"\bsetsid\b")
_DISOWN = re.compile(r"\bdisown\b")

_ADVISORY = (
    "\n\n[harness note] This Bash call backgrounded a process with '&' / "
    "'nohup'. Each Bash tool call runs in a SEPARATE, short-lived shell "
    "(docker exec bash -lc). A job started with a bare '&' is a child of THAT "
    "shell and is terminated/reaped when the call returns, so it will NOT be "
    "visible to a later 'ps'/'pgrep' in a subsequent tool call. If a later "
    "check shows the process missing, this is why — it is not a code bug in "
    "your script. Two reliable patterns: (1) fully detach so it outlives the "
    "call, e.g. 'setsid nohup <cmd> >/tmp/out.log 2>&1 < /dev/null & disown'; "
    "or (2) run the launcher AND the workload that depends on it inside ONE "
    "Bash command (start the daemon, then invoke the thing it must observe, "
    "then inspect results — all in the same call), since a single call shares "
    "one shell."
)


class BackgroundProcessPersistenceGuard(MultiHookProcessor):
    """Warn once when a Bash call backgrounds a job that won't persist.

    Args:
        tool_name: which tool to inspect (default ``Bash``).
        max_fires: max advisories per task (default 2) — enough to catch the
            first couple of naive launches without inflating token cost.
    """

    _singleton_group = "tmax_bg_persist_guard"
    _order = 6

    def __init__(self, tool_name: str = "Bash", max_fires: int = 2) -> None:
        self.tool_name = str(tool_name)
        self.max_fires = max(1, int(max_fires))
        self._fired: int = 0
        # Remembers, keyed by tool_call_id, whether the pending call backgrounded
        # a job. Populated in on_before_tool (which has tool_input), consumed in
        # on_after_tool (which does not).
        self._pending: dict[str, bool] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = 0
        self._pending = {}
        yield event

    @staticmethod
    def _backgrounds_job(cmd: str) -> bool:
        if not cmd:
            return False
        # Already detached properly — no need to warn.
        if _SETSID.search(cmd) or _DISOWN.search(cmd):
            return False
        if _NOHUP.search(cmd):
            return True
        return bool(_TRAILING_AMP.search(cmd))

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == self.tool_name:
            cmd = ""
            if isinstance(event.tool_input, dict):
                cmd = event.tool_input.get("command", "") or ""
            if isinstance(cmd, str) and self._backgrounds_job(cmd):
                self._pending[event.tool_call_id] = True
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        flagged = self._pending.pop(event.tool_call_id, False)
        if (
            not flagged
            or self._fired >= self.max_fires
            or event.tool_name != self.tool_name
            or event.error
        ):
            yield event
            return

        self._fired += 1
        yield dataclasses.replace(event, result=(event.result or "") + _ADVISORY)

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = 0
        self._pending = {}
        yield event
