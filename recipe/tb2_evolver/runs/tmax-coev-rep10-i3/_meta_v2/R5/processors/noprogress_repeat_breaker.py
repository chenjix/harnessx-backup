# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""NoProgressRepeatBreaker — a two-stage circuit breaker for zero-progress
Bash command spirals.

TB2 exposes only ``Bash``. A recurring, harness-addressable pathology in the
R4 trajectories is the agent re-issuing the *exact same* command and getting
back the *exact same* result many times in a row, making literally zero
progress until the step budget is exhausted (``exit_reason=budget_exceeded``)
or, in the worst case, the run crashes (``exit_reason=error``) after the same
timed-out command (``exit 124, no output captured``) is re-run a dozen times.

Why this differs from the reverted R1 exact-command *nudge* guard
(``h_repeated_command_guard_v1``):

  1. **Signal = consecutive identical (command, result) pairs**, not a global
     count of a command string. R1 counted every occurrence of a command
     string anywhere in the run, which also flagged benign passing runs that
     re-emit an ``echo`` many times (exit 0, real progress between them). This
     breaker only reacts when the *same command produces the same output
     back-to-back* — the unambiguous signature of a no-progress loop. In the
     R4 data, no passing task ever exceeds 3 such consecutive identical pairs,
     while dying budget_exceeded tasks reach 5 / 6 / 10 / 19 / 24.

  2. **Two stages, and the second one blocks.** R1 was nudge-only and was
     ignored (in ``task_001017`` the model got repeated EditDetection nudges
     and still ran the same timed-out command 15 more times, burning ~3800s of
     wall-clock). Stage 1 (``soft_threshold``) appends a one-shot firm redirect
     to the tool result. Stage 2 (``hard_threshold``) actually *blocks*
     re-execution of the offending command via ``approved=False`` +
     ``synthetic_result``, injecting a provider-safe tool result that tells the
     model the command is being refused because it is not changing state. This
     reclaims the remaining step budget and caps wall-clock/cost bleed.

Safety / regression scope:
  - Thresholds are set strictly above the passing-cluster maximum of 3
    consecutive identical pairs (default soft=4, hard=6), so benign short
    polls / retries are untouched.
  - Only *identical* consecutive command+result pairs count; any change in the
    command OR its output resets the streak, so iterative debugging (each
    attempt slightly different, or the same command now producing new output)
    is never penalised.
  - The block is scoped to the exact offending command string. As soon as the
    agent issues any different command, the breaker disarms and normal
    execution resumes — it can never permanently wedge a run.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


_SOFT_NUDGE = (
    "\n\n[NoProgressRepeatBreaker] You have now run this exact command "
    "{count} times in a row and received the identical result each time. "
    "Re-running an identical command does not change the system state, so "
    "this is making zero progress. STOP repeating it. Instead: (1) re-read "
    "the task requirements, (2) diagnose *why* the output is unchanged — a "
    "wrong path, a missing dependency, a process that never started, a "
    "command that keeps timing out, or a flawed assumption — and (3) take a "
    "fundamentally different action. If you believe the work is already done, "
    "verify each required output file with `ls -lh` and `cat` rather than "
    "re-running this command."
)

_HARD_BLOCK = (
    "[NoProgressRepeatBreaker] BLOCKED. This exact command has already been "
    "executed {count} times in a row with the identical result and was NOT "
    "advancing the task, so it has not been run again to avoid exhausting the "
    "step budget. Do not issue this command again. Take a fundamentally "
    "different action: re-read the task, check the actual paths and process "
    "state, fix the underlying assumption, or verify your existing outputs "
    "with `ls -lh` / `cat`. Any different command will run normally."
)


def _norm(text) -> str:
    if not isinstance(text, str):
        return ""
    return text.strip()


# Guard-injected advisory suffixes appended by *other* processors (or by this
# one's own soft nudge) must be stripped before comparing results, otherwise an
# intermittent nudge from a sibling processor breaks the "identical result"
# streak and lets a genuine no-progress spiral slip through.
_GUARD_MARKERS = (
    "[NoProgressRepeatBreaker]",
    "[EditDetection]",
    "[RepeatedCommandGuard]",
    "Verification check initiated",
)


def _norm_result(text) -> str:
    """Normalize a tool result for streak comparison: strip whitespace and drop
    any trailing guard-injected advisory blocks so the underlying command
    output is compared, not the harness annotations layered on top of it."""
    if not isinstance(text, str):
        return ""
    s = text
    cut = len(s)
    for marker in _GUARD_MARKERS:
        idx = s.find(marker)
        if idx != -1 and idx < cut:
            cut = idx
    return s[:cut].strip()


def _is_timeout(result: str) -> bool:
    """A command that hit the per-command timeout (exit 124) produced no useful
    work and consumed the full timeout budget — the most expensive form of a
    no-progress repeat."""
    return "exit 124" in result


class NoProgressRepeatBreaker(MultiHookProcessor):
    """Break zero-progress identical-command / identical-result spirals.

    Stage 1 (soft_threshold): append a one-shot redirect nudge to the result.
    Stage 2 (hard_threshold): block re-execution of the offending command and
    return a synthetic tool result instead.
    """

    _singleton_group = "noprogress_repeat_breaker"
    _order = 32  # just after CustomEditToolProcessor (_order=30)

    def __init__(
        self,
        soft_threshold: int = 4,
        hard_threshold: int = 6,
        timeout_block_threshold: int = 3,
    ) -> None:
        self.soft_threshold = int(soft_threshold)
        self.hard_threshold = int(hard_threshold)
        if self.hard_threshold < self.soft_threshold:
            self.hard_threshold = self.soft_threshold
        # A command that keeps *timing out* (exit 124) burns the full per-command
        # timeout of wall-clock each time and makes zero progress, so it is
        # blocked at a lower streak than an ordinary identical-output repeat.
        self.timeout_block_threshold = int(timeout_block_threshold)
        # per-task state
        self._last_cmd: str = ""
        self._last_result: str = ""
        self._streak: int = 0  # consecutive identical (cmd, result) pairs
        self._timeout_streak: int = 0  # consecutive timeouts of the same command
        self._pending_cmd: dict[str, str] = {}  # tool_call_id -> normalized command
        self._nudged_for_cmd: str = ""  # cmd we've already soft-nudged once

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event

    def _reset(self) -> None:
        self._last_cmd = ""
        self._last_result = ""
        self._streak = 0
        self._timeout_streak = 0
        self._pending_cmd.clear()
        self._nudged_for_cmd = ""

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name != "Bash":
            yield event
            return
        cmd = _norm((event.tool_input or {}).get("command", ""))
        same_cmd = bool(cmd) and cmd == self._last_cmd
        # Block on either signal:
        #  (a) the same command has produced hard_threshold consecutive identical
        #      results (a stable no-progress loop), or
        #  (b) the same command has timed out timeout_block_threshold times in a
        #      row (each timeout burns the full per-command wall-clock budget).
        if same_cmd and (
            self._streak >= self.hard_threshold
            or self._timeout_streak >= self.timeout_block_threshold
        ):
            count = max(self._streak, self._timeout_streak)
            msg = _HARD_BLOCK.format(count=count)
            yield dataclasses.replace(event, approved=False, synthetic_result=msg)
            return
        # Stash the command so on_after_tool can pair it with its result.
        self._pending_cmd[event.tool_call_id] = cmd
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if event.tool_name != "Bash":
            yield event
            return
        # Ignore synthetic block results: our _HARD_BLOCK sentinel means the
        # command was refused, not executed — do not fold it into the streak.
        result = event.result or ""
        if "[NoProgressRepeatBreaker] BLOCKED." in result:
            yield event
            return

        # Pair the result with the command captured in on_before_tool.
        cmd = self._pending_cmd.pop(event.tool_call_id, None)
        norm_result = _norm_result(result)

        if cmd is None:
            # No command captured (shouldn't happen for Bash) — reset streak.
            self._last_cmd = ""
            self._last_result = norm_result
            self._streak = 0
            self._timeout_streak = 0
            yield event
            return

        same_cmd = cmd == self._last_cmd and cmd != ""
        if same_cmd and norm_result == self._last_result:
            self._streak += 1
        else:
            self._streak = 1
            self._nudged_for_cmd = ""  # new (cmd,result) context: allow a fresh nudge

        # Timeout streak: consecutive timeouts of the *same* command (output need
        # not be byte-identical — a timeout is a timeout).
        if same_cmd and _is_timeout(norm_result):
            self._timeout_streak += 1
        elif _is_timeout(norm_result):
            self._timeout_streak = 1
        else:
            self._timeout_streak = 0

        self._last_cmd = cmd
        self._last_result = norm_result

        # Soft nudge fires when either streak crosses soft_threshold but neither
        # has yet reached its hard/block threshold.
        soft_hit = (
            self.soft_threshold <= self._streak < self.hard_threshold
            or self.soft_threshold
            <= self._timeout_streak
            < self.timeout_block_threshold
        )
        if soft_hit and self._nudged_for_cmd != cmd:
            self._nudged_for_cmd = cmd
            count = max(self._streak, self._timeout_streak)
            yield dataclasses.replace(
                event, result=result + _SOFT_NUDGE.format(count=count)
            )
            return
        yield event

