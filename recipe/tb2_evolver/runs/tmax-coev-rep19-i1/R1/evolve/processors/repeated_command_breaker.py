# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandBreaker — break degenerate identical-command loops.

Closes a systemic failure mode observed across many TB2 / Tmax failures:
the agent issues the *same* Bash command (byte-identical after whitespace
normalisation) turn after turn, getting the same result each time, without
making progress. Two concrete shapes seen in trajectories:

* the agent re-writes the identical source file 30+ times ("cat > file
  << EOF" heredoc) while narrating "I've been stuck in a loop" but never
  actually changing the command — burns the entire step/time budget and
  exits ``budget_exceeded`` with reward 0;
* the agent re-runs an extraction command (e.g. OCR on an image) that
  keeps returning the same garbled/failed output ~7 times, then abandons
  the authoritative source and *fabricates* a guess — producing output
  that fails the verifier's exact-match check.

The existing ``CustomEditToolProcessor`` counts *file edits* (parsed from
redirect targets) and emits a soft advisory, but (a) it mis-parses the
"file" out of heredoc commands, (b) it only fires on write commands, and
(c) it does not detect that the command is *byte-identical* — a
materially-changed rewrite of the same file is legitimate iteration,
whereas re-issuing the identical bytes is a pure loop. This processor
targets the identical-command loop directly and escalates a directive
intervention appended to the tool result (a contract-safe mutation: it
only augments ``event.result``, never inserts messages).

Generalisation: this is command-agnostic. It keys on a normalised hash of
whatever command was run, so it fires for OCR loops, compile loops, file
rewrites, SQL rewrites, curl loops — any class of task where the model
gets stuck repeating itself. No task-specific literals.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

_WS_RE = re.compile(r"\s+")


def _normalise(command: str) -> str:
    """Collapse whitespace so trivially-reformatted duplicates still match.

    Two commands that differ only in indentation / trailing whitespace are
    treated as the same command — the model has not made a material change.
    """
    return _WS_RE.sub(" ", command.strip())


_WARN = (
    "\n\n[RepeatedCommandBreaker] You have now run this EXACT command "
    "{count} times and received the same result every time. Re-running an "
    "identical command cannot produce a different outcome. Do NOT issue this "
    "command again. Step back and change your approach materially: inspect "
    "the actual inputs/outputs to understand *why* it is not working, then "
    "try a genuinely different command (different tool, library, flags, or "
    "algorithm)."
)

_HARD = (
    "\n\n[RepeatedCommandBreaker] STOP. This identical command has now run "
    "{count} times with no change in result — you are stuck in a loop and "
    "wasting your budget. Abandon this command entirely. Concretely:\n"
    "  1. If you are trying to EXTRACT data from a source (an image, a "
    "binary, a document) and the extraction keeps failing, try a "
    "fundamentally different extraction method or different parameters — and "
    "do NOT silently substitute a guessed value for data you were required "
    "to read from the source; a fabricated value will fail verification.\n"
    "  2. If you are re-writing the same file, the file content is not the "
    "problem — run the program and read the ACTUAL error/output to find the "
    "real cause before editing again.\n"
    "  3. If you cannot make the current strategy work, pick a different "
    "strategy for this subtask rather than repeating this one.\n"
    "Your very next command MUST be different from the one you just ran."
)


class RepeatedCommandBreaker(MultiHookProcessor):
    """Detect byte-identical repeated Bash commands and escalate a directive.

    Parameters
    ----------
    tool_name:
        Which tool to watch (TB2/Tmax only expose ``Bash``).
    warn_threshold:
        On the Nth identical run, append the soft directive.
    hard_threshold:
        On the Mth identical run (and every run after), append the hard
        directive. Must be >= ``warn_threshold``.
    """

    _singleton_group = "repeated_command_breaker"
    _order = 31  # after CustomEditToolProcessor (30); appends to tool result

    def __init__(
        self,
        tool_name: str = "Bash",
        warn_threshold: int = 3,
        hard_threshold: int = 5,
    ) -> None:
        self.tool_name = tool_name
        self.warn_threshold = max(2, int(warn_threshold))
        self.hard_threshold = max(self.warn_threshold, int(hard_threshold))
        self._counts: dict[str, int] = {}
        self._pending: dict[str, str] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._counts.clear()
        self._pending.clear()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == self.tool_name:
            command = event.tool_input.get("command", "") if event.tool_input else ""
            norm = _normalise(command)
            if norm:
                key = hashlib.sha1(norm.encode("utf-8")).hexdigest()
                self._counts[key] = self._counts.get(key, 0) + 1
                self._pending[event.tool_call_id] = key
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        key = self._pending.pop(event.tool_call_id, None)
        if key is None:
            yield event
            return
        count = self._counts.get(key, 0)
        if count >= self.hard_threshold:
            warn = _HARD.format(count=count)
        elif count >= self.warn_threshold:
            warn = _WARN.format(count=count)
        else:
            yield event
            return
        yield dataclasses.replace(event, result=(event.result or "") + warn)

    async def on_task_end(self, event: TaskEndEvent):
        self._counts.clear()
        self._pending.clear()
        yield event
