# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""CyclicToolLoopDetector — terminate degenerate *multi-step* tool-call cycles.

Closes a systemic failure mode the existing ``LoopDetectionProcessor`` misses.

``LoopDetectionProcessor`` (Strategy 1) only counts the *consecutive tail* of a
single exact fingerprint, so it catches **period-1** loops (A A A A) but is
blind to **period-k** cycles (A B A B …, A B C A B C …) because the alternating
call resets the consecutive count to 1 every step. Its name-only Strategy 2
does notice the repetition but is warn-only and never terminates.

Observed on the Tmax evolve set:
* task_000011: a strict A-B-A-B cycle — two Bash verification commands with
  byte-identical outputs (``Q0: 8.25`` / ``Q0..Q3``) repeated ~10 times while
  the model kept "verifying" and getting cut off by the token limit. 962s of
  wall-clock burned, run never terminated on its own.
* task_000010: fingerprint repeated 17× (period-1, would already be caught if
  LoopDetection were enabled — but it is not in the pipeline).

This processor fingerprints the **(tool_name, tool_input, result)** triple, so
it only fires when both the call *and* its observable output are identical
across repetitions. Productive exploration where the environment changes (an
install that finally succeeds, a Traceback that becomes a success) produces
*different* outputs each cycle and is therefore never flagged — that is the
guard against false-positive termination.

Detection: after each tool result, scan candidate periods ``1..max_period``.
For period ``p``, if the tail of the fingerprint window is ``n`` identical
consecutive blocks of length ``p`` (i.e. the same p-step cycle repeated ``n``
times), then:
* ``n >= warn_cycles``  → append a corrective warning to the tool result;
* ``n >= max_cycles``   → raise ``LoopDetectedError`` (clean
  ``exit_reason=loop_detected``, recovers best output — not an error exit).

Compaction-aware: the window is cleared on a message-count drop, matching
``LoopDetectionProcessor`` so stale pre-compaction fingerprints cannot inflate
a cycle count.
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
from harnessx.core.runloop import LoopDetectedError


_WARN_TEMPLATE = (
    "\n\n[CyclicLoopDetection] ⚠️  You have repeated the SAME cycle of "
    "{period} command(s) {cycles} times with identical output each time. You are "
    "stuck in a loop and making no progress. STOP repeating this sequence. "
    "Re-read the task requirements from scratch, question whether your current "
    "result is actually correct, and either take a fundamentally different action "
    "or finish now if the work is genuinely complete."
)


class CyclicToolLoopDetector(MultiHookProcessor):
    """Detect and terminate repeating period-k tool-call+output cycles.

    Args:
        max_period:   Largest cycle length to test for (default 4). Periods
                      1..max_period are all checked each step.
        max_cycles:   Number of identical consecutive cycle repetitions that
                      triggers ``LoopDetectedError`` (default 3).
        warn_cycles:  Repetition count that injects a corrective warning into
                      the tool result before the hard stop (default 2).
        window_size:  Sliding fingerprint-window length (default 24). Must be
                      >= max_period * (max_cycles + 1) to see the full pattern.
        compaction_drop_threshold: Message-count drop that signals compaction
                      and clears the window (default 5).
    """

    _singleton_group = "cyclic_tool_loop"
    # After CompactionProcessor (order=8) and LoopDetectionProcessor (order=20)
    # so the window reflects post-eviction message counts; letting the exact
    # period-1 detector fire first when it can.
    _order = 21

    def __init__(
        self,
        max_period: int = 4,
        max_cycles: int = 3,
        warn_cycles: int = 2,
        window_size: int = 24,
        compaction_drop_threshold: int = 5,
    ) -> None:
        self.max_period = max(1, int(max_period))
        self.max_cycles = max(2, int(max_cycles))
        self.warn_cycles = max(1, min(int(warn_cycles), self.max_cycles - 1))
        needed = self.max_period * (self.max_cycles + 1)
        self.window_size = max(int(window_size), needed)
        self.compaction_drop_threshold = int(compaction_drop_threshold)

        self._fps: deque[str] = deque(maxlen=self.window_size)
        self._pending_fp: dict[str, str] = {}
        self._current_run_id: str = ""
        self._prev_message_count: int = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]

    def _tail_cycle_count(self, period: int) -> int:
        """How many identical consecutive blocks of length *period* sit at the
        tail of the window. Returns 1 if the last block does not repeat."""
        n = len(self._fps)
        if n < period * 2:
            return 1
        fps = list(self._fps)
        block = fps[-period:]
        # Empty fingerprints (no-op steps) never form a loop.
        if any(f == "" for f in block):
            return 1
        count = 1
        idx = n - period
        while idx - period >= 0 and fps[idx - period : idx] == block:
            count += 1
            idx -= period
        return count

    def _reset(self) -> None:
        self._fps.clear()
        self._pending_fp.clear()
        self._prev_message_count = 0

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        self._current_run_id = event.run_id
        yield event

    async def on_step_start(self, event: StepStartEvent):
        if event.run_id != self._current_run_id:
            self._reset()
            self._current_run_id = event.run_id
        current = len(event.messages)
        if (
            self._prev_message_count > 0
            and self._prev_message_count - current >= self.compaction_drop_threshold
        ):
            self._fps.clear()
            self._pending_fp.clear()
        self._prev_message_count = current
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        try:
            input_str = json.dumps(event.tool_input, sort_keys=True, ensure_ascii=False)
        except Exception:
            input_str = repr(event.tool_input)
        self._pending_fp[event.tool_call_id] = f"{event.tool_name}\x00{input_str}"
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        call_part = self._pending_fp.pop(event.tool_call_id, None)
        if call_part is None:
            # No matching before_tool — record a break so it can't join a cycle.
            self._fps.append("")
            yield event
            return

        result_part = event.result or ""
        if event.error:
            result_part += f"\x01ERR\x01{event.error}"
        fp = self._hash(f"{call_part}\x00{result_part}")
        self._fps.append(fp)

        # Find the shortest period whose repeated-cycle count is highest.
        best_cycles = 1
        best_period = 1
        for period in range(1, self.max_period + 1):
            if len(self._fps) < period * 2:
                break
            c = self._tail_cycle_count(period)
            if c > best_cycles:
                best_cycles = c
                best_period = period

        if best_cycles >= self.max_cycles:
            raise LoopDetectedError(
                f"Cyclic loop detected: a {best_period}-step tool-call cycle with "
                f"identical output repeated {best_cycles} times consecutively"
            )

        if best_cycles >= self.warn_cycles:
            warning = _WARN_TEMPLATE.format(period=best_period, cycles=best_cycles)
            yield dataclasses.replace(event, result=(event.result or "") + warning)
            return

        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
