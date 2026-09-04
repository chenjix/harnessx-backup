# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""DegenerateLoopBreakerProcessor — break low-information tool-call cycles.

Closes a systemic Tmax/TB2 failure mode distinct from the max_tokens
repetition loop handled by ``LengthTruncationRecoveryProcessor``:

The model issues *completed* tool calls, gets results, but is stuck
repeatedly running the same tiny set of commands that produce the same
outputs — making zero new observations. This shows up in two shapes:

* a single command repeated back-to-back many times (e.g. re-running an
  identical failing ``sqlite3``/``jq`` invocation), and
* a short A/B/A/B *cycle* of two commands whose results never change
  (e.g. ``rm && ln && python3 -c 'import operator'`` -> ``OK`` alternating
  with ``python3 /path/script.py`` -> the same traceback).

The naive "N consecutive byte-identical results" detector catches the
first shape but misses the second, because no two adjacent calls are
identical. This processor instead watches a rolling window of the last
``window`` (command, result) fingerprints: when that window collapses to
at most ``distinct_max`` distinct fingerprints, the agent is cycling among
a tiny set of repeated actions with no new information, and a corrective
note is appended to the tool result.

Design constraints (all satisfied):
* **Append-only on the tool result** (mirrors ``CustomEditToolProcessor``):
  never inserts/removes messages, never mutates the system prompt, so it
  cannot violate the message-count hook contract.
* **Content-agnostic**: no task ids, paths, command syntax, or dataset
  literals — it keys purely on the repetition *shape*.
* **Non-terminating**: it nudges and returns control; it never kills the
  run or forces an exit.
* Fires at most once per ``refire_gap`` firing tool-results so a stubborn
  loop is nudged again (escalating message) without spamming every step.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections import deque

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

_NUDGE_SOFT = (
    "\n\n[LoopBreaker] You have run essentially the same command(s) several "
    "times in a row and are getting the same result(s) — you are stuck in a "
    "loop and making no new progress. STOP repeating this action. Step back: "
    "state in one sentence what you now know for certain from these repeated "
    "outputs, then take a DIFFERENT concrete step (a different command, a "
    "different tool/approach, or inspect state you have not yet looked at). "
    "Do not re-run the same command again."
)

_NUDGE_HARD = (
    "\n\n[LoopBreaker] You are STILL cycling on the same failing action. This "
    "approach is not working and re-running it will not change the outcome. "
    "Abandon it entirely and change strategy: if a command keeps failing the "
    "same way, the fix is a fundamentally different mechanism, not a retry. "
    "If you are blocked on a hard constraint, satisfy it a different way and "
    "make sure every required output file exists at its exact specified path "
    "before you finish. Take one new, different action now."
)


def _fingerprint(command: str, result: str) -> str:
    """Stable short fingerprint of a (command, result) pair.

    Result is truncated to keep large outputs cheap to hash while still
    distinguishing genuinely different observations; the command is included
    in full so an unchanged command with an unchanged head-of-result reads as
    the same action.
    """
    h = hashlib.blake2b(digest_size=16)
    h.update((command or "").strip().encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update((result or "")[:2000].strip().encode("utf-8", "replace"))
    return h.hexdigest()


class DegenerateLoopBreakerProcessor(MultiHookProcessor):
    """Append a corrective nudge when recent tool activity collapses to a loop."""

    _singleton_group = "degenerate_loop_breaker"
    _order = 33  # after CustomEditToolProcessor(30), before CustomSelfVerifyProcessor(90)

    def __init__(
        self,
        window: int = 6,
        distinct_max: int = 2,
        min_window_fill: int = 6,
        refire_gap: int = 4,
    ) -> None:
        self.window = max(2, int(window))
        self.distinct_max = max(1, int(distinct_max))
        # Require the window to be full before judging (avoid firing on the
        # first couple of calls of a short task).
        self.min_window_fill = max(self.distinct_max + 1, int(min_window_fill))
        self.refire_gap = max(1, int(refire_gap))
        self._fps: deque[str] = deque(maxlen=self.window)
        self._pending: dict[str, str] = {}
        self._fires: int = 0
        self._since_last_fire: int = 0

    def _reset(self) -> None:
        self._fps.clear()
        self._pending.clear()
        self._fires = 0
        self._since_last_fire = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # Record the command text so we can pair it with its result.
        cmd = ""
        if isinstance(event.tool_input, dict):
            cmd = str(event.tool_input.get("command", "")) or str(event.tool_input)
        self._pending[event.tool_call_id] = cmd
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        cmd = self._pending.pop(event.tool_call_id, "")
        self._fps.append(_fingerprint(cmd, event.result or ""))
        self._since_last_fire += 1

        # Only judge once the window is full enough to be meaningful.
        if len(self._fps) < self.min_window_fill:
            yield event
            return

        distinct = len(set(self._fps))
        looping = distinct <= self.distinct_max

        # Fire on first detection, then only every refire_gap results so a
        # persistent loop gets re-nudged (escalated) without spamming.
        should_fire = looping and (
            self._fires == 0 or self._since_last_fire >= self.refire_gap
        )
        if not should_fire:
            yield event
            return

        note = _NUDGE_HARD if self._fires >= 1 else _NUDGE_SOFT
        self._fires += 1
        self._since_last_fire = 0
        yield dataclasses.replace(event, result=(event.result or "") + note)

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
