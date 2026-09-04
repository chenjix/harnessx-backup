# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""NoProgressRepeatGuard — break identical-command no-progress loops.

Closes a systemic failure mode observed across multiple Tmax/TB2 tasks: the
agent issues the *same* Bash command over and over, receiving the *same*
output every time, making zero progress until it burns the entire step
budget (``exit_reason=budget_exceeded``) or crashes (``exit_reason=error``).

Concrete shapes seen in trajectories:
* a ``cat << EOF | nc ...`` command repeated ~30 times, each returning
  ``(exit 0, no output captured)`` — the agent never noticed nothing changed.
* a large ``cat > file << EOF`` heredoc rewrite repeated ~40 times in a row.

The existing ``LengthTruncationRecoveryProcessor`` only fires when the model
hits ``finish_reason=length``; these loops have a normal finish reason and a
real (repeated) tool call, so nothing currently intercepts them.

Mechanism (mirrors ``CustomEditToolProcessor``'s ``on_before_tool`` /
``on_after_tool`` idiom):
  * capture the command in ``on_before_tool`` keyed by ``tool_call_id``;
  * in ``on_after_tool`` build a signature of (command, truncated-output) and
    track how many times the *same* signature repeats consecutively;
  * when it repeats ``repeat_threshold`` times in a row, append a corrective
    warning to that tool's result telling the agent to change approach;
  * escalate the message if the loop continues past ``escalate_threshold``.

Only touches ``event.result`` (never ``event.messages``), so it is
contract-safe by construction — the same pattern the in-tree edit-limit
guard uses.
"""

from __future__ import annotations

import dataclasses
import hashlib

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


_WARN = (
    "\n\n[NoProgressGuard] You have now run an IDENTICAL command and received an "
    "IDENTICAL result {n} times in a row. Repeating the same command will not "
    "change anything. STOP repeating it. Step back and reason about WHY it is "
    "not producing the expected effect: check that any background service is "
    "actually running and listening, inspect the real state (`ls -l`, `ps aux`, "
    "read the file you just wrote, check exit codes and stderr), or try a "
    "materially different approach. Do not re-issue this command unchanged."
)

_WARN_ESCALATE = (
    "\n\n[NoProgressGuard] CRITICAL: this exact command has now repeated {n} "
    "times with no change in output. You are stuck in a loop and wasting your "
    "step budget. Abandon this line of attack entirely. Run ONE diagnostic "
    "command to observe the current state of the filesystem / processes, then "
    "choose a genuinely different next action. If a required output file has "
    "not yet been written to the exact path named in the task, write it now."
)


def _sig(command: str, result: str) -> str:
    h = hashlib.sha1()
    h.update(command.encode("utf-8", "replace"))
    h.update(b"\x00")
    # Only hash a bounded prefix of the result so huge outputs are cheap and
    # incidental trailing differences don't accidentally mask an
    # otherwise-identical loop.
    h.update(result[:4000].encode("utf-8", "replace"))
    return h.hexdigest()


class NoProgressRepeatGuard(MultiHookProcessor):
    """Detect and interrupt identical-command / identical-output loops."""

    _singleton_group = "noprogress_repeat_guard"
    # After CustomEditToolProcessor (30); both append to tool results.
    _order = 31

    def __init__(
        self,
        repeat_threshold: int = 3,
        escalate_threshold: int = 6,
        tool_name: str = "Bash",
    ) -> None:
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.escalate_threshold = max(self.repeat_threshold + 1, int(escalate_threshold))
        self.tool_name = tool_name
        self._pending: dict[str, str] = {}
        self._last_sig: str | None = None
        self._run: int = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._pending.clear()
        self._last_sig = None
        self._run = 0
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == self.tool_name:
            command = ""
            ti = event.tool_input
            if isinstance(ti, dict):
                command = str(ti.get("command", "") or "")
            self._pending[event.tool_call_id] = command
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        command = self._pending.pop(event.tool_call_id, None)
        if event.tool_name != self.tool_name or command is None:
            yield event
            return

        if event.result is None:
            result = ""
        elif isinstance(event.result, str):
            result = event.result
        else:
            result = str(event.result)

        sig = _sig(command, result)

        if sig == self._last_sig:
            self._run += 1
        else:
            self._last_sig = sig
            self._run = 1

        if self._run >= self.escalate_threshold:
            warn = _WARN_ESCALATE.format(n=self._run)
        elif self._run >= self.repeat_threshold:
            warn = _WARN.format(n=self._run)
        else:
            warn = ""

        if warn:
            yield dataclasses.replace(event, result=(result or "") + warn)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._pending.clear()
        self._last_sig = None
        self._run = 0
        yield event
