# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandBreaker — interrupt identical-tool-call repetition loops.

Systemic failure mode observed on TB2/Tmax tasks: a small model gets stuck and
re-emits the **byte-identical** tool call (typically the same Bash command)
turn after turn — 11 to 31 times in a row in observed trajectories — each
returning the same result and making zero progress, until the step/budget cap
is hit (``exit_reason=budget_exceeded``). This burns the whole task on pure
repetition.

Existing guards do not cover it:
* ``LengthTruncationRecoveryProcessor`` only fires on ``finish_reason=length``
  with no tool call; here the model *is* issuing tool calls.
* ``CustomEditToolProcessor`` only matches file-*write* command patterns and
  only appends a soft advisory the model ignores; most loops are non-write
  commands (``ps aux | grep``, ``pkill``, running a binary).

This processor keys on the *raw tool-call signature* (name + normalised input).
When the same signature is emitted ``repeat_threshold`` times in a row, it
injects an escalating, forceful user redirect that (1) states the exact command
has run N times with no state change, (2) forbids re-emitting it, and (3)
forces a diagnostic pivot / fundamentally different approach. It never blocks
the tool and adds no net messages (it replaces the run loop's trailing user
turn when present), so it is contract-safe and cannot regress working tasks —
legitimate work does not repeat one command verbatim N times consecutively.
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


def _signature(tool_calls) -> str:
    """Stable signature for a turn's tool calls (order-preserving)."""
    parts = []
    for tc in tool_calls or ():
        name = getattr(tc, "name", "") or ""
        raw = getattr(tc, "input", None)
        try:
            inp = json.dumps(raw, sort_keys=True, default=str)
        except Exception:
            inp = repr(raw)
        parts.append(f"{name}::{inp}")
    return "\u0001".join(parts)


def _first_command(tool_calls) -> str:
    for tc in tool_calls or ():
        raw = getattr(tc, "input", None)
        if isinstance(raw, dict):
            cmd = raw.get("command")
            if isinstance(cmd, str):
                return cmd
        return str(raw)
    return ""


_NUDGE_FIRST = (
    "STOP — you have now issued the EXACT same command {n} times in a row and it "
    "keeps producing the same result, so it is not changing the state of the "
    "system. Repeating it again will not help. Do NOT run this command again:\n"
    "    {cmd}\n"
    "Instead, in your next turn take a DIFFERENT concrete action: inspect the "
    "current state to understand why the last attempt did not work (check the "
    "actual file contents / process list / exit codes), then try a fundamentally "
    "different approach to the task. One command only."
)

_NUDGE_HARD = (
    "STOP IMMEDIATELY. You are stuck in a repetition loop — the identical command "
    "below has been run {n} times with no effect. Continuing to repeat it will "
    "waste your entire remaining budget and the task will fail.\n"
    "    {cmd}\n"
    "Abandon this exact command completely. Reason briefly about WHY it is not "
    "working (what would you need to check to find out?), then issue ONE new, "
    "different Bash command — either a diagnostic to gather the missing "
    "information, or a genuinely different strategy for the task. Do not paraphrase "
    "the same command; change the actual approach."
)


class RepeatedCommandBreaker(MultiHookProcessor):
    """Break identical-tool-call repetition loops with an escalating redirect."""

    _singleton_group = "repeated_command_breaker"
    _order = 6

    def __init__(
        self,
        repeat_threshold: int = 3,
        hard_threshold: int = 6,
        max_cmd_chars: int = 400,
    ) -> None:
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.hard_threshold = max(self.repeat_threshold + 1, int(hard_threshold))
        self.max_cmd_chars = max(80, int(max_cmd_chars))
        self._last_sig: str = ""
        self._run_len: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._last_sig = ""
        self._run_len = 0
        self._pending_nudge = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        tool_calls = event.tool_calls
        if not tool_calls:
            # A non-tool turn breaks the streak.
            self._last_sig = ""
            self._run_len = 0
            yield event
            return

        sig = _signature(tool_calls)
        if sig and sig == self._last_sig:
            self._run_len += 1
        else:
            self._last_sig = sig
            self._run_len = 1

        if self._run_len >= self.repeat_threshold:
            cmd = _first_command(tool_calls)
            if len(cmd) > self.max_cmd_chars:
                cmd = cmd[: self.max_cmd_chars] + " …"
            template = _NUDGE_HARD if self._run_len >= self.hard_threshold else _NUDGE_FIRST
            self._pending_nudge = template.format(n=self._run_len, cmd=cmd)

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Stay contract-safe: if the run loop already appended a trailing user
        # turn (e.g. the tool result echo / continue nudge), replace it rather
        # than insert a second consecutive user message.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            base = msgs[-1].content or ""
            merged = f"{base}\n\n{nudge}" if base else nudge
            msgs[-1] = Message(role="user", content=merged)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._last_sig = ""
        self._run_len = 0
        self._pending_nudge = ""
        yield event
