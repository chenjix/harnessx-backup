# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LengthLoopDeprimeProcessor — break self-priming max_tokens repetition loops.

Systemic failure mode observed on the weak-model Tmax eval: the model emits a
paragraph of narration (no tool call), hits ``max_tokens`` (``finish_reason ==
"length"``), the run loop appends a passive "please continue" nudge, and the
model re-emits *the same* narration next turn. Because each truncated turn's
full narration is persisted to history, the model sees its own repeated prose
5-6 times in context, which re-primes the exact same runaway generation. The
cycle burns the entire step budget on one task (observed: 15-17 consecutive
length-truncations across tasks 264 / 958 / 1818, all ``budget_exceeded``).

The stock ``LengthTruncationRecoveryProcessor`` collapses only the *current*
turn (head+tail) and nudges. That mitigates but does not break the loop: the
collapsed-but-still-substantial narration keeps accumulating and re-priming.

This processor keeps the first-truncation behaviour (collapse head+tail, gentle
nudge) but, once truncations recur consecutively, **de-primes** the loop:

* it collapses the current runaway turn to a tiny marker stub (dropping the
  repeated narration entirely, not just its middle) so the persisted history
  stops re-showing the model its own loop text, and
* it replaces the passive continuation with an escalating hard directive that
  forbids prose and demands exactly one concrete Bash command.

Contract-safe: mutates only the current ``ModelResponseEvent.content`` in
``on_after_model`` (no ``messages`` field there) and, in ``on_before_model``,
either replaces the last user message (when the run loop already appended its
passive nudge) or appends exactly one user message when the tail is not a user
turn — never removes or reorders history.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

_TRUNC_MARKER = (
    "\n\n[response truncated by harness: the model hit the output token limit "
    "without issuing a tool call; the omitted middle was discarded to prevent a "
    "repetition loop]\n\n"
)

# On a *repeated* truncation the whole narration is dropped: leaving it in
# history is what re-primes the loop. Only this compact stub is persisted.
_DEPRIME_STUB = (
    "[prior turn discarded by harness: the model produced only narration and hit "
    "the output token limit again — repeated no-op narration was removed to break "
    "a repetition loop]"
)

_NUDGE_FIRST = (
    "Your last turn ran into the output token limit without running any command. "
    "Do NOT re-explain or continue the previous narration. In your next turn write "
    "at most two sentences of reasoning, then issue exactly ONE concrete Bash "
    "command that makes progress (inspect a file, run a script, or write output). "
    "Keep the response short."
)

_NUDGE_REPEAT = (
    "STOP. You have hit the output token limit several turns in a row — you are "
    "repeating narration instead of acting, and that prior narration has now been "
    "discarded from the conversation. Do not restate your plan or analysis. Your "
    "entire next reply must be a SINGLE short Bash tool call and nothing else: "
    "either (a) run one quick command to inspect the current concrete state "
    "(e.g. `ls`, `cat`, a status check), or (b) write one required output file. "
    "No prose. One command."
)


class LengthLoopDeprimeProcessor(MultiHookProcessor):
    """Break the max_tokens repetition loop by de-priming history and hard-nudging."""

    _singleton_group = "tmax_length_recovery"  # same group: replaces the stock one
    _order = 5

    def __init__(
        self,
        repeat_threshold: int = 2,
        head_chars: int = 1200,
        tail_chars: int = 600,
        deprime_after: int = 2,
    ) -> None:
        self.repeat_threshold = max(1, int(repeat_threshold))
        # Once consecutive truncations reach ``deprime_after`` we stop keeping
        # any of the runaway narration in history.
        self.deprime_after = max(1, int(deprime_after))
        self.head_chars = max(0, int(head_chars))
        self.tail_chars = max(0, int(tail_chars))
        self._consecutive: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._consecutive = 0
        self._pending_nudge = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        length_truncated = event.finish_reason == "length" and not event.tool_calls
        if not length_truncated:
            self._consecutive = 0
            self._pending_nudge = ""
            yield event
            return

        self._consecutive += 1
        self._pending_nudge = (
            _NUDGE_REPEAT if self._consecutive >= self.repeat_threshold else _NUDGE_FIRST
        )

        content = event.content or ""

        # Repeated truncation → drop the narration entirely (de-prime the loop).
        if self._consecutive >= self.deprime_after:
            yield dataclasses.replace(event, content=_DEPRIME_STUB)
            return

        # First truncation → keep head+tail so useful early reasoning survives.
        if len(content) > (self.head_chars + self.tail_chars + len(_TRUNC_MARKER)):
            collapsed = (
                content[: self.head_chars]
                + _TRUNC_MARKER
                + (content[-self.tail_chars :] if self.tail_chars else "")
            )
            yield dataclasses.replace(event, content=collapsed)
        else:
            yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Run loop may already have appended a passive "continue" user message
        # for finish_reason=length. Replace it (do not add a second) to respect
        # the before_model +1/last-role-user contract.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._consecutive = 0
        self._pending_nudge = ""
        yield event
