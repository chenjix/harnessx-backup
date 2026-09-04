# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ShellCwdGuard — surface the non-persistent-shell trap at runtime.

Closes a silent, generalisable harness/mental-model mismatch observed on
Tmax/TB2 tasks. In this benchmark each ``Bash`` tool call is executed as a
*fresh* ``docker exec`` with the working directory reset to the agent
workspace path — there is NO persistent shell session. Consequently:

  * a ``cd /some/dir`` issued in one turn does NOT carry to the next turn;
  * a subsequent relative invocation such as ``./server`` or ``python
    app.py`` then fails with ``No such file or directory`` / ``command not
    found`` even though the file exists at its absolute path;
  * shell variables / ``export`` / activated venvs likewise do not persist.

A concrete failure: an agent compiled a binary to ``/app/server`` (absolute),
then repeatedly ran ``./server ...`` from a shell whose cwd was the workspace
root, getting ``bash: ./server: No such file or directory`` every time. It
never realised the shell was non-persistent, mis-attributed the error to
stale program output, and burned its whole budget — the backend never ran,
so the external verifier saw a 502 and scored 0.

The model treats the shell like an interactive terminal and cannot infer the
non-persistence from the error text alone (the error looks like a missing
file, not a cwd problem). This processor detects the signature at runtime and
appends a ONE-TIME corrective directive to the offending tool result (a
contract-safe mutation: it only augments ``event.result``, never inserts a
message). It names no task, path, binary, or command — a pure mechanism nudge
that is a no-op on runs that never hit the trap.
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

# Error signatures a non-persistent shell produces when a relative path or a
# previously-``cd``'d location is assumed to persist.
_ERR_RE = re.compile(
    r"(No such file or directory|command not found|"
    r"can'?t open file|cannot access|No such file)",
    re.IGNORECASE,
)

# The command was written assuming cwd/state persisted: a relative executable
# invocation (``./x``), a bare interpreter on a relative script, or reliance
# on a prior standalone ``cd`` (which does not carry over).
_REL_INVOKE_RE = re.compile(
    r"(^|[\s;&|])\./"  # ./something
    r"|(^|[\s;&|])(python3?|node|ruby|perl|bash|sh|java|\./?a\.out)\s+"
    r"(?!/)[\w.\-]+",  # interpreter on a non-absolute script path
    re.MULTILINE,
)

_STANDALONE_CD_RE = re.compile(r"(^|[\s;&|])cd\s+\S", re.MULTILINE)


def _looks_like_cwd_trap(command: str) -> bool:
    if not command:
        return False
    # A single command that already chains ``cd DIR && ...`` is fine — state
    # only needs to persist WITHIN one command, which it does. Only flag when
    # the command relies on a relative path WITHOUT chaining its own cd, or is
    # a standalone cd whose effect the agent may expect to persist.
    has_chained_cd = re.search(r"cd\s+\S+\s*(&&|;)", command) is not None
    if has_chained_cd:
        return False
    if _REL_INVOKE_RE.search(command):
        return True
    # standalone cd with nothing chained after it (agent likely expects the
    # directory change to stick for the next turn)
    if _STANDALONE_CD_RE.search(command) and "&&" not in command:
        return True
    return False


_NUDGE = (
    "\n\n[ShellCwdGuard] This looks like a non-persistent-shell problem, "
    "NOT a missing file. Each Bash call runs in a FRESH shell whose working "
    "directory is reset to the agent workspace root every turn — a `cd` from "
    "a previous turn, shell variables, `export`s, and activated environments "
    "do NOT carry over. A relative path like `./prog` or `python app.py` "
    "therefore resolves against the workspace root, not where you built/wrote "
    "it. Fix it by making each command self-contained:\n"
    "  * run executables/scripts by ABSOLUTE path (e.g. `/abs/dir/prog`), or\n"
    "  * chain the directory change inside the SAME command "
    "(`cd /abs/dir && ./prog ...`).\n"
    "Confirm the file exists (`ls -l /abs/path`) before assuming your code "
    "is wrong."
)


class ShellCwdGuard(MultiHookProcessor):
    """Detect the non-persistent-shell / cwd-reset trap and nudge once.

    Parameters
    ----------
    tool_name:
        Which tool to watch (TB2/Tmax only expose ``Bash``).
    max_fires:
        How many times to emit the nudge per task (default 2). Bounded so a
        genuinely-missing file that keeps erroring does not spam the context.
    """

    _singleton_group = "shell_cwd_guard"
    _order = 32  # after RepeatedCommandBreaker (31); only augments tool result

    def __init__(self, tool_name: str = "Bash", max_fires: int = 2) -> None:
        self.tool_name = tool_name
        self.max_fires = max(1, int(max_fires))
        self._fired = 0
        self._pending: dict[str, bool] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = 0
        self._pending.clear()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == self.tool_name:
            command = event.tool_input.get("command", "") if event.tool_input else ""
            self._pending[event.tool_call_id] = _looks_like_cwd_trap(command)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        suspect = self._pending.pop(event.tool_call_id, False)
        if not suspect or self._fired >= self.max_fires:
            yield event
            return
        result = event.result or ""
        if _ERR_RE.search(result):
            self._fired += 1
            yield dataclasses.replace(event, result=result + _NUDGE)
            return
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = 0
        self._pending.clear()
        yield event
