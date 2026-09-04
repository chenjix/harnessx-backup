# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandBlocker — escalate an identical-command loop from an
advisory nudge to an actual *block*.

Background
----------
The existing ``RepeatedCommandBreaker`` (R0 asset) detects byte-identical
repeated Bash commands and *appends a text warning* to the tool result at a
warn threshold (default 3) and a hard threshold (default 5). On a strong
model the escalating text is usually enough to break the loop. On a weaker
model it frequently is **not**: the model reads the warning, re-narrates
"I am stuck in a loop", and then issues the *identical* command again —
turn after turn — until the step / time budget is gone.

Concrete evidence (this round): a task where the agent issued the exact
command ``ls -la /home/user/logs/ | head -20`` **26 times** in a row. The
advisory hard-warning fired repeatedly (it was visible in the tool result
"...has now run 7 times...") and was ignored every single time. ~40% of the
step budget evaporated in that dead loop before an unrelated compaction
event happened to jolt the model out of it — leaving too little budget to
iterate on the actual solution.

The gap this closes
-------------------
Advisory text cannot stop a model that ignores advisory text. A loop that
has already survived the warn + hard advisory stages needs a *mechanical*
intervention: refuse to execute the command and return a synthetic result
that forces a materially different next action. This is the same
``approved=False`` + ``synthetic_result`` mechanism the benchmark's own
``CustomSelfVerifyProcessor`` uses to intercept a tool call.

Design (Pareto-safe)
--------------------
The block threshold is deliberately set **above** the advisory hard
threshold. In this round's trajectories, three *passing* tasks legitimately
re-issued an identical inspection command up to 5 times (a benign re-read of
an output file), while the one runaway-loop failure hit 26. Blocking only
after ``block_threshold`` (default 8) identical repeats leaves every
observed benign repeat untouched (they cap at 5) while still cutting the
pathological loop off long before it can drain the budget.

The processor still *appends* the same escalating advisory text at
warn/hard thresholds (so on a model that can self-correct, nothing changes
until the loop proves itself pathological). Only when the count reaches
``block_threshold`` does it stop executing the command.

Generalisation: command-agnostic. Keys on a normalised hash of the whole
command, so it fires for OCR loops, compile loops, inspection loops, file
rewrites, curl loops — any class of task where a weak model gets stuck
repeating identical bytes. No task-specific literals.
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
    "[RepeatedCommandBreaker] BLOCKED — this exact command has now been "
    "issued {count} times with an identical result and repeated warnings "
    "were ignored. It was NOT executed this time. Re-running identical bytes "
    "cannot change anything and is draining your remaining budget.\n"
    "You MUST change strategy now. Do exactly one of:\n"
    "  1. If you are inspecting state (ls / cat / ps / du) and it keeps "
    "showing the same thing, that state is NOT going to change by looking "
    "again — act on what you have already seen and move to the next step.\n"
    "  2. If a program keeps producing the same wrong/empty output, read the "
    "actual error and change the program or its inputs — do not re-run it "
    "unchanged, and do not fabricate a value you were required to read.\n"
    "  3. If your current approach is stuck, adopt a genuinely different "
    "approach for this subtask.\n"
    "Your next Bash command MUST differ materially from this one; an "
    "identical re-issue will be blocked again."
)


class RepeatedCommandBlocker(MultiHookProcessor):
    """Detect byte-identical repeated Bash commands and, past a threshold,
    actually block execution (not just append a warning).

    Parameters
    ----------
    tool_name:
        Which tool to watch (TB2/Tmax only expose ``Bash``).
    warn_threshold:
        On the Nth identical run, append the soft advisory to the result.
    hard_threshold:
        On the Mth identical run (and every run up to ``block_threshold``),
        append the hard advisory. Must be >= ``warn_threshold``.
    block_threshold:
        On the Kth identical run (and every run after), refuse to execute the
        command and return a synthetic redirect instead. Must be
        >= ``hard_threshold``. Set above the largest *benign* repeat count
        observed in passing trajectories so it never blocks legitimate
        re-reads.
    """

    _singleton_group = "repeated_command_breaker"
    _order = 31  # after CustomEditToolProcessor (30)

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
            # Mechanical intervention: refuse to execute the identical command.
            yield dataclasses.replace(
                event,
                approved=False,
                synthetic_result=_BLOCK.format(count=count),
            )
            return
        # Below the block threshold: let it run, remember it for on_after_tool
        # so the advisory text can be appended to the real result.
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
