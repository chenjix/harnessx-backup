# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatCommandGuardProcessor — break degenerate near-identical command loops.

Closes a systemic Tmax/TB2 failure mode: the agent re-runs *essentially the
same* Bash command over and over (an OCR preprocessing pipeline, a build, a
test, a probe) getting the same result each time, making no progress. It keeps
going until it exhausts the step/budget cap or triggers the max_tokens
repetition loop, then commits a wrong answer or times out.

Evidence (round R0, tmax-coev-rep24-i1):
  * task_000015 — same tesseract-preprocessing snippet run 9x, all garbled;
    agent then guessed the output schema wrong -> reward 0.
  * task_001031 — same mpi4py test snippet run 19x -> budget_exceeded.
  * task_000028 — same ffprobe invocation run 14x.
  * task_000118 — same ps/ls polling loop.

The existing ``CustomEditToolProcessor`` only counts *writes* to a given file
path; it does not see repeated read/exec/build/test commands. This processor
complements it by keying on a *normalized command signature* across ALL Bash
calls, and nudges the agent to change strategy (not re-run) once the same
signature recurs ``threshold`` times.

Design notes:
  * Signature = whitespace-collapsed command string, so cosmetic edits
    (extra spaces, changed heredoc delimiter tail) still cluster. Heredoc
    *bodies* are stripped so re-emitting a slightly-edited file whose leading
    command is identical does not falsely cluster with unrelated commands;
    this keeps the guard conservative and write-agnostic.
  * Fires at most ``max_fires`` times total per task, and re-arms only after
    a *different* command runs, so a legitimately-repeated poll (e.g. waiting
    on a service) is warned once, not spammed every turn.
  * Injects into the tool RESULT (``on_after_tool``) so the nudge is adjacent
    to the repeated output the agent just saw — no message-count contract
    risk (it mutates ``event.result``, never ``event.messages``).
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
# Strip heredoc bodies: `<< 'TAG' ... TAG` / `<<TAG ... TAG`. Keeps the command
# head (the part that actually determines *what* is being run) and drops the
# file payload so file writes are compared by their command shell, not content.
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\s*\1\s*$", re.DOTALL | re.MULTILINE)

_NUDGE = (
    "\n\n[RepeatCommandGuard] You have now run essentially this same command "
    "{count} times and are getting the same kind of result each time. Repeating "
    "it again will not help. STOP re-running it. Instead do ONE of the following: "
    "(a) if the output above already contains the information you need, carefully "
    "re-read it and act on it — do not wait for a cleaner version that is not "
    "coming; (b) re-read the task description and confirm the exact required "
    "output format / file path / field names before writing anything; or (c) try "
    "a fundamentally different approach (different tool, different command, or "
    "commit your best-effort output now). Do not issue another near-identical "
    "command."
)


def _signature(command: str) -> str:
    """Normalize a Bash command into a coarse repetition signature."""
    if not command:
        return ""
    stripped = _HEREDOC_RE.sub("<<HEREDOC>>", command)
    return _WS_RE.sub(" ", stripped).strip()


class RepeatCommandGuardProcessor(MultiHookProcessor):
    """Nudge the agent off degenerate near-identical command loops."""

    _singleton_group = "tmax_repeat_command_guard"
    _order = 31  # runs just after CustomEditToolProcessor (30)

    def __init__(self, threshold: int = 3, max_fires: int = 3) -> None:
        self.threshold = max(2, int(threshold))
        self.max_fires = max(1, int(max_fires))
        self._counts: dict[str, int] = {}
        self._pending: dict[str, str] = {}
        self._last_sig: str = ""
        self._fires: int = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._counts.clear()
        self._pending.clear()
        self._last_sig = ""
        self._fires = 0
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            sig = _signature(event.tool_input.get("command", ""))
            if sig:
                self._pending[event.tool_call_id] = sig
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        sig = self._pending.pop(event.tool_call_id, "")
        if not sig:
            yield event
            return

        # A different command since the last warning re-arms the guard so a
        # legitimately-repeated poll gets warned once, not on every turn.
        if sig != self._last_sig:
            self._last_sig = sig

        count = self._counts.get(sig, 0) + 1
        self._counts[sig] = count

        if count >= self.threshold and self._fires < self.max_fires:
            self._fires += 1
            # Reset this signature's counter so the same command must recur
            # another full `threshold` times before re-warning.
            self._counts[sig] = 0
            nudge = _NUDGE.format(count=count)
            yield dataclasses.replace(event, result=(event.result or "") + nudge)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._counts.clear()
        self._pending.clear()
        self._last_sig = ""
        self._fires = 0
        yield event
