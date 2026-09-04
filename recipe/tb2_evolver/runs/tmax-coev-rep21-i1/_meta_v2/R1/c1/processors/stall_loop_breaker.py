# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""StallLoopBreakerProcessor — break byte-identical Bash command loops.

Closes a dominant Tmax failure cluster: the model gets stuck re-issuing the
*exact same* Bash command turn after turn (same tesseract invocation, the same
``cat > file << EOF`` rewrite, the same ``redis-cli LLEN``), receives the same
result, makes no progress, and burns the entire step / wall-clock budget until
``exit_reason`` is ``budget_exceeded`` or ``error``. Across a round these loops
correlate almost perfectly with ``reward == 0``.

Existing guards do not catch this:
* ``LengthTruncationRecoveryProcessor`` only fires when the model produces NO
  tool call (``finish_reason == "length"``). Here the model *does* emit a tool
  call — it is just identical to the previous one.
* ``CustomEditToolProcessor`` only counts writes to the *same file* and fires at
  a high threshold (>7), too late and too narrow (misses read-only loops like
  ``redis-cli LLEN`` or ``convert``).

This processor is generic and command-agnostic. It normalises the Bash command
string, tracks how many times each signature has been *recently* seen, and:
* on the ``warn_repeat``-th recurrence, appends a corrective note to the tool
  result telling the agent the command is a repeat that produced the same output
  and to change approach;
* on the ``block_repeat``-th recurrence, short-circuits the tool call entirely
  (``approved=False`` + a synthetic result) so the identical command does not
  re-execute and waste budget — forcing the model to do something different.

It never touches a command the first time it is seen, so trajectories that make
linear progress (one distinct command per step) are unaffected.
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

_WARN_NOTE = (
    "\n\n[StallLoopBreaker] You have now issued this EXACT same command "
    "{count} times. It produced the same result each time and is not making "
    "progress. Do NOT run it again. Change your approach: inspect a different "
    "part of the state, fix the underlying bug, or write the required output "
    "with a materially different command."
)

_BLOCK_MSG = (
    "[StallLoopBreaker] BLOCKED — this is the {count}th time you have tried to "
    "run this identical command. It was NOT executed because it produced the "
    "same result on every previous attempt and is wasting your budget. You are "
    "stuck in a loop. Abandon this exact command completely. In your next turn: "
    "(1) do NOT repeat any variation of narration you have already written, and "
    "(2) issue a DIFFERENT command — e.g. print intermediate values to find the "
    "real bug, examine the actual file contents, or write the required output "
    "file(s) with a fresh implementation. One new, different command only."
)


def _normalize(cmd: str) -> str:
    """Collapse whitespace so cosmetically-identical commands hash the same."""
    return _WS_RE.sub(" ", (cmd or "").strip())


class StallLoopBreakerProcessor(MultiHookProcessor):
    """Detect and break byte-identical Bash command repetition loops."""

    _singleton_group = "tmax_stall_loop_breaker"
    _order = 25

    def __init__(
        self,
        warn_repeat: int = 2,
        block_repeat: int = 3,
        window: int = 6,
    ) -> None:
        # warn_repeat: on this many recent identical occurrences, warn (still run)
        # block_repeat: on this many, short-circuit the call (do not run)
        self.warn_repeat = max(2, int(warn_repeat))
        self.block_repeat = max(self.warn_repeat + 1, int(block_repeat))
        self.window = max(2, int(window))
        # sliding window of recent command signatures (most recent last)
        self._recent: list[str] = []
        # tool_call_id -> (signature, occurrence_count) for calls we let through
        self._pending: dict[str, tuple[str, int]] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._recent.clear()
        self._pending.clear()
        yield event

    def _count_in_window(self, sig: str) -> int:
        return self._recent.count(sig)

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name != "Bash":
            yield event
            return
        cmd = event.tool_input.get("command", "") if event.tool_input else ""
        norm = _normalize(cmd)
        if not norm:
            yield event
            return
        sig = hashlib.md5(norm.encode("utf-8", "ignore")).hexdigest()

        # occurrence count *including* this attempt, within the sliding window
        occ = self._count_in_window(sig) + 1

        if occ >= self.block_repeat:
            # Short-circuit: do not execute the identical command again.
            # Do NOT record it in the window (it did not run) so that a later
            # genuinely-new command resets the loop naturally.
            yield dataclasses.replace(
                event,
                approved=False,
                synthetic_result=_BLOCK_MSG.format(count=occ),
            )
            return

        # Let it run; remember signature so on_after_tool can annotate.
        self._recent.append(sig)
        if len(self._recent) > self.window:
            self._recent = self._recent[-self.window :]
        self._pending[event.tool_call_id] = (sig, occ)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        entry = self._pending.pop(event.tool_call_id, None)
        if entry is None:
            yield event
            return
        _sig, occ = entry
        if occ >= self.warn_repeat:
            note = _WARN_NOTE.format(count=occ)
            yield dataclasses.replace(event, result=(event.result or "") + note)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._recent.clear()
        self._pending.clear()
        yield event
