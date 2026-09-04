# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatCommandBreakerProcessor for Tmax (and TB2-style) agents.

Closes a systemic no-progress failure mode observed across many Tmax
trajectories: the agent issues the **same** shell command, gets the
**same** result, and then re-issues that identical command again and
again — 15 to 28 consecutive times in the worst cases — making zero
progress until it either exhausts its step budget
(``exit_reason=budget_exceeded``) or gets its context compacted and
loses the plot. Common triggers:

* trying to ``kill`` / ``pkill`` a process that is a **zombie**
  (``<defunct>``) — a zombie cannot be killed; it is already dead and
  waiting to be reaped by its parent, so the kill is a permanent no-op;
* re-running a build/test command that keeps failing identically and
  expecting a different outcome;
* polling a state that never changes.

The single tool (``Bash``) makes this purely a control-loop problem:
the model has no self-monitor for "I have run this exact command with
this exact output N times and nothing has changed." This processor is
that monitor. It is benchmark-agnostic: it keys only on the observable
command/result fingerprint, never on task content.

Mechanism:
* ``on_after_tool`` fingerprints each ``Bash`` call as
  (normalized command, normalized result) and counts consecutive
  identical fingerprints.
* When the run reaches ``repeat_threshold`` identical (command, result)
  pairs in a row, it arms a one-shot corrective nudge.
* ``on_before_model`` injects that nudge as a single user message,
  telling the agent the command is not making progress and to change
  strategy. It fires at most once per
  distinct loop, then re-arms only if the count climbs another
  ``repeat_threshold`` above where it last fired — so a persistent loop
  gets a second, firmer escalation rather than a nudge every step.
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
    "task. If a mutating command has no effect, first inspect the actual "
    "current state before repeating it, then either address the underlying "
    "cause or move on."
)

_NUDGE_REPEAT = (
    "STOP. You are still repeating the exact same command with the exact same "
    "output ({n} times now) and getting nowhere. Abandon this command "
    "completely — do not issue it again under any circumstances. Re-read the "
    "task requirements, pick a genuinely different next action, and if this "
    "sub-step truly cannot be completed, move on to the other parts of the "
    "task rather than looping. One short, DIFFERENT command only."
)


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    t = text.strip()
    if len(t) > _MAX_FINGERPRINT_CHARS:
        t = t[:_MAX_FINGERPRINT_CHARS]
    return t


class RepeatCommandBreakerProcessor(MultiHookProcessor):
    """Interrupt degenerate identical-command / identical-output loops."""

    _singleton_group = "tmax_repeat_command_breaker"
    _order = 6

    def __init__(
        self,
        repeat_threshold: int = 4,
        tool_name: str = "Bash",
    ) -> None:
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.tool_name = str(tool_name)
        self._last_cmd: str = ""
        self._last_fp: str | None = None
        self._count: int = 0
        self._last_fired_at: int = 0
        self._pending_nudge: str = ""
        self._pending_n: int = 0

    def _reset(self) -> None:
        self._last_cmd = ""
        self._last_fp = None
        self._count = 0
        self._last_fired_at = 0
        self._pending_nudge = ""
        self._pending_n = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # Capture the command text so the fingerprint keys on
        # (command, result), not result alone — two genuinely different
        # commands that happen to print the same output must not be treated
        # as a loop.
        if event.tool_name == self.tool_name:
            inp = event.tool_input or {}
            cmd = inp.get("command", "") if isinstance(inp, dict) else str(inp)
            self._last_cmd = _normalize(cmd)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        # Only monitor the shell tool; ignore synthetic/other tools.
        if event.tool_name != self.tool_name:
            yield event
            return

        # ToolResultEvent does not carry the command input, so combine the
        # command captured in on_before_tool with the result. A degenerate
        # loop (same command -> same output) produces an identical
        # (command, result) fingerprint every iteration.
        result = event.result if event.error is None else f"ERR:{event.error}"
        fp = f"{self._last_cmd}\x00{_normalize(result)}"

        if fp and fp == self._last_fp:
            self._count += 1
        else:
            self._last_fp = fp
            self._count = 1
            self._last_fired_at = 0

        # Arm a nudge the first time we cross the threshold, and re-arm only
        # after another full threshold's worth of repeats past the last firing.
        if (
            self._count >= self.repeat_threshold
            and (self._count - self._last_fired_at) >= self.repeat_threshold
        ):
            escalate = self._last_fired_at > 0
            template = _NUDGE_REPEAT if escalate else _NUDGE_FIRST
            self._pending_nudge = template.format(n=self._count)
            self._pending_n = self._count
            self._last_fired_at = self._count

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        self._pending_n = 0
        msgs = list(event.messages)
        # Contract: never produce two consecutive user messages via a +1
        # insertion when the last role is already user. If a passive nudge is
        # already there, replace it; otherwise append.
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
