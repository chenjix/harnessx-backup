# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatCommandBlockerProcessor — escalating identical-command loop breaker.

Closes a systemic no-progress failure mode observed across many Tmax
trajectories: the agent issues the **same** shell command, gets the
**same** result, and re-issues that identical command again and again —
15 to 28 consecutive times in the worst cases — making zero progress
until it exhausts its step budget or its context is compacted and it
loses the plan. Common triggers:

* trying to ``kill`` / ``pkill`` a process that is already a **zombie**
  (``<defunct>``) — a zombie cannot be killed; it is dead and waiting to
  be reaped by its parent, so the kill is a permanent no-op;
* re-running a build/test command that keeps failing identically and
  expecting a different outcome;
* polling a state that never changes.

Why this processor and not the pre-existing nudge-only breaker
============================================================
The prior round shipped a nudge-only breaker
(``RepeatCommandBreakerProcessor``) that appends a user message after N
identical (command, result) pairs but never blocks the call. Trajectory
evidence on the 4B model shows the nudge fires and is then **ignored**:
the agent keeps emitting the byte-identical command and burns its whole
budget (e.g. ~40 of 58 steps spent re-issuing ``kill -9 <pids>`` on
unkillable zombies, then failing the real task). A passive text nudge is
insufficient control for a model that does not act on it.

This processor keeps the early **soft nudge** (recoverable, cheap) but
adds a **hard block** escalation: once the same command has produced the
same result ``block_threshold`` times, the *next* attempt to run that
exact command is intercepted in ``on_before_tool`` (``approved=False`` +
``synthetic_result``). The command is NOT executed; a synthetic tool
result is injected explaining the block and demanding a different action.
This physically returns the wasted step to the agent instead of letting
it spend it on a known no-op, while never raising / terminating the run
(so a late-recovering task is never killed).

It is benchmark-agnostic: it keys only on the observable command/result
fingerprint, never on task content. A single genuinely-different command
resets the counter, so legitimate polling (a few identical checks that
then change) is untouched.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

_MAX_FINGERPRINT_CHARS = 4000

_NUDGE_FIRST = (
    "PROGRESS CHECK: your last {n} tool calls were the *identical* command "
    "producing the *identical* output. Repeating the same command will keep "
    "returning the same result — it is not making progress. STOP repeating it. "
    "In your next turn, do NOT re-run that command. Instead, change approach: "
    "form a hypothesis about *why* the state is not changing, then run a "
    "DIFFERENT command that either diagnoses the root cause or advances the "
    "task. Note: a process shown as `<defunct>` / `Z` (zombie) is already "
    "dead and CANNOT be killed — it only disappears when its parent reaps it "
    "or the parent exits, so re-issuing kill/pkill on it is a permanent no-op."
)

_BLOCK_MSG = (
    "[LOOP BLOCKED] This exact command has already been run {n} times in a row "
    "with the exact same output, so it was NOT executed again. Re-running it "
    "cannot change anything. Do not issue this command again. Instead, take a "
    "genuinely different action: inspect a different piece of state, address "
    "the underlying cause, or move on to another required part of the task. "
    "If a `kill`/`pkill` keeps showing the same `<defunct>`/zombie process, "
    "that process is already dead and unkillable — stop trying to kill it and "
    "continue with the rest of the task."
)


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    t = text.strip()
    if len(t) > _MAX_FINGERPRINT_CHARS:
        t = t[:_MAX_FINGERPRINT_CHARS]
    return t


class RepeatCommandBlockerProcessor(MultiHookProcessor):
    """Escalating identical-command loop breaker: nudge, then hard block."""

    _singleton_group = "tmax_repeat_command_breaker"
    _order = 6

    def __init__(
        self,
        repeat_threshold: int = 4,
        block_threshold: int = 6,
        tool_name: str = "Bash",
    ) -> None:
        self.repeat_threshold = max(2, int(repeat_threshold))
        # Block must be at or above the nudge threshold so the agent always
        # gets a soft warning first and only gets hard-blocked if it ignores it.
        self.block_threshold = max(self.repeat_threshold, int(block_threshold))
        self.tool_name = str(tool_name)
        self._last_cmd: str = ""
        self._last_fp: str | None = None
        # Command string of the currently-looping fingerprint, used to decide
        # whether an incoming call is the SAME command that is already stuck.
        self._loop_cmd: str = ""
        self._count: int = 0
        self._last_fired_at: int = 0
        self._pending_nudge: str = ""

    def _reset(self) -> None:
        self._last_cmd = ""
        self._last_fp = None
        self._loop_cmd = ""
        self._count = 0
        self._last_fired_at = 0
        self._pending_nudge = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name != self.tool_name:
            yield event
            return

        inp = event.tool_input or {}
        cmd = inp.get("command", "") if isinstance(inp, dict) else str(inp)
        cmd = _normalize(cmd)
        self._last_cmd = cmd

        # Hard block: the same command has already produced the same output
        # block_threshold times in a row, and this incoming call is that exact
        # same command again. Refuse to execute it; inject a synthetic result.
        if (
            cmd
            and self._count >= self.block_threshold
            and cmd == self._loop_cmd
        ):
            msg = _BLOCK_MSG.format(n=self._count)
            yield dataclasses.replace(
                event,
                approved=False,
                synthetic_result=msg,
            )
            return

        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if event.tool_name != self.tool_name:
            yield event
            return

        result = event.result if event.error is None else f"ERR:{event.error}"
        fp = f"{self._last_cmd}\x00{_normalize(result)}"

        if fp and fp == self._last_fp:
            self._count += 1
        else:
            self._last_fp = fp
            self._loop_cmd = self._last_cmd
            self._count = 1
            self._last_fired_at = 0

        # Arm a soft nudge the first time we cross the nudge threshold, and
        # re-arm only after another full threshold's worth of repeats past the
        # last firing. Once block_threshold is reached the on_before_tool block
        # takes over, so we stop arming further nudges.
        if (
            self.repeat_threshold <= self._count < self.block_threshold
            and (self._count - self._last_fired_at) >= self.repeat_threshold
        ):
            self._pending_nudge = _NUDGE_FIRST.format(n=self._count)
            self._last_fired_at = self._count

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Contract: never produce two consecutive user messages via a +1
        # insertion when the last role is already user. If a trailing user
        # message is already there, replace it; otherwise append one.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
