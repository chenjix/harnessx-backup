# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandBreaker — break degenerate identical-command loops.

Closes a systemic failure mode observed across many TB2 / Tmax failures:
the agent issues the *same* Bash command (byte-identical after whitespace
normalisation) turn after turn, getting the same result each time, without
making progress. Concrete shapes seen in trajectories:

* the agent re-writes the identical source file 30+ times ("cat > file
  << EOF" heredoc) while narrating "I've been stuck in a loop" but never
  actually changing the command — burns the entire step/time budget and
  exits ``budget_exceeded`` with reward 0;
* the agent re-runs an extraction / test / restart command that keeps
  returning the same output ~7-21 times, verbally acknowledging the loop
  ("The user is right - I've been stuck in a loop") yet issuing the
  byte-identical command again on the very next turn.

Why this file exists as an *upgrade* over the soft-only breaker
--------------------------------------------------------------
The previous version only *appended advisory text* to the tool result
(``on_after_tool``). Trajectories in this round prove that a soft warning
is insufficient for this model: on ``task_000028`` the same
``pkill … server`` command ran 12+ times *after* the hard warning fired;
on ``task_000506`` the same test command ran 21 times; on ``task_001207``
the same verification command repeated to budget exhaustion. In every
case the model *narrated* agreement with the warning and then re-issued
the identical command. Advisory text keyed on ``on_after_tool`` cannot
break this — the model has already committed to the next identical call
by the time it reads the warning.

This version therefore adds a HARD BLOCK at ``on_before_tool``: once a
normalised command has been requested ``block_threshold`` times, the
processor refuses to execute it (``approved=False``) and injects a
synthetic tool result stating the command was NOT run and that the very
next command must be materially different. This converts a run of wasted
identical steps (which otherwise exhaust ``budget_exceeded``) into a
forced pivot, while leaving the earlier soft-warn escalation ladder
intact for cases the model self-corrects from.

The block is contract-safe: it only sets ``approved`` / ``synthetic_result``
on the ``ToolCallEvent`` (the documented interception path, identical to
``BgInstallGuard``) and only augments ``event.result`` on the after-tool
path — it never inserts messages.

Generalisation: this is command-agnostic. It keys on a normalised hash of
whatever command was run, so it fires for OCR loops, compile loops, file
rewrites, SQL rewrites, restart loops, test loops — any class of task
where the model gets stuck repeating itself. No task-specific literals.
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

_BLOCK = (
    "[RepeatedCommandBreaker] BLOCKED — this command was NOT executed. You "
    "have already issued this byte-identical command {count} times and it "
    "produced the same result every time; running it again cannot change "
    "anything and would only waste your remaining budget. Re-running it is "
    "no longer permitted.\n"
    "You must break the loop NOW. Do exactly one of the following on your "
    "next turn:\n"
    "  1. Diagnose the ROOT CAUSE differently: inspect the actual "
    "inputs/state/errors with a DIFFERENT command (e.g. read the real "
    "error output, check the environment, list the actual files) rather "
    "than re-running the failing action.\n"
    "  2. Change the approach materially: a different tool, library, flag, "
    "algorithm, filename, or file location — not a whitespace-only edit of "
    "this command.\n"
    "  3. If part of the task already works, STOP polishing it: confirm each "
    "required deliverable exists at its exact required path, then move on to "
    "the parts that are still missing.\n"
    "Do NOT fabricate or guess a value you were required to read from a "
    "source — a fabricated value fails verification. Issue a genuinely "
    "different command now."
)


class RepeatedCommandBreaker(MultiHookProcessor):
    """Detect byte-identical repeated Bash commands; escalate then hard-block.

    Escalation ladder on the count of identical normalised requests:

    * ``>= warn_threshold``  → append the soft directive to the result.
    * ``>= hard_threshold``  → append the hard directive to the result.
    * ``>= block_threshold`` → refuse to execute the command at all
      (``approved=False``) and inject a synthetic result telling the model
      the command was blocked and it must pivot. This is the mechanism that
      actually stops the budget-exhausting loop; the soft/hard text alone
      was shown to be ignored by the model in this round's trajectories.

    Parameters
    ----------
    tool_name:
        Which tool to watch (TB2/Tmax only expose ``Bash``).
    warn_threshold:
        On the Nth identical run, append the soft directive.
    hard_threshold:
        On the Mth identical run, append the hard directive. ``>= warn``.
    block_threshold:
        On the Kth identical *request* (and every request after), the
        command is blocked before execution. ``>= hard``. Set to a value
        higher than ``hard_threshold`` so the model gets at least one
        escalated text warning before the mechanical block engages.
    """

    _singleton_group = "repeated_command_breaker"
    _order = 31  # after CustomEditToolProcessor (30); appends to tool result

    def __init__(
        self,
        tool_name: str = "Bash",
        warn_threshold: int = 3,
        hard_threshold: int = 5,
        block_threshold: int = 7,
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
            # Hard block: do not execute; inject a corrective synthetic result.
            # Do not register this call in _pending — there is no real result
            # to augment on the after-tool path.
            yield dataclasses.replace(
                event,
                approved=False,
                synthetic_result=_BLOCK.format(count=count),
            )
            return
        # Below the block threshold: let it run, remember it so the
        # after-tool hook can escalate a text warning on the real result.
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
