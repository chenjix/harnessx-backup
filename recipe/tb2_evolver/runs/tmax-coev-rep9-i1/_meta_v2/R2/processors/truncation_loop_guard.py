# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""TruncationLoopGuard — break the no-tool-call max-tokens repetition thrash.

Systemic failure mode observed on TB2/Tmax with a small per-call `max_tokens`
cap (4096): when the model tries to reason its way through a stuck spot it
emits a long block of narration with **no tool call**, hits the output-token
limit, gets truncated, receives the run-loop's passive "continue from where you
left off" nudge, and re-emits near-identical truncated narration — over and
over. Each such turn is truncated at the same length and carries no tool call,
so it makes zero progress and never writes the required output files.

Why the existing pipeline does not catch this:

* `LoopDetectionProcessor` fingerprints **tool calls only** (`on_before_tool` /
  `on_after_tool`). A turn with no tool call is invisible to it, so a run of
  10-17 truncated narration turns never trips it.
* `LengthTruncationRecoveryProcessor` keys its corrective nudge off
  `finish_reason == "length"` in `on_after_model`; in this pipeline that
  corrective text never reaches the transcript (only the run-loop's passive
  nudge appears), so the model gets no directive to stop narrating and act.

This guard is deliberately independent of `finish_reason`. It keys off two
directly observable properties of the assistant turn:

1. it carried **no tool call**, and
2. its content is **long** (>= ``trunc_char_threshold``) — the signature of a
   response that ran into the output-token cap.

It counts such turns inside a sliding window of the most recent assistant
turns. A single legitimate reasoning-only turn (or a couple interleaved with
real tool work) never accumulates; a truncation thrash does.

* At ``warn_threshold`` long-no-tool turns in the window it injects ONE strong
  corrective user message (contract-safe: replaces the trailing user message if
  the run loop already added its passive nudge, otherwise appends exactly one).
  This is the recovery push the dead length-recovery nudge was meant to give.
* At ``raise_threshold`` it raises ``LoopDetectedError`` for a clean
  ``exit_reason=loop_detected`` — containment that stops the run from burning
  its whole step budget on a loop that is provably making no progress.

Thresholds are tuned wide against this round's trajectories: every passing task
tops out at <= 2 long-no-tool turns in an 8-window, and the one heavy-but-
recovering passing task (task_000740) peaks at 4 — so ``warn=3`` only nudges and
``raise=6`` never fires on it, while the failing thrash cluster (6-7 in-window)
is caught.
"""

from __future__ import annotations

import dataclasses
from collections import deque

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runloop import LoopDetectedError


_NUDGE = (
    "STOP narrating. Your last several turns produced long reasoning that was "
    "cut off by the output-token limit WITHOUT running any command — you are "
    "repeating yourself and making no progress. Do not re-explain your analysis. "
    "In your very next turn write at most two short sentences, then issue exactly "
    "ONE concrete Bash command that either (a) writes a required output file to "
    "the exact path named in the task, or (b) runs a single quick command to "
    "inspect the current state so you can pick the next concrete step. One "
    "command only. Keep the whole response short so it is not truncated again."
)


class TruncationLoopGuard(MultiHookProcessor):
    """Detect and break a run of long, tool-call-free (truncated) turns."""

    _singleton_group = "tmax_truncation_loop_guard"
    # After length_recovery (5) / env (5) / time (6); before parse_retry (10).
    _order = 7

    def __init__(
        self,
        window_size: int = 8,
        warn_threshold: int = 3,
        raise_threshold: int = 6,
        trunc_char_threshold: int = 1500,
    ) -> None:
        self.window_size = max(1, int(window_size))
        self.warn_threshold = max(1, int(warn_threshold))
        self.raise_threshold = max(self.warn_threshold + 1, int(raise_threshold))
        self.trunc_char_threshold = max(1, int(trunc_char_threshold))
        # Sliding window of booleans: was each recent assistant turn a
        # long, tool-call-free (i.e. likely truncated) turn?
        self._window: deque[bool] = deque(maxlen=self.window_size)
        self._pending_nudge: str = ""
        self._warned: bool = False

    def _reset(self) -> None:
        self._window.clear()
        self._pending_nudge = ""
        self._warned = False

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        content = event.content or ""
        long_no_tool = (not event.tool_calls) and len(content) >= self.trunc_char_threshold
        self._window.append(long_no_tool)

        if not long_no_tool:
            # A productive turn (tool call, or a short answer) is progress;
            # clear the pending nudge so we don't inject stale corrections.
            self._pending_nudge = ""
            yield event
            return

        count = sum(1 for v in self._window if v)

        if count >= self.raise_threshold:
            raise LoopDetectedError(
                "Truncation loop: "
                f"{count} long tool-call-free (token-limit-truncated) turns "
                f"in the last {len(self._window)} turns — no progress being made"
            )

        if count >= self.warn_threshold and not self._warned:
            self._pending_nudge = _NUDGE
            self._warned = True

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # The run loop appends a passive "continue" user message after a
        # length truncation. Replace it (never insert a 2nd trailing user
        # message) to stay within the on_before_model contract.
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
