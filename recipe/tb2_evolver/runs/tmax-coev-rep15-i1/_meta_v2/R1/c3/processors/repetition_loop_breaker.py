# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepetitionLoopBreaker — break degenerate near-identical narration loops.

Closes a systemic failure mode that the existing ``LengthTruncationRecovery``
processor does not catch: the model emits the *same* long reasoning block turn
after turn, sometimes hitting ``max_tokens`` (``finish_reason=length``) and
sometimes not, while the passive run-loop "please continue" nudge simply
re-primes the identical runaway generation. Even the escalating length-recovery
nudge is ignored because the model's own previous verbatim narration is still in
context and dominates the next completion. The cycle burns every remaining step
until ``budget_exceeded`` on a single task.

Why a separate processor (not just a knob on LengthTruncationRecovery):

* The loop is defined by *content self-similarity across turns*, not by
  ``finish_reason``. Several looping turns in the observed failure ended
  normally (``end_turn`` / tool-less) rather than on ``length`` — the length
  processor never fired on those.
* The decisive intervention is *pruning the poisoned duplicate assistant
  messages from context* so the model is no longer re-primed by its own
  runaway prose. A nudge alone cannot do this; the stale narration outweighs
  a one-line user message every time.

Mechanism:

* On each assistant turn with no tool call, fingerprint a normalised prefix of
  the content.
* When ``repeat_threshold`` consecutive tool-less turns share (near-)identical
  fingerprints, declare a loop.
* On the next ``before_model``: drop all but the most recent looping assistant
  message (and the passive "continue" nudges that trail them) from the context
  window, then append a single decisive redirect instructing the model to stop
  narrating and issue exactly one concrete command. This is content-agnostic:
  it names no task, path, or constant, so it generalises to any repetition
  loop the harness sees.
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

_WS_RE = re.compile(r"\s+")

_REDIRECT = (
    "SYSTEM INTERVENTION: your last several turns repeated the same reasoning "
    "almost verbatim without making progress. That stale narration has been "
    "removed from the conversation to stop the loop. Do NOT restate or continue "
    "any earlier analysis. In this turn, write at most ONE sentence, then issue "
    "exactly ONE Bash tool call that takes a concrete step — inspect the current "
    "state, or write/modify the required file. If you are unsure what to do "
    "next, run a single short command to observe the current state and decide "
    "from its output."
)


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text") or block.get("content") or ""
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return ""


def _fingerprint(content, prefix_chars: int) -> str:
    """Normalised, whitespace-collapsed, lowercased prefix of the content."""
    text = _extract_text(content)
    norm = _WS_RE.sub(" ", text).strip().lower()
    return norm[:prefix_chars]


class RepetitionLoopBreaker(MultiHookProcessor):
    """Detect near-identical repeated assistant narration and hard-break it."""

    _singleton_group = "tmax_repetition_loop_breaker"
    # Runs just after length-recovery (5) so its content collapse (if any) has
    # already been applied to this turn's fingerprint, but before compaction.
    _order = 6

    def __init__(
        self,
        repeat_threshold: int = 3,
        prefix_chars: int = 400,
        min_content_chars: int = 200,
    ) -> None:
        # Number of consecutive tool-less near-identical turns that trips the
        # breaker. 3 keeps normal short retries (which differ turn-to-turn)
        # safe while catching true verbatim loops quickly.
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.prefix_chars = max(80, int(prefix_chars))
        # Very short assistant turns (one-line "Let me run X") are not loops
        # even if repeated; only fingerprint substantial narration.
        self.min_content_chars = max(0, int(min_content_chars))
        self._last_fp: str = ""
        self._run: int = 0
        self._pending_break: bool = False

    async def on_task_start(self, event: TaskStartEvent):
        self._last_fp = ""
        self._run = 0
        self._pending_break = False
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        # A turn that issued a tool call is making progress — reset.
        if event.tool_calls:
            self._last_fp = ""
            self._run = 0
            self._pending_break = False
            yield event
            return

        text = _extract_text(event.content or "")
        if len(text.strip()) < self.min_content_chars:
            # Too short to be a meaningful narration loop; do not accumulate.
            self._last_fp = ""
            self._run = 0
            yield event
            return

        fp = _fingerprint(event.content or "", self.prefix_chars)
        if fp and fp == self._last_fp:
            self._run += 1
        else:
            self._run = 1
            self._last_fp = fp

        if self._run >= self.repeat_threshold:
            self._pending_break = True

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_break:
            yield event
            return
        self._pending_break = False
        self._last_fp = ""
        self._run = 0

        msgs = list(event.messages)
        if not msgs:
            yield event
            return

        # Identify the trailing block of looping assistant turns + the passive
        # "continue" user nudges interleaved with them, and prune all but keep
        # the conversation coherent. We remove trailing assistant messages that
        # share the looping fingerprint and any adjacent passive-continue user
        # messages, then append the decisive redirect. The first message
        # (task/system anchor) is always preserved.
        target_fp = ""
        # Recompute the loop fingerprint from the most recent qualifying
        # assistant message.
        for m in reversed(msgs):
            if getattr(m, "role", None) == "assistant" and not getattr(m, "tool_calls", ()):  # noqa: E501
                cand = _fingerprint(getattr(m, "content", ""), self.prefix_chars)
                if cand:
                    target_fp = cand
                    break

        if not target_fp:
            # Fall back to a plain redirect append.
            yield self._append_redirect(event, msgs)
            return

        pruned: list[Message] = []
        removed = 0
        for idx, m in enumerate(msgs):
            role = getattr(m, "role", None)
            # Never drop the first message (task anchor).
            if idx == 0:
                pruned.append(m)
                continue
            if role == "assistant" and not getattr(m, "tool_calls", ()):
                if _fingerprint(getattr(m, "content", ""), self.prefix_chars) == target_fp:
                    removed += 1
                    continue  # drop this looping duplicate
            if role == "user":
                utext = _extract_text(getattr(m, "content", "")).strip().lower()
                if "cut off by the token limit" in utext or utext.startswith(
                    "your last turn ran into the output token limit"
                ):
                    removed += 1
                    continue  # drop passive/soft continue nudges tied to loop
            pruned.append(m)

        if removed == 0:
            yield self._append_redirect(event, msgs)
            return

        yield self._append_redirect(event, pruned)

    def _append_redirect(self, event: BeforeModelEvent, msgs: list[Message]):
        # Contract: must not leave two trailing user messages that duplicate.
        # If the last message is already a user turn, replace it; else append.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs = list(msgs)
            msgs[-1] = Message(role="user", content=_REDIRECT)
        else:
            msgs = list(msgs) + [Message(role="user", content=_REDIRECT)]
        return dataclasses.replace(event, messages=tuple(msgs))

    async def on_task_end(self, event: TaskEndEvent):
        self._last_fp = ""
        self._run = 0
        self._pending_break = False
        yield event
