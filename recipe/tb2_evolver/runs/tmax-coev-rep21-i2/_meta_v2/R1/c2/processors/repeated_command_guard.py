# SPDX-License-Identifier: MIT
"""RepeatedCommandGuard — break identical-command loops in TB2.

TB2 exposes only ``Bash``. A recurring failure mode on the Qwen3.5-4B
task agent is a *command-level loop*: the model issues a byte-identical
Bash command several times in a row, receives byte-identical output
(often the same error), narrates "I'm stuck in a loop, let me try a
different approach", and then re-issues the exact same command anyway —
until the step / time budget is exhausted (``exit_reason=budget_exceeded``).

The existing ``CustomEditToolProcessor`` only counts *file-write*
commands (redirect / ``sed -i`` / ``tee``) with a threshold of 7, so
pure read/exec loops (``tesseract ...``, ``python -c "help(...)"``,
no-op ``sed``) are never caught, and 7 repeats is well past the point
where the loop is already fatal. ``LengthTruncationRecoveryProcessor``
only fires on ``finish_reason=length``.

This processor closes that gap generically:

* it tracks the *last executed* normalised Bash command and a run
  counter of consecutive identical commands;
* when the same command is issued ``repeat_threshold`` times in a row
  it appends an **escalating advisory** to the tool result (it never
  blocks or kills the command — a genuine retry still runs);
* the message escalates each subsequent repeat so the model is not
  copying identical context, and lists concrete alternative moves;
* counters reset per task and whenever the command actually changes.

The intervention is purely additive text on the tool result, so it is
contract-safe (it does not touch ``event.messages`` and never removes
the model's context).
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

_WS_RE = re.compile(r"\s+")


def _normalise(cmd: str) -> str:
    """Collapse whitespace so trivially-reformatted repeats still match."""
    return _WS_RE.sub(" ", (cmd or "").strip())


_LOOP_WARN_FIRST = (
    "\n\n[LoopGuard] You have now run this exact command {n} times in a row and "
    "gotten the same result each time. Repeating it again will not change the "
    "outcome. STOP and diagnose: read the actual error/output above carefully, "
    "then change your APPROACH — not just retype the same command. Concretely: "
    "inspect the relevant file with `cat`/`sed -n`, test a smaller isolated "
    "piece, verify your assumptions about paths and variable names, or try a "
    "fundamentally different tool/method."
)
_LOOP_WARN_ESCALATED = (
    "\n\n[LoopGuard] CRITICAL: you are STILL repeating the identical command "
    "(now {n} times) and burning your budget with zero progress. Do NOT run "
    "this command again in any form. Take a completely different action THIS "
    "turn: if you are debugging code, print/inspect the intermediate state that "
    "the error points at; if a tool keeps failing, switch to another tool or "
    "method entirely. If part of the task is already blocked, MOVE ON and "
    "complete the other required deliverables so they are not left unfinished."
)


class RepeatedCommandGuard(MultiHookProcessor):
    """Detect consecutive identical Bash commands and inject a loop-break nudge.

    Parameters
    ----------
    repeat_threshold:
        Number of consecutive identical commands that triggers the first
        advisory (default 3 — sits above benign 1-2 re-runs).
    require_identical_output:
        When True (default), only warn if the tool output is also identical
        to the previous run of the same command — a command whose output is
        changing may still be making progress. When False, warn purely on
        command repetition.
    max_track_chars:
        Cap on the command/output length compared, to bound memory.
    """

    _singleton_group = "repeated_command_guard"
    # Fire after EditDetection (30) so both warnings compose cleanly.
    _order = 32

    def __init__(
        self,
        repeat_threshold: int = 3,
        require_identical_output: bool = True,
        max_track_chars: int = 4000,
    ) -> None:
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.require_identical_output = bool(require_identical_output)
        self.max_track_chars = int(max_track_chars)
        self._last_cmd: str | None = None
        self._run_len: int = 0
        self._warn_count: int = 0
        self._pending: dict[str, str] = {}

    def _reset(self) -> None:
        self._last_cmd = None
        self._run_len = 0
        self._warn_count = 0
        self._pending.clear()

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            cmd = _normalise(event.tool_input.get("command", ""))[: self.max_track_chars]
            self._pending[event.tool_call_id] = cmd
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        cmd = self._pending.pop(event.tool_call_id, None)
        if cmd is None:
            yield event
            return

        if cmd and cmd == self._last_cmd:
            self._run_len += 1
        else:
            self._last_cmd = cmd
            self._run_len = 1
            self._warn_count = 0

        result = event.result or ""

        # Optionally gate on identical output: if the output changed, the
        # command may still be making progress, so don't nag.
        if self.require_identical_output:
            cur_out = result[: self.max_track_chars]
            prev_out = getattr(self, "_last_out", None)
            self._last_out = cur_out
            if self._run_len >= 2 and cur_out != prev_out:
                # Output changed -> treat as fresh progress, damp the counter.
                self._run_len = 1
                self._warn_count = 0
                yield event
                return

        if self._run_len >= self.repeat_threshold:
            self._warn_count += 1
            if self._warn_count == 1:
                warn = _LOOP_WARN_FIRST.format(n=self._run_len)
            else:
                warn = _LOOP_WARN_ESCALATED.format(n=self._run_len)
            yield dataclasses.replace(event, result=result + warn)
            return

        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
