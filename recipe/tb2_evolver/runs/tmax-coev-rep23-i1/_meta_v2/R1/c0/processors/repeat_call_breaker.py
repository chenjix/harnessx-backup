# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedToolCallBreaker for Tmax (TB2-style, Bash-only) agents.

Closes a systemic failure mode observed across the round: the model issues
the *exact same tool call* and receives the *exact same output* over and over,
making zero progress, until the step/wall-clock budget is exhausted. The
existing pipeline has recovery for the ``finish_reason=length`` narration loop
(LengthTruncationRecoveryProcessor) and for repeated file edits
(CustomEditToolProcessor), but nothing that catches a plain
identical-command / identical-output spin loop. Compaction fires but the
agent resumes the identical call immediately afterwards.

Mechanism (no new tool, works purely on the existing Bash tool stream):

* ``on_after_tool`` builds a signature ``(tool_name, normalized_input,
  result)`` for each completed tool call and counts *consecutive* identical
  signatures.
* When the count reaches ``threshold``, a corrective note is appended to the
  tool result (exactly the injection style CustomEditToolProcessor uses) and
  the counter resets, so the nudge escalates but never floods.

The note is deliberately dual-purpose so it is safe for *already-finished*
tasks (which sometimes idle on a repeated status/``ls`` command) as well as
genuinely stuck tasks:
  - if the work is already complete → stop and end the turn (no regression);
  - if not → the identical command cannot make progress, so change approach.

Because it only appends text to a tool result it introduces no new message and
cannot violate the before_model insertion contract.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    ToolCallEvent,
    ToolResultEvent,
    TaskStartEvent,
    TaskEndEvent,
)
from harnessx.core.processor import MultiHookProcessor

_BREAK_NOTE = (
    "\n\n[harness note] You have now run the SAME command and received the SAME "
    "output {n} times in a row. Repeating it again cannot make progress. Do NOT "
    "issue this command again. Instead:\n"
    "  - If the task is already complete, verify the required output paths exist "
    "and then end your turn.\n"
    "  - Otherwise, STOP this approach. Diagnose why it is not working (e.g. the "
    "state genuinely cannot change with this method, a prior step failed, or you "
    "are targeting the wrong resource) and take a FUNDAMENTALLY different next "
    "action.\n"
)

_MAX_SIG_CHARS = 4000


def _signature(tool_name: str, cmd: str, result) -> str:
    """Stable signature for a completed tool call.

    Normalizes the command text and the result (whitespace-collapsed) so
    cosmetic differences do not defeat detection, while genuinely different
    commands / outputs reset the counter.
    """
    inp = " ".join(str(cmd or "").split())
    out = " ".join(str(result or "").split())
    sig = f"{tool_name}\x00{inp}\x00{out}"
    return sig[:_MAX_SIG_CHARS]


class RepeatedToolCallBreaker(MultiHookProcessor):
    """Break identical-command / identical-output spin loops early."""

    _singleton_group = "tmax_repeat_call_breaker"
    _order = 31  # right after CustomEditToolProcessor (_order=30)

    def __init__(self, threshold: int = 4) -> None:
        # threshold = number of consecutive identical (cmd, output) pairs that
        # triggers the corrective note. 4 leaves room for legitimate short
        # polling/retry while still cutting a spin loop well before it can
        # exhaust the budget.
        self.threshold = max(2, int(threshold))
        self._last_sig: str | None = None
        self._run: int = 0
        self._pending_cmd: dict[str, str] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._last_sig = None
        self._run = 0
        self._pending_cmd.clear()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # ToolResultEvent carries no tool_input, so stash the command text here
        # keyed by tool_call_id and fold it into the signature on_after_tool.
        ti = event.tool_input
        if isinstance(ti, dict):
            cmd = ti.get("command")
            cmd = cmd if cmd is not None else repr(sorted(ti.items()))
        else:
            cmd = str(ti)
        self._pending_cmd[event.tool_call_id] = str(cmd)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        cmd = self._pending_cmd.pop(event.tool_call_id, "")
        sig = _signature(event.tool_name, cmd, event.result)
        if sig == self._last_sig:
            self._run += 1
        else:
            self._last_sig = sig
            self._run = 1

        if self._run >= self.threshold:
            note = _BREAK_NOTE.format(n=self._run)
            self._run = 0  # reset so the note escalates but does not flood
            self._last_sig = None
            yield dataclasses.replace(event, result=(event.result or "") + note)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._last_sig = None
        self._run = 0
        self._pending_cmd.clear()
        yield event
