# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedToolCallBreaker for Tmax (and TB2-style) agents.

Closes a systemic, benchmark-dominant failure mode: the model gets stuck
emitting the *byte-identical tool call* turn after turn, receiving the same
tool result each time and making no forward progress, until it exhausts the
step / budget limit (``exit_reason in {budget_exceeded, max_steps}``) or ends
with no tool calls.

Round-0 evidence (tmax-coev-rep20-i1-r0): 28 / 50 tasks emitted a run of >=5
byte-identical consecutive Bash calls; 27 of those 28 failed. Several tasks
emitted the same command 33 times in a row (the whole session). The existing
``LengthTruncationRecoveryProcessor`` only catches the ``finish_reason=length``
variant of stuck-ness (repetition *inside one generation*); it does not catch
the case where each generation is short and well-formed but is the *same call
as the previous turn*. No loop detector fires on these — they run to
``budget_exceeded``.

This processor is model-agnostic and task-agnostic. It tracks the signature
``(tool_name, canonical_json(input))`` of the most recent tool call. When the
model repeats the identical signature ``repeat_threshold`` times in a row it
injects a single corrective user message before the next model turn that:

* states plainly that the identical command has already run N times with the
  same result and is not making progress, and
* directs the model to change strategy (inspect a different thing, or act on
  what it already knows) rather than re-issuing the same command.

It never kills the run or drops the tool call — it only adds a nudge, so a
legitimately-repeated poll degrades gracefully (the model is simply told the
result is unchanged) instead of being terminated. The nudge escalates if the
model keeps repeating after being warned.
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


def _sig(tool_calls) -> str:
    """Order-stable identity signature for a turn's tool calls."""
    parts = []
    for tc in tool_calls:
        name = getattr(tc, "name", "") or ""
        inp = getattr(tc, "input", None)
        try:
            canon = json.dumps(inp, sort_keys=True, default=str)
        except Exception:
            canon = repr(inp)
        parts.append(name + "\x1f" + canon)
    return "\x1e".join(parts)


def _nudge_first(count: int) -> str:
    return (
        f"You have now issued the exact same command {count} times in a row and "
        "received the same result each time. Repeating it again will not change "
        "anything. Stop re-running it. In your next turn, take a DIFFERENT "
        "concrete action: either (a) act on what that result already tells you "
        "and move to the next step of the task, or (b) run a different command "
        "that inspects a different file, path, or aspect of the state. Do not "
        "re-issue the previous command."
    )


def _nudge_repeat(count: int) -> str:
    return (
        f"STOP. You have repeated the identical command {count} times and it is "
        "still producing the same output. You are in a loop and burning your step "
        "budget. Abandon this command entirely. Re-read the task requirements, "
        "then issue ONE clearly different command that makes real progress toward "
        "producing the required output file(s) or final state. If you believe the "
        "task is already complete, verify the required outputs exist and then "
        "stop."
    )


class RepeatedToolCallBreaker(MultiHookProcessor):
    """Detect byte-identical consecutive tool calls and nudge a strategy change."""

    _singleton_group = "tmax_repeated_tool_call_breaker"
    _order = 6  # right after length_recovery (_order=5), before compaction

    def __init__(self, repeat_threshold: int = 3, escalate_threshold: int = 5) -> None:
        # repeat_threshold: number of consecutive identical calls that triggers
        # the first nudge. escalate_threshold: switch to the harder nudge.
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.escalate_threshold = max(self.repeat_threshold + 1, int(escalate_threshold))
        self._last_sig: str = ""
        self._run: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._last_sig = ""
        self._run = 0
        self._pending_nudge = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        tool_calls = event.tool_calls or ()
        if not tool_calls:
            # No tool call this turn — not the identical-call loop; reset.
            self._last_sig = ""
            self._run = 0
            yield event
            return

        sig = _sig(tool_calls)
        if sig and sig == self._last_sig:
            self._run += 1
        else:
            self._last_sig = sig
            self._run = 1

        if self._run >= self.repeat_threshold:
            if self._run >= self.escalate_threshold:
                self._pending_nudge = _nudge_repeat(self._run)
            else:
                self._pending_nudge = _nudge_first(self._run)
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # If the run loop already left a trailing user message, replace it so we
        # don't create back-to-back user turns (the loop merges them, but
        # replacing keeps the corrective message primary).
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._last_sig = ""
        self._run = 0
        self._pending_nudge = ""
        yield event
