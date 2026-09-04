# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandBreaker with an execution-blocking tier.

Evolution of the prior ``RepeatedCommandBreaker`` (which only *appended a
text advisory* to the tool result). Trajectory evidence shows a model that
reads the advisory ("[RepeatedCommandBreaker] ... run N times ... STOP") and
re-issues the byte-identical Bash command anyway — a long-running identical
command also timing out repeatedly, burning the entire step/time budget to
``exit_reason=budget_exceeded`` with the loop still spinning.

A pure advisory has no teeth against a model that ignores advisories. This
version keeps the two soft tiers (warn / hard, appended to the result) and
adds a **third, higher block tier**: once the same normalised command has been
issued ``block_threshold`` times, the processor *refuses to execute it*
(``approved=False`` + ``synthetic_result``) in ``on_before_tool``, so the dead
command can no longer consume budget. The block message tells the agent to
change the command materially or move on.

Design notes
------------
* Blocking must happen in ``on_before_tool`` (before execution), so counting
  is done there. The warn / hard advisories still fire in ``on_after_tool`` for
  runs below the block threshold — they preserve the existing early-warning
  behaviour verbatim.
* ``block_threshold`` is set high by default (8) so legitimate short poll /
  healthcheck / retry repeats (typically 2-4 identical runs) are never blocked;
  identical ≥8× with zero variation is almost always a genuine degenerate loop.
* Command-agnostic: keys on a whitespace-normalised SHA1 of the whole command,
  so it fires for compile loops, extraction loops, service-restart loops, SQL
  rewrites — any stuck-repeat class. No task-specific literals.
* Contract-safe: ``on_after_tool`` only augments ``event.result``;
  ``on_before_tool`` only sets ``approved`` / ``synthetic_result`` — neither
  inserts or mutates ``event.messages``.
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
    """Collapse whitespace so trivially-reformatted duplicates still match."""
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

_BLOCK = (
    "[RepeatedCommandBreaker] BLOCKED — this command was NOT executed. You "
    "have issued this byte-identical command {count} times; the previous "
    "advisories to stop were ignored. Re-running it again cannot change the "
    "result and only wastes your remaining budget, so it is now being "
    "refused. You MUST take a materially different action:\n"
    "  1. Do NOT re-issue this command (even reformatted) — it will be "
    "blocked again.\n"
    "  2. If a background service / port keeps failing, inspect WHY (e.g. "
    "`ss -ltnp` / `ps aux`, read the actual bind error) instead of blindly "
    "restarting it; a process that keeps dying needs a different launch "
    "strategy (redirect its output, `nohup ... &`, or a timeout wrapper), "
    "not another restart.\n"
    "  3. If a script keeps timing out, stop re-running it whole — reproduce "
    "the failing step in isolation and read its real output.\n"
    "  4. Do NOT fabricate a required output to escape the loop; a guessed "
    "value fails verification. Solve the real blocker or move to the next "
    "required deliverable.\n"
    "Your next command MUST be genuinely different."
)


class RepeatedCommandBreaker(MultiHookProcessor):
    """Detect byte-identical repeated Bash commands; warn, then block.

    Parameters
    ----------
    tool_name:
        Which tool to watch (TB2/Tmax only expose ``Bash``).
    warn_threshold:
        On the Nth identical run, append the soft directive to the result.
    hard_threshold:
        On the Mth identical run (>= ``warn_threshold``), append the hard
        directive to the result.
    block_threshold:
        On the Kth identical run (>= ``hard_threshold``) and every run after,
        REFUSE to execute the command and return a synthetic block message
        instead. Set high so only genuine degenerate loops are blocked.
    """

    _singleton_group = "repeated_command_breaker"
    _order = 31  # after CustomEditToolProcessor (30); appends to tool result

    def __init__(
        self,
        tool_name: str = "Bash",
        warn_threshold: int = 3,
        hard_threshold: int = 5,
        block_threshold: int = 8,
    ) -> None:
        self.tool_name = tool_name
        self.warn_threshold = max(2, int(warn_threshold))
        self.hard_threshold = max(self.warn_threshold, int(hard_threshold))
        self.block_threshold = max(self.hard_threshold + 1, int(block_threshold))
        self._counts: dict[str, int] = {}
        self._pending: dict[str, str] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._counts.clear()
        self._pending.clear()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name != self.tool_name:
            yield event
            return
        command = event.tool_input.get("command", "") if event.tool_input else ""
        norm = _normalise(command)
        if not norm:
            yield event
            return
        key = hashlib.sha1(norm.encode("utf-8")).hexdigest()
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        if count >= self.block_threshold:
            # Refuse to execute; do not register a pending advisory (the block
            # message replaces both execution and the appended advisory).
            yield dataclasses.replace(
                event,
                approved=False,
                synthetic_result=_BLOCK.format(count=count),
            )
            return
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
