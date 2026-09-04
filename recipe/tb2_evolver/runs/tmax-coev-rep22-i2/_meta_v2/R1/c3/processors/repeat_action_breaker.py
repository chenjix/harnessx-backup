# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatActionBreakerProcessor for Tmax (TB2-style) agents.

Closes a systemic failure mode observed across multiple distinct tasks:
the agent issues the *exact same tool call* (identical name + identical
arguments) turn after turn, receives essentially the same tool output each
time, and never breaks out of the loop. It then either exhausts the step
budget (``exit_reason=budget_exceeded``) or trips a run-loop error
(``exit_reason=error``). Every observed task with >=10 consecutive
identical tool calls failed, burning the full step budget on a single
stuck command.

Neither ``LengthTruncationRecoveryProcessor`` (keyed on
``finish_reason=length``) nor ``ParseRetryProcessor`` (keyed on malformed
tool calls) catches this shape: here the tool call is well-formed and the
model finishes normally — it is simply repeating a well-formed no-op.

This processor tracks the fingerprint (tool name + canonicalised input) of
each step's tool call. When the same fingerprint repeats
``repeat_threshold`` times in a row it injects a corrective user message
before the next model turn, telling the agent it is looping and to change
strategy. On a second escalation it issues a stronger "stop and take a
fundamentally different approach" nudge. The nudge is advisory only — it
never blocks, kills, or rewrites the agent's action; it only adds context
so the agent can self-correct. This keeps already-passing tasks (which
never repeat a call) completely untouched.
"""

from __future__ import annotations

import dataclasses
import json

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

_NUDGE_FIRST = (
    "You have now issued the SAME command with the SAME arguments several "
    "turns in a row and are getting the same result each time — you are stuck "
    "in a loop and making no progress. Do NOT run that command again. Step "
    "back and reason about WHY it is not changing anything: is the command "
    "actually a no-op, is it targeting the wrong process/file/path, or does "
    "the underlying problem need a different fix entirely? Then take a "
    "DIFFERENT concrete action that addresses the root cause."
)

_NUDGE_REPEAT = (
    "STOP repeating the same command. You have looped on an identical action "
    "many times with no effect, which means your current approach cannot "
    "solve this. Abandon it completely. Re-read the task objectives, inspect "
    "the current state with a NEW diagnostic command you have not run yet, and "
    "pursue a fundamentally different strategy. Under no circumstances re-issue "
    "the command you have been repeating."
)


def _fingerprint(event: ModelResponseEvent) -> str | None:
    """Stable signature of the tool call(s) in a model response.

    Returns ``None`` when the response has no tool calls (nothing to loop on).
    """
    calls = event.tool_calls or []
    if not calls:
        return None
    parts = []
    for tc in calls:
        name = getattr(tc, "name", "") or ""
        inp = getattr(tc, "input", None)
        try:
            inp_s = json.dumps(inp, sort_keys=True, default=str)
        except Exception:
            inp_s = repr(inp)
        parts.append(f"{name}\x00{inp_s}")
    return "\x01".join(parts)


class RepeatActionBreakerProcessor(MultiHookProcessor):
    """Break identical-tool-call repetition loops and redirect the agent."""

    _singleton_group = "tmax_repeat_action_breaker"
    _order = 6

    def __init__(self, repeat_threshold: int = 3, escalate_threshold: int = 6) -> None:
        # repeat_threshold: consecutive identical calls before the first nudge.
        # escalate_threshold: consecutive identical calls before the stronger nudge.
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.escalate_threshold = max(self.repeat_threshold + 1, int(escalate_threshold))
        # Keyed by run_id so parallel workers sharing the singleton each track
        # their own state.
        self._last_fp: dict[str, str] = {}
        self._run_len: dict[str, int] = {}
        self._pending: dict[str, str] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._last_fp.pop(event.run_id, None)
        self._run_len.pop(event.run_id, None)
        self._pending.pop(event.run_id, None)
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        run_id = event.run_id
        fp = _fingerprint(event)
        if fp is None:
            # No tool call this turn — reset the streak but don't fire.
            self._last_fp.pop(run_id, None)
            self._run_len[run_id] = 0
            yield event
            return

        if fp == self._last_fp.get(run_id):
            self._run_len[run_id] = self._run_len.get(run_id, 1) + 1
        else:
            self._last_fp[run_id] = fp
            self._run_len[run_id] = 1

        run_len = self._run_len[run_id]
        if run_len >= self.escalate_threshold:
            self._pending[run_id] = _NUDGE_REPEAT
        elif run_len >= self.repeat_threshold:
            self._pending[run_id] = _NUDGE_FIRST
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        run_id = event.run_id
        nudge = self._pending.pop(run_id, "")
        if not nudge:
            yield event
            return
        msgs = list(event.messages)
        # Contract-safe: if the run loop already left a trailing user message,
        # replace it rather than inserting a second consecutive user turn.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._last_fp.pop(event.run_id, None)
        self._run_len.pop(event.run_id, None)
        self._pending.pop(event.run_id, None)
        yield event
