# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""DegenerateRepetitionRecoveryProcessor — a superset of the Tmax
LengthTruncationRecoveryProcessor that also catches *self-repetition*
attractor loops where the model emits huge blocks of near-identical
narration but STILL manages to attach a (usually useless, identical)
tool call.

Motivation (harness deficiency observed on a >=4 task cluster of
`exit_reason=budget_exceeded` runs):

The original recovery processor only acts when
``finish_reason == "length" and not event.tool_calls``. On the observed
loops the model interleaves the runaway narration with a repeated no-op
Bash command (e.g. re-``ls``-ing the same path). Those turns carry a
tool call, so the original processor:

  * never collapses the runaway content (it only collapses on the
    no-tool-call branch), so the repetitive text accumulates in the
    transcript and keeps re-priming the same attractor; and
  * resets its consecutive-truncation counter every interleaved turn,
    so the escalated "STOP, you are repeating yourself" nudge almost
    never fires.

The run then burns its entire step budget (80/80) in the loop.

This processor generalises the trigger to *degeneracy* — a turn is
degenerate when EITHER it was length-truncated with no tool call, OR
its assistant content is dominated by internal self-repetition (a low
unique-sentence ratio over a long body). On a degenerate turn it:

  * collapses the runaway content to head + marker + tail REGARDLESS of
    whether a tool call is attached, so poisoned narration stops
    accumulating in history; and
  * tracks degeneracy in a decaying window (interleaved healthy turns
    decrement rather than hard-reset), so escalation is sticky; and
  * escalates the corrective user nudge across three tiers, the last of
    which is a terminal "commit your best final state and stop" push so
    a stuck run stops wasting budget instead of grinding to the cap.

It touches no task-specific literal — the heuristic is purely
structural (sentence de-duplication) and applies to any repetition
attractor on any task class. A run that never degenerates sees this
processor as a complete no-op.
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

_TRUNC_MARKER = (
    "\n\n[response truncated by harness: the model produced a runaway / "
    "self-repeating response; the omitted middle was discarded to break the "
    "repetition loop]\n\n"
)

_NUDGE_FIRST = (
    "Your last turn ran into the output token limit without making real "
    "progress. Do NOT re-explain or continue the previous narration. In your "
    "next turn write at most two sentences of reasoning, then issue exactly "
    "ONE concrete Bash command that makes progress (inspect a file, run a "
    "script, or write output). Keep the response short."
)

_NUDGE_REPEAT = (
    "STOP. You are repeating the same narration (and/or the same command) turn "
    "after turn instead of acting. Abandon the current line of thought "
    "entirely. Do not write any prose analysis. Respond with a SINGLE short "
    "Bash tool call that does something you have NOT already tried: read the "
    "ACTUAL error output of your last real attempt, or try a materially "
    "different approach (different flags, tool, path, or algorithm). One "
    "command only."
)

_NUDGE_TERMINAL = (
    "You are stuck in a repetition loop and are about to exhaust your budget "
    "with nothing committed. Take ONE final concrete action now: with a single "
    "Bash command, write your best attempt at every required output file to "
    "the exact path(s) named in the task, using the most direct method "
    "available even if it is imperfect. A committed best-effort artifact is "
    "strictly better than looping until the budget is exhausted. Do not narrate."
)

_SENTENCE_SPLIT = re.compile(r"[.!?\n]+")


def _self_repetition_ratio(text: str) -> float:
    """Return the fraction of sentence-fragments that are duplicates.

    0.0 = every fragment unique; approaching 1.0 = almost all fragments
    are repeats of earlier ones. A degenerate narration loop scores high.
    """
    frags = [
        f.strip().lower()
        for f in _SENTENCE_SPLIT.split(text)
        if len(f.strip()) >= 12  # ignore trivial fragments
    ]
    if len(frags) < 6:
        return 0.0
    unique = len(set(frags))
    return 1.0 - (unique / len(frags))


class DegenerateRepetitionRecoveryProcessor(MultiHookProcessor):
    """Break both max_tokens and self-repetition attractor loops."""

    # Distinct group so it cleanly replaces the old singleton in config.
    _singleton_group = "tmax_degenerate_recovery"
    _order = 5

    def __init__(
        self,
        repeat_threshold: int = 2,
        terminal_threshold: int = 5,
        head_chars: int = 1200,
        tail_chars: int = 600,
        repetition_ratio: float = 0.55,
        min_repetition_chars: int = 900,
    ) -> None:
        self.repeat_threshold = max(1, int(repeat_threshold))
        self.terminal_threshold = max(self.repeat_threshold + 1, int(terminal_threshold))
        self.head_chars = max(0, int(head_chars))
        self.tail_chars = max(0, int(tail_chars))
        self.repetition_ratio = float(repetition_ratio)
        self.min_repetition_chars = max(0, int(min_repetition_chars))
        # Decaying degeneracy score: bumped on degenerate turns, decayed on
        # healthy ones so brief healthy turns don't fully reset escalation.
        self._score: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._score = 0
        self._pending_nudge = ""
        yield event

    def _is_degenerate(self, event: ModelResponseEvent) -> bool:
        length_truncated = event.finish_reason == "length" and not event.tool_calls
        if length_truncated:
            return True
        content = event.content or ""
        if len(content) >= self.min_repetition_chars:
            if _self_repetition_ratio(content) >= self.repetition_ratio:
                return True
        return False

    def _collapse(self, event: ModelResponseEvent) -> ModelResponseEvent:
        content = event.content or ""
        if len(content) > (self.head_chars + self.tail_chars + len(_TRUNC_MARKER)):
            collapsed = (
                content[: self.head_chars]
                + _TRUNC_MARKER
                + (content[-self.tail_chars :] if self.tail_chars else "")
            )
            return dataclasses.replace(event, content=collapsed)
        return event

    async def on_after_model(self, event: ModelResponseEvent):
        if not self._is_degenerate(event):
            # Healthy turn: decay the degeneracy score (don't hard-reset, so a
            # single interleaved healthy turn can't defeat escalation) and
            # clear any pending nudge only once we're back to a clean state.
            if self._score > 0:
                self._score -= 1
            if self._score == 0:
                self._pending_nudge = ""
            yield event
            return

        self._score += 1
        if self._score >= self.terminal_threshold:
            self._pending_nudge = _NUDGE_TERMINAL
        elif self._score >= self.repeat_threshold:
            self._pending_nudge = _NUDGE_REPEAT
        else:
            self._pending_nudge = _NUDGE_FIRST

        # Collapse runaway content regardless of whether a tool call rode
        # along — this is the key fix over the length-only predecessor.
        yield self._collapse(event)

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # The run loop may already have appended a passive continue nudge for
        # finish_reason=length. Replace it (contract: must not add a message
        # when the last role is already user; must not shrink the list).
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._score = 0
        self._pending_nudge = ""
        yield event
