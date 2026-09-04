# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ContextOverflowGuard — bound per-message and total context size at step_start.

Motivation (harness deficiency, not a model-knowledge gap)
----------------------------------------------------------
The provider call in the run loop is not wrapped in try/except, so a provider
``400 BadRequest`` (input exceeds the model's real max-context window) propagates
uncaught and terminates the task with ``exit_reason=error`` at step ~0/1 —
before the agent takes any recoverable action.

The recurring trigger is a *single runaway assistant turn*: the model emits one
enormous ``content`` string (observed 170k–344k characters across many distinct
tasks). On the very next request the transcript exceeds the server's hard input
limit and the whole run dies. The existing ``CompactionProcessor`` cannot help
here because (a) its ``token_threshold`` is tuned above the server's real limit,
and (b) it summarises *older* messages while leaving the single oversized recent
turn intact.

The stock token counter (``rough_token_count`` / cl100k_base) materially
*undercounts* the real tokenizer for this model and ignores non-``content``
fields, so a token-based budget is unreliable. This guard therefore works on
**character length**, which is a stable, provider-agnostic upper bound on the
transcript size that actually gets serialised into the request body.

Mechanism
---------
Runs on ``on_step_start`` (the only hook where structural history edits are
contract-legal). For each message in the assembled window:

1. If any single message's textual ``content`` exceeds ``per_message_char_cap``,
   replace it with a head + tail excerpt joined by a truncation marker. This
   defuses the runaway single-turn dump that is the dominant failure mode.
2. If the whole transcript still exceeds ``total_char_budget``, shrink the
   largest messages further (oldest-largest first, always keeping the first
   message and the most recent ``keep_recent`` messages readable) until the
   transcript fits.

Truncation is done **in place**: message count, roles, ordering, and
``tool_call_id`` links are all preserved, so the raw/effective track invariant
and the step_start message contract both hold. Only the ``content`` field of
oversized messages shrinks. Messages whose ``content`` is a multimodal block
list are left untouched (they are bounded elsewhere and are not the failure
shape observed here).

This is deliberately a *class* fix: it fires on any task whose transcript grows
past a safe size, with no task-specific literals. Tasks that never approach the
cap (every passing trajectory observed had a max message well under 6k chars)
are structurally unaffected.
"""

from __future__ import annotations

import dataclasses
import logging

from harnessx.core.events import (
    StepStartEvent,
    Message,
    rough_token_count,
    _extract_text,
)
from harnessx.core.processor import MultiHookProcessor

logger = logging.getLogger(__name__)

_TRUNC_MARKER = "\n\n... [ContextOverflowGuard: {dropped} chars elided to fit the model context window] ...\n\n"


class ContextOverflowGuard(MultiHookProcessor):
    """Cap per-message and total transcript character length at step_start.

    Args:
        per_message_char_cap: Any single message whose text ``content`` exceeds
            this many characters is head/tail truncated. Chosen well above any
            legitimate tool output / message observed on passing tasks, and well
            below the char-equivalent of the model's real context window, so it
            only ever fires on runaway turns.
        total_char_budget: Ceiling on the summed text ``content`` of the whole
            window. When exceeded after the per-message pass, the largest
            messages are shrunk further until the transcript fits.
        keep_recent: Number of most-recent messages exempt from the *secondary*
            (total-budget) shrink pass so the live working context stays intact.
            The per-message cap still applies to them.
        min_message_chars: Floor a message is never shrunk below during the
            secondary pass (keeps every turn at least minimally legible).
    """

    required_providers: frozenset = frozenset()

    _singleton_group = "context_overflow_guard"
    # After compaction (8) so we cap whatever survives compaction; before
    # parse/verify processors so the request that actually leaves is bounded.
    _order = 9

    def __init__(
        self,
        per_message_char_cap: int = 120_000,
        total_char_budget: int = 280_000,
        keep_recent: int = 4,
        min_message_chars: int = 2_000,
    ) -> None:
        self.per_message_char_cap = int(per_message_char_cap)
        self.total_char_budget = int(total_char_budget)
        self.keep_recent = max(0, int(keep_recent))
        self.min_message_chars = max(256, int(min_message_chars))

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _is_text(content) -> bool:
        return isinstance(content, str)

    def _truncate_text(self, text: str, cap: int) -> str:
        """Head+tail truncation preserving both ends of an oversized string."""
        if len(text) <= cap:
            return text
        # Reserve room for the marker; split remaining budget head/tail.
        marker = _TRUNC_MARKER.format(dropped=len(text) - cap)
        budget = max(self.min_message_chars, cap - len(marker))
        head = budget * 2 // 3
        tail = budget - head
        return text[:head] + marker + (text[-tail:] if tail > 0 else "")

    def _cap_message(self, m: Message, cap: int) -> Message:
        if not self._is_text(m.content):
            return m
        if len(m.content) <= cap:
            return m
        return dataclasses.replace(m, content=self._truncate_text(m.content, cap))

    # -- hook ------------------------------------------------------------

    async def on_step_start(self, event: StepStartEvent):
        source = event.messages if event.messages else event.raw_messages
        if not source:
            yield event
            return

        msgs = list(source)
        n = len(msgs)
        changed = False

        # Pass 1 — per-message cap (defuses the runaway single-turn dump).
        for i in range(n):
            capped = self._cap_message(msgs[i], self.per_message_char_cap)
            if capped is not msgs[i]:
                msgs[i] = capped
                changed = True

        # Pass 2 — total budget. Shrink largest eligible messages first.
        def total_chars() -> int:
            return sum(len(m.content) for m in msgs if self._is_text(m.content))

        if total_chars() > self.total_char_budget:
            # Eligible = not the first message, not within keep_recent tail.
            recent_start = max(1, n - self.keep_recent)
            eligible = list(range(1, recent_start))
            # Largest first.
            eligible.sort(key=lambda i: len(msgs[i].content) if self._is_text(msgs[i].content) else 0, reverse=True)

            guard = 0
            while total_chars() > self.total_char_budget and guard < n + 1:
                guard += 1
                progressed = False
                for i in eligible:
                    if total_chars() <= self.total_char_budget:
                        break
                    m = msgs[i]
                    if not self._is_text(m.content):
                        continue
                    cur = len(m.content)
                    if cur <= self.min_message_chars:
                        continue
                    over = total_chars() - self.total_char_budget
                    new_cap = max(self.min_message_chars, cur - over)
                    if new_cap < cur:
                        msgs[i] = dataclasses.replace(m, content=self._truncate_text(m.content, new_cap))
                        changed = True
                        progressed = True
                if not progressed:
                    break

        if not changed:
            yield event
            return

        new_messages = tuple(msgs)
        new_count = rough_token_count(list(new_messages))

        logger.info(
            "ContextOverflowGuard: bounded transcript (%d msgs, ~%d chars total)",
            len(new_messages),
            sum(len(_extract_text(m.content)) for m in new_messages),
        )

        yield dataclasses.replace(
            event,
            messages=new_messages,
            raw_messages=new_messages,
            token_count=new_count,
        )
