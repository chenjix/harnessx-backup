# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""StuckReasoningRecoveryProcessor — break degenerate *reasoning* loops.

Failure class (observable, task-agnostic):
    The agent emits the SAME assistant message content on consecutive
    turns — often explicitly narrating "I keep getting the same error /
    I am stuck in a loop / let me try a fundamentally different approach"
    — yet the next turn reproduces that identical narration and the same
    action. The agent has recognised it is stuck but cannot self-recover:
    its recovery attempt is itself the repeat.

Why this is distinct from the existing guards:
    * ``LengthTruncationRecoveryProcessor`` only fires on
      ``finish_reason == "length"`` (output-cap runaway with no tool call).
    * Error/command-fingerprint guards key on a *failing* tool result or a
      byte-identical *tool call*. This processor keys on the repeated
      *assistant content* and therefore also catches loops where every tool
      call returns SUCCESS (e.g. "Script created", exit 0) while the agent's
      reasoning never actually changes — a shape those guards miss.

Mechanism:
    * Track the last non-empty assistant message content.
    * When the current turn's content is (near-)identical to the previous
      turn's, increment a run counter; any genuinely different turn resets it.
    * Once ``repeat_threshold`` consecutive identical turns are seen, arm a
      single legible loop-break directive that is injected before the next
      model call. It names that the previous approach produced no change and
      requires a *concrete different* action (inspect inputs / isolate the
      failing piece / change the invocation / re-read the task), not more
      narration.
    * A cooldown prevents re-nudging until the agent actually changes its
      content, and ``max_nudges`` bounds total interventions per task.

The nudge is advisory (a user message); it never blocks or rewrites the
agent's tool calls, so it cannot break a task that was about to self-correct.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor


_NUDGE_FIRST = (
    "LOOP DETECTED: your last several turns produced the SAME reasoning and the "
    "same action, so nothing in the environment is changing. Repeating it again "
    "will not help. Do NOT restate your previous plan. Instead, in your next turn: "
    "(1) run ONE command that inspects the ACTUAL current state or the exact error "
    "output you keep hitting, then (2) change something concrete about your "
    "approach — a different invocation, isolate the smallest failing piece, or "
    "re-read the task to check you are solving the right problem. One concrete step."
)

_NUDGE_REPEAT = (
    "STILL LOOPING. You are again repeating an approach that has already failed to "
    "change the outcome. STOP describing the same plan. Abandon this line entirely. "
    "Pick a DIFFERENT strategy: if a file/command keeps failing the same way, read "
    "its full error, verify your assumptions about inputs and paths from scratch, or "
    "solve a reduced version first. Emit at most two sentences of NEW reasoning, "
    "then exactly one command that is materially different from what you just tried."
)


def _norm(text: str) -> str:
    """Whitespace-normalise so trivial formatting jitter is not 'different'."""
    return re.sub(r"\s+", " ", (text or "")).strip()


class StuckReasoningRecoveryProcessor(MultiHookProcessor):
    """Detect consecutive identical assistant reasoning and force a change."""

    _singleton_group = "tmax_stuck_reasoning_recovery"
    _order = 6

    def __init__(
        self,
        repeat_threshold: int = 12,
        max_nudges: int = 3,
        min_chars: int = 24,
    ) -> None:
        # repeat_threshold=12 sits ABOVE the longest identical-reasoning run seen
        # on any passing trajectory where the loop occurs MID-TASK (10-11 on the
        # sampled batch), and BELOW the failing cluster's runs (14-24), so doomed
        # loops are broken while productive-but-repetitive work is not touched.
        # The only passing runs above 12 are post-completion verify-tails, where
        # an advisory nudge is at worst harmless (work already banked).
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.max_nudges = max(1, int(max_nudges))
        self.min_chars = max(0, int(min_chars))
        self._prev: str = ""
        self._run: int = 1
        self._nudges: int = 0
        self._pending_nudge: str = ""
        self._armed_for: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._prev = ""
        self._run = 1
        self._nudges = 0
        self._pending_nudge = ""
        self._armed_for = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        cur = _norm(event.content)

        # Only meaningful narration counts; ignore empty/trivial turns so a
        # normal short tool-only turn cannot accumulate a false loop.
        if len(cur) < self.min_chars:
            yield event
            return

        if cur == self._prev:
            self._run += 1
        else:
            self._run = 1
            self._prev = cur
            self._armed_for = ""  # content changed -> loop broken, reset cooldown

        if (
            self._run >= self.repeat_threshold
            and self._nudges < self.max_nudges
            and self._armed_for != cur  # don't re-arm for the same stuck content
        ):
            self._pending_nudge = (
                _NUDGE_REPEAT if self._nudges >= 1 else _NUDGE_FIRST
            )
            self._armed_for = cur
            self._nudges += 1

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Contract: never create a +2 user insertion. If the run loop already
        # appended a trailing user message, replace it; otherwise append one.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._prev = ""
        self._run = 1
        self._nudges = 0
        self._pending_nudge = ""
        self._armed_for = ""
        yield event
