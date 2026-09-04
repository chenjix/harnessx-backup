# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""WindowedRepeatBreakerProcessor.

A warn-only Control hook that catches *non-consecutive* no-progress loops
that the existing ``LoopDetectionProcessor`` structurally misses.

Observed failure mode (R3, budget_exceeded cluster): several tasks burn the
whole 80-step budget oscillating between two or three commands that never
change the underlying state — e.g. a service/daemon restart-and-retest cycle
(``pkill server; ./server &; sleep; curl ...`` re-run 16-19x), or a
"final verification" command interleaved with empty/no-op commands
(``verify`` / ``''`` / ``verify`` / ``''`` ...). Because the repeats are
*interleaved* with other commands (an inspection, an empty command, a
slightly different check), the existing ``LoopDetectionProcessor`` — which
counts only the **strictly consecutive** run length at the tail of its
window — never reaches its warn/raise threshold. The agent keeps re-running
the identical failing cycle instead of reading and fixing the source that
produces the failing result.

This processor adds two complementary, orthogonal signals, both **warn-only**
(never raises — a raise would convert legitimately-repetitive passing tasks
into ``loop_detected`` failures):

1. **Windowed exact-repeat** — within a sliding window of the last
   ``window_size`` tool calls, if the *same* non-empty command fingerprint
   has now appeared ``repeat_threshold`` or more times (not necessarily
   consecutively), append an advisory: you have run this identical command
   many times with the same result — the problem is upstream (your code /
   config), so stop re-running it and go inspect/fix the source, or finish
   if the deliverable is already correct.

2. **Empty / no-op command run** — if the agent issues ``empty_threshold``
   empty-or-whitespace commands in a row, append an advisory: an empty
   command does nothing; either issue a real command or finish. This catches
   the degenerate generation loop where the model emits blank tool calls.

Class of tasks served: every TB2 task that can fall into an interleaved
no-progress loop (service/daemon build-and-test tasks, multi-stage pipeline
tasks). It carries no task-specific literals — it fingerprints whatever
command the agent runs.

Design notes
------------
- Warn-only by construction: there is no ``threshold`` that raises. The
  R2 evidence showed that raising on repeat signals risks flipping
  currently-passing tasks that legitimately repeat a command (a passing task
  was observed repeating the same command 8x within an 8-call window). The
  advisory is appended to the tool *result* content, so ``event.messages`` is
  never mutated and the hook contract is trivially satisfied (same mechanism
  as ``LoopDetectionProcessor``).
- Complements, does not duplicate, ``LoopDetectionProcessor``: that processor
  fires on strictly-*consecutive* identical calls; this one fires on
  *windowed* repeats regardless of interleaving. The two can both be present.
- Ignores the ``FunctionalVerifyGateProcessor`` keepalive tool
  (``_tb2_functional_verify``) so the exit-gate handshake is never counted as
  a loop.
- To keep the advisory from spamming every subsequent step once a loop is
  established, each distinct signal fires at most once per
  ``cooldown`` window of steps.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import deque

from harnessx.core.events import (
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Keepalive tool emitted by FunctionalVerifyGateProcessor — never count it.
_VERIFY_KEEPALIVE = "_tb2_functional_verify"

_WINDOWED_WARN = (
    "\n\n[RepeatBreaker] ⚠️  You have run this exact command {count} times "
    "within the last {window} steps (not making progress — same command, same "
    "result). Re-running it will not change the outcome: the problem is "
    "upstream. Stop repeating this command. Instead, open and READ the source "
    "file / config / script that produces this result, form a hypothesis about "
    "WHY it is wrong, and change THAT. If the required deliverable is already "
    "correct on disk, finish now."
)

_EMPTY_WARN = (
    "\n\n[RepeatBreaker] ⚠️  You have issued {count} empty/no-op commands in a "
    "row. An empty command does nothing. Either run a real command that makes "
    "progress on the task, or — if every required deliverable is already on "
    "disk and verified — end now."
)


class WindowedRepeatBreakerProcessor(MultiHookProcessor):
    """Warn-only detector for interleaved (non-consecutive) no-progress loops."""

    # Distinct singleton slot from LoopDetectionProcessor's "loop_detection".
    _singleton_group = "windowed_repeat_breaker"
    _order = 21  # just after LoopDetectionProcessor (order=20)

    def __init__(
        self,
        window_size: int = 8,
        repeat_threshold: int = 5,
        empty_threshold: int = 3,
        cooldown: int = 4,
        compaction_drop_threshold: int = 5,
    ) -> None:
        self.window_size = max(2, int(window_size))
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.empty_threshold = max(2, int(empty_threshold))
        self.cooldown = max(1, int(cooldown))
        self.compaction_drop_threshold = max(1, int(compaction_drop_threshold))

        self._fingerprints: deque[str] = deque(maxlen=self.window_size)
        self._empty_run: int = 0
        self._steps_seen: int = 0
        self._last_windowed_warn_step: int = -10_000
        self._last_empty_warn_step: int = -10_000
        self._current_run_id: str = ""
        self._prev_message_count: int = 0
        # tool_call_id -> (fingerprint, is_empty)
        self._pending: dict[str, tuple[str, bool]] = {}

    # ------------------------------------------------------------------
    def _reset(self) -> None:
        self._fingerprints.clear()
        self._empty_run = 0
        self._steps_seen = 0
        self._last_windowed_warn_step = -10_000
        self._last_empty_warn_step = -10_000
        self._prev_message_count = 0
        self._pending.clear()

    @staticmethod
    def _fingerprint(tool_name: str, command: str) -> str:
        return hashlib.sha256(f"{tool_name}\x00{command}".encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        self._current_run_id = event.run_id
        yield event

    async def on_step_start(self, event: StepStartEvent):
        if event.run_id != self._current_run_id:
            self._reset()
            self._current_run_id = event.run_id
        # Clear stale fingerprints after a compaction-driven message drop so
        # pre-compaction history doesn't inflate the windowed count.
        current_count = len(event.messages)
        if (
            self._prev_message_count > 0
            and self._prev_message_count - current_count >= self.compaction_drop_threshold
        ):
            self._fingerprints.clear()
            self._empty_run = 0
            self._pending.clear()
        self._prev_message_count = current_count
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _VERIFY_KEEPALIVE:
            yield event
            return
        command = ""
        try:
            command = event.tool_input.get("command", "") or ""
        except AttributeError:
            command = ""
        if not isinstance(command, str):
            try:
                command = json.dumps(command, sort_keys=True, ensure_ascii=False)
            except Exception:
                command = repr(command)
        is_empty = not command.strip()
        fp = self._fingerprint(event.tool_name, command.strip())
        self._pending[event.tool_call_id] = (fp, is_empty)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        entry = self._pending.pop(event.tool_call_id, None)
        if entry is None:
            # Not a tracked call (e.g. the verify keepalive) — pass through.
            yield event
            return

        fp, is_empty = entry
        self._steps_seen += 1
        warning = ""

        # --- Signal 2: empty / no-op run -------------------------------
        if is_empty:
            self._empty_run += 1
            if (
                self._empty_run >= self.empty_threshold
                and (self._steps_seen - self._last_empty_warn_step) >= self.cooldown
            ):
                warning = _EMPTY_WARN.format(count=self._empty_run)
                self._last_empty_warn_step = self._steps_seen
        else:
            self._empty_run = 0

        # --- Signal 1: windowed exact repeat ---------------------------
        # Count occurrences of this fp in the current window (before append).
        if not is_empty and not warning:
            count_in_window = sum(1 for x in self._fingerprints if x == fp) + 1
            if (
                count_in_window >= self.repeat_threshold
                and (self._steps_seen - self._last_windowed_warn_step) >= self.cooldown
            ):
                warning = _WINDOWED_WARN.format(
                    count=count_in_window, window=self.window_size
                )
                self._last_windowed_warn_step = self._steps_seen

        # Record this call in the window (empty commands are recorded too so a
        # single stray blank between repeats doesn't reset the windowed count).
        self._fingerprints.append(fp)

        if warning:
            yield dataclasses.replace(event, result=(event.result or "") + warning)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
