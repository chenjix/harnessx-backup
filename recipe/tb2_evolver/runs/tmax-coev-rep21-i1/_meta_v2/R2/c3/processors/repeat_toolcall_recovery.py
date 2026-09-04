# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedToolCallRecoveryProcessor — command-keyed loop breaker.

Closes a gap left open by ``RepeatedCommandRecoveryProcessor``, which keys its
loop fingerprint on ``(tool_name, result_text, error_text)``. That guard is
DEFEATED whenever the agent re-issues a byte-for-byte identical command but the
result text drifts slightly between calls — e.g. a ``ps aux`` snapshot whose
RSS / CPU / STAT columns tick on every invocation, or any command whose output
embeds a timestamp, PID, counter, or memory figure. In that case the
result-keyed run length never exceeds 1, the guard never fires, and the agent
can burn its entire step budget re-issuing the same command with a static
narration.

Observed shape (task_000118_3043e92d): the model emitted the identical
assistant message and the identical Bash call
``ps aux | grep -E "worker_sim|python3" | grep -v grep`` ~20 consecutive times.
Each result differed only in the monitor process's RSS column (10288, 10300,
10308, ...) and STAT flag (S/R), so the existing result-keyed guard saw 20
DISTINCT fingerprints and stayed silent. The task was never fixed.

This processor detects the loop on the *cause* side rather than the *effect*
side: it fingerprints each tool call by ``(tool_name, tool_input)`` at
``on_before_tool`` — the command the agent chose to run, independent of what
came back. Consecutive identical commands are counted; once the run reaches
``repeat_threshold`` a corrective user message is injected before the next
model call telling the agent the repeated command is not moving it forward and
to take a materially different action.

It is complementary to (not a replacement for) the result-keyed guard:
* result-keyed guard: identical command AND identical result (stuck on a stable
  error). Fires and can be silenced by fixing the command.
* this guard: identical command regardless of result drift (spinning on a
  monitoring / polling command, or a narration loop that keeps re-checking
  the same thing). Catches the drift-immune case.

The nudge is a generic strategy prompt: it names no task, path, command, or
constant, so it applies to any repeated-command loop on any task.
"""

from __future__ import annotations

import dataclasses
import json

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

_NUDGE_FIRST = (
    "LOOP DETECTED: you have issued the same command {n} times in a row. Even "
    "though its output may look slightly different each time (e.g. a changing "
    "PID, memory figure, timestamp, or process state), re-running it is not "
    "moving you toward the goal. STOP re-issuing that command. In your next "
    "turn: (1) state in one line what you were trying to learn or verify, then "
    "(2) take a MATERIALLY DIFFERENT action toward the actual task — write or "
    "fix the required file, run the real test/deployment end-to-end, or inspect "
    "a different thing. One concrete, different command."
)

_NUDGE_REPEAT = (
    "STILL LOOPING: you have now repeated the same command many times without "
    "changing your approach. Polling or re-checking the same thing will not "
    "complete the task. Step back: re-read the task's success criteria, decide "
    "the ONE concrete change still needed to satisfy them, and do that now with "
    "a single different command. Do not narrate at length; act differently."
)


def _input_fingerprint(tool_input) -> str:
    """Stable string form of a tool_input (dict / str / other)."""
    if isinstance(tool_input, str):
        return tool_input
    try:
        return json.dumps(tool_input, sort_keys=True, default=str)
    except Exception:
        return repr(tool_input)


class RepeatedToolCallRecoveryProcessor(MultiHookProcessor):
    """Break identical-command loops even when the result text drifts."""

    _singleton_group = "tmax_repeat_toolcall_recovery"
    # Sit just after the result-keyed guard (7) so the two are adjacent and
    # both run before compaction (8). Ordering is not load-bearing: it only
    # injects a message at on_before_model.
    _order = 7

    def __init__(
        self,
        repeat_threshold: int = 3,
        input_fingerprint_chars: int = 4000,
    ) -> None:
        # repeat_threshold = number of consecutive identical (tool_name,
        # tool_input) pairs required before the guard fires. threshold=3 means
        # the 3rd identical command in a row triggers a nudge before the model
        # would produce a 4th.
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.input_fingerprint_chars = max(0, int(input_fingerprint_chars))
        self._last_fp: str | None = None
        self._run_len: int = 0
        self._pending_nudge: str = ""

    def _reset(self) -> None:
        self._last_fp = None
        self._run_len = 0
        self._pending_nudge = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        raw = _input_fingerprint(event.tool_input)
        fp = "\x00".join(
            (
                event.tool_name or "",
                raw[: self.input_fingerprint_chars],
            )
        )

        if fp == self._last_fp:
            self._run_len += 1
        else:
            self._last_fp = fp
            self._run_len = 1

        if self._run_len >= self.repeat_threshold:
            if self._run_len >= self.repeat_threshold + 2:
                self._pending_nudge = _NUDGE_REPEAT
            else:
                self._pending_nudge = _NUDGE_FIRST.format(n=self._run_len)

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Contract: never create two consecutive user messages. If the loop
        # already left a trailing user message, append onto it.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            prev = msgs[-1].content or ""
            merged = (prev + "\n\n" + nudge) if prev else nudge
            msgs[-1] = Message(role="user", content=merged)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
