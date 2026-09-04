# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""CyclicLoopBreaker — break degenerate period-k tool-call cycles *and let the
run continue*, instead of terminating it.

Failure mode this closes
------------------------
The Tmax agent frequently gets stuck repeating the SAME tool call (or the SAME
short alternating sequence of calls) that returns byte-identical output every
time, making no progress and burning its entire step budget on a distraction
before it can even attempt the real task. Observed on the evolve set:

* ``task_000118_3043e92d`` (system_administration, budget_exceeded @ 80 steps):
  28 *consecutive* identical (call, output) triples — the agent fixated on a
  zombie process (``kill -9 1`` → same ``[python3] <defunct>`` output) for
  ~50 turns, recognised it was looping ("I'm stuck in a loop") but could not
  break out. It finally escaped on its own and started writing the actual
  ``deployment_monitor.py`` at step 60 — with only ~20 steps of budget left,
  not enough to iterate on the (buggy) monitor. Peak log dir = 209MB vs. the
  45MB threshold → reward 0.
* ``task_001857_24daeef3`` (debugging, budget_exceeded @ 80 steps): a strict
  period-2 A-B cycle — two commands with byte-identical outputs alternating
  for ~8 cycles (fingerprints ``f2ccf2, 2bf091, f2ccf2, 2bf091, …``).
* ``task_001979_a1e24b6f`` (data_processing, exit=error): one fingerprint
  repeated 37× across the run.

Why the existing pipeline misses it
-----------------------------------
* ``LengthTruncationRecoveryProcessor`` resets its counter on *any* tool call,
  so a loop that carries tool calls never trips it.
* ``ParseRetryProcessor`` only counts parse errors, not productive-looking
  repeats.
* The stock ``LoopDetectionProcessor`` exact detector only counts a *period-1
  consecutive* tail (A A A A) — it is blind to the period-2 A-B-A-B case, and
  it is not even registered in this pipeline.

Design — redirect, don't terminate
-----------------------------------
A prior candidate proposed a detector that raises ``LoopDetectedError`` on the
same shape. Terminating the run reclaims budget but can *never flip* these
tasks: the agent never gets to finish. task_000118 shows the agent can recover
on its own if it stops looping early enough. So this processor:

1. ``on_after_tool``: fingerprints the (tool_name, tool_input, result) triple
   and, when the tail forms an identical period-k cycle repeated
   ``warn_cycles`` times, appends a one-time corrective warning to the result
   (a nudge — still lets the tool run).
2. ``on_before_tool``: if the *incoming* call would extend a cycle that has
   already repeated ``break_cycles`` times, it **blocks the call**
   (``approved=False``) and injects a forceful redirect as the synthetic
   result — WITHOUT ending the run. The agent keeps its remaining budget and
   is pushed to abandon the dead-end line and work the actual task.
3. Safety net: if the agent ignores the redirect and keeps re-forming the same
   loop, blocked calls are cheap (instant, no execution), but if the number of
   blocks in one run exceeds ``max_total_blocks`` we raise ``LoopDetectedError``
   so a pathological non-recovering agent can't spin its whole budget.

False-positive guard: the fingerprint includes the observable *output*, so
productive exploration where the environment changes (an install that finally
succeeds, a Traceback that becomes a success) produces different outputs each
cycle and is never flagged. Empty/no-op steps can never join a cycle.

Compaction-aware: the window is cleared on a large message-count drop so stale
pre-compaction fingerprints can't inflate a cycle count.

Contains no task-specific constants, paths, commands, or answers — it keys
purely on the structural property "identical call+output repeated".
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
    "\n\n[CyclicLoopBreaker] WARNING: you have repeated the SAME cycle of "
    "{period} command(s) {cycles} times and the output is identical every "
    "time. You are stuck and making no progress. STOP repeating this. Re-read "
    "the original task, question whether this action is even relevant to the "
    "goal, and take a fundamentally different step."
)

_BREAK_TEMPLATE = (
    "[CyclicLoopBreaker] This exact command has already been run and returned "
    "identical output {runs} times with no change — it is blocked to stop an "
    "infinite loop. Repeating it will NOT help. Do not run it again. Step back: "
    "re-read the ORIGINAL task requirements, drop whatever tangent you are stuck "
    "on (it is almost certainly not required by the task), and make concrete "
    "progress on the actual deliverable now. If the real work is done, finish."
)


class CyclicLoopBreaker(MultiHookProcessor):
    """Detect a repeating identical period-k tool cycle, then block-and-redirect.

    Args:
        max_period:   Largest cycle length to test for (default 4). Periods
                      1..max_period are all checked each step.
        warn_cycles:  Identical-cycle repetitions that inject a soft warning
                      into the tool result (default 2).
        break_cycles: Identical-cycle repetitions after which the *next*
                      matching call is blocked and redirected instead of run
                      (default 3).
        max_total_blocks: Total blocked calls allowed in one run before falling
                      back to LoopDetectedError as a hard safety net
                      (default 12).
        window_size:  Sliding fingerprint-window length (default 32).
        compaction_drop_threshold: Message-count drop that signals compaction
                      and clears the window (default 5).
    """

    _singleton_group = "cyclic_loop_breaker"
    # After CompactionProcessor / PostCompactionRefresh so the window reflects
    # post-eviction message counts, and after BgInstallGuard so it can't fight
    # another on_before_tool blocker for the same call.
    _order = 22

    def __init__(
        self,
        max_period: int = 4,
        warn_cycles: int = 2,
        break_cycles: int = 3,
        max_total_blocks: int = 12,
        window_size: int = 32,
        compaction_drop_threshold: int = 5,
    ) -> None:
        self.max_period = max(1, int(max_period))
        self.warn_cycles = max(1, int(warn_cycles))
        self.break_cycles = max(self.warn_cycles + 1, int(break_cycles))
        self.max_total_blocks = max(1, int(max_total_blocks))
        needed = self.max_period * (self.break_cycles + 2)
        self.window_size = max(int(window_size), needed)
        self.compaction_drop_threshold = int(compaction_drop_threshold)

        # Full (call+result) fingerprint window — used for cycle detection.
        self._fps: deque[str] = deque(maxlen=self.window_size)
        # Call-only fingerprint window — used to match an *incoming* call to
        # the repeating block before its result is known.
        self._call_fps: deque[str] = deque(maxlen=self.window_size)
        self._pending: dict[str, str] = {}
        self._blocked_ids: set[str] = set()
        self._current_run_id: str = ""
        self._prev_message_count: int = 0
        self._total_blocks: int = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]

    def _call_fp(self, tool_name: str, tool_input: dict) -> str:
        try:
            input_str = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
        except Exception:
            input_str = repr(tool_input)
        return self._hash(f"{tool_name}\x00{input_str}")

    @staticmethod
    def _tail_cycle_count(fps: list[str], period: int) -> int:
        """Identical consecutive blocks of length *period* at the tail."""
        n = len(fps)
        if n < period * 2:
            return 1
        block = fps[-period:]
        if any(f == "" for f in block):
            return 1
        count = 1
        idx = n - period
        while idx - period >= 0 and fps[idx - period : idx] == block:
            count += 1
            idx -= period
        return count

    def _best_cycle(self, fps: list[str]) -> tuple[int, int]:
        best_cycles, best_period = 1, 1
        for period in range(1, self.max_period + 1):
            if len(fps) < period * 2:
                break
            c = self._tail_cycle_count(fps, period)
            if c > best_cycles:
                best_cycles, best_period = c, period
        return best_cycles, best_period

    def _reset(self) -> None:
        self._fps.clear()
        self._call_fps.clear()
        self._pending.clear()
        self._blocked_ids.clear()
        self._prev_message_count = 0
        self._total_blocks = 0

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
            self._call_fps.clear()
        self._prev_message_count = current
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # Only intervene when a full-triple cycle is already established AND the
        # incoming call is the same call that repeats inside that cycle.
        call_fp = self._call_fp(event.tool_name, event.tool_input)
        self._pending[event.tool_call_id] = f"{event.tool_name}\x00{call_fp}"

        best_cycles, best_period = self._best_cycle(list(self._fps))
        if best_cycles >= self.break_cycles and len(self._call_fps) >= best_period:
            # The repeating call-block is the tail `best_period` call fps.
            recent_block = list(self._call_fps)[-best_period:]
            if call_fp in recent_block:
                self._total_blocks += 1
                if self._total_blocks > self.max_total_blocks:
                    raise LoopDetectedError(
                        f"Cyclic loop unbroken: a {best_period}-step identical "
                        f"tool cycle kept re-forming and was blocked "
                        f"{self.max_total_blocks} times without the agent making "
                        f"progress"
                    )
                # Block the call, inject a redirect, keep the run alive.
                # Do NOT record this call in the windows (it never executed);
                # the pending entry is dropped and the id is tagged blocked so
                # on_after_tool can tell a block apart from real progress.
                self._pending.pop(event.tool_call_id, None)
                self._blocked_ids.add(event.tool_call_id)
                yield dataclasses.replace(
                    event,
                    approved=False,
                    synthetic_result=_BREAK_TEMPLATE.format(runs=best_cycles),
                )
                return

        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        entry = self._pending.pop(event.tool_call_id, None)
        if entry is None:
            # A call we blocked: it never executed. Record a break so it can't
            # join a cycle. The run-wide _total_blocks counter is intentionally
            # NOT reset here — it is the safety-net budget.
            if event.tool_call_id in self._blocked_ids:
                self._blocked_ids.discard(event.tool_call_id)
                self._fps.append("")
                self._call_fps.append("")
                yield event
                return
            # An unmatched result (no before_tool seen). Genuine progress:
            # break any cycle.
            self._fps.append("")
            self._call_fps.append("")
            yield event
            return

        tool_name, call_fp = entry.split("\x00", 1)
        result_part = event.result or ""
        if event.error:
            result_part += f"\x01ERR\x01{event.error}"
        full_fp = self._hash(f"{call_fp}\x00{result_part}")
        self._fps.append(full_fp)
        self._call_fps.append(call_fp)

        best_cycles, best_period = self._best_cycle(list(self._fps))
        if best_cycles >= self.warn_cycles:
            warning = _WARN_TEMPLATE.format(period=best_period, cycles=best_cycles)
            yield dataclasses.replace(event, result=(event.result or "") + warning)
            return

        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
