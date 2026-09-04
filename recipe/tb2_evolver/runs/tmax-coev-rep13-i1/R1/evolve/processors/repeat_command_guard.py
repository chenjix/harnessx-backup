# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatCommandGuard — break exact-identical Bash command loops.

Closes a systemic failure mode observed on the tmax/TB2 task set: an agent
that has stalled keeps re-issuing the *same* Bash command verbatim (a query
that returns "no output captured", a build that keeps failing, a cat of a
file that never changes). The existing recovery processors miss this shape:

* ``LengthTruncationRecoveryProcessor`` only fires on ``finish_reason=length``
  (a token-limit runaway), not on ordinary tool loops.
* ``CustomEditToolProcessor`` only counts *file writes* to the same path, so a
  read/query/build command repeated verbatim slides straight past it.

Both leave the exact-repeat-command loop untouched, and every one of the
budget_exceeded failures in the baseline round burned all 80 steps re-running
a handful of identical commands while narrating "I'm stuck in a loop."

This processor keys on the *exact normalised command string*. It only fires
when the identical command has been run at least ``threshold`` times, so
healthy runs that iterate through many distinct commands (the passing
high-step tasks) are never touched — they never repeat a single command that
often. On each further repeat past the threshold the injected message
escalates: first a redirect ("this exact command produced the same result;
change the command or the approach"), then a hard stop that tells the agent to
either write the required output or take a genuinely different action.

Purely additive: appends a bracketed advisory to the tool result. It never
blocks execution and never mutates message history, so it cannot violate the
message contract.
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

_REDIRECT = (
    "\n\n[RepeatCommandGuard] You have now run this EXACT command {count} times "
    "and it keeps producing the same result. Repeating it again will not change "
    "anything. Do NOT run it a {nextcount}th time. Instead, either (a) change the "
    "command itself (different flags, a smaller diagnostic step, inspect the "
    "inputs it depends on), or (b) change your approach to the task entirely. "
    "State in one sentence what specifically was wrong with the previous attempts "
    "before your next tool call."
)

_HARD_STOP = (
    "\n\n[RepeatCommandGuard] STOP — this identical command has now been executed "
    "{count} times with no change in outcome. You are in a loop that is wasting "
    "your step budget and will end the task with a score of 0 if it continues. "
    "Abandon this exact command. Take ONE of these actions now: (a) if the "
    "required output file(s) can be written from what you already know, write "
    "them to the exact path named in the task; (b) otherwise run a DIFFERENT, "
    "concrete diagnostic command that tests a new hypothesis about why the "
    "previous attempts failed. Never issue the same failing command again."
)


def _normalise(cmd: str) -> str:
    """Collapse whitespace so trivially-reformatted repeats still match."""
    return " ".join(cmd.split())


class RepeatCommandGuard(MultiHookProcessor):
    """Detect an exact-identical Bash command loop and escalate a break-out nudge."""

    _singleton_group = "tb2_repeat_command_guard"
    _order = 31  # right after CustomEditToolProcessor (_order=30)

    def __init__(self, threshold: int = 3, hard_stop_extra: int = 2) -> None:
        # threshold: first advisory fires on the Nth identical execution.
        self.threshold = max(2, int(threshold))
        # hard_stop_extra: after this many repeats past threshold, escalate to STOP.
        self.hard_stop_extra = max(1, int(hard_stop_extra))
        self._counts: dict[str, int] = {}
        self._pending: dict[str, str] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._counts.clear()
        self._pending.clear()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            cmd = _normalise(str((event.tool_input or {}).get("command", "")))
            if cmd:
                count = self._counts.get(cmd, 0) + 1
                self._counts[cmd] = count
                self._pending[event.tool_call_id] = cmd
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        cmd = self._pending.pop(event.tool_call_id, None)
        if cmd is None:
            yield event
            return
        count = self._counts.get(cmd, 0)
        if count < self.threshold:
            yield event
            return
        if count >= self.threshold + self.hard_stop_extra:
            msg = _HARD_STOP.format(count=count)
        else:
            msg = _REDIRECT.format(count=count, nextcount=count + 1)
        yield dataclasses.replace(event, result=(event.result or "") + msg)

    async def on_task_end(self, event: TaskEndEvent):
        self._counts.clear()
        self._pending.clear()
        yield event
