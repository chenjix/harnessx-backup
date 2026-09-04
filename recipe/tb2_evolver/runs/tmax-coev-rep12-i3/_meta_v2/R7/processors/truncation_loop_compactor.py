# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""TruncationLoopCompactor — collapse repeated max_tokens-truncation turns.

Closes a systemic *self-reinforcing verbosity loop* observed on the eval
model (a small, chatty model run with a hard ``max_tokens`` output cap).

Failure shape (from the r6 trajectories):

    assistant  ~4096-token prose, finish_reason=length, NO tool call
    user       "Your previous response was cut off by the token limit.
                Please continue from where you left off."   (run-loop default)
    assistant  ~identical prose again ...
    user       (same passive nudge) ...
    ... repeated 8-23 times until budget_exceeded ...

The run loop appends a *passive* "please continue" nudge after every
length-truncation. Because the model then sees a growing wall of its own
near-identical truncated narration (measured: the SAME assistant prefix
repeated 10-13 times in a single run), it pattern-matches on that wall and
reproduces it — the passive nudge literally instructs it to *continue* the
runaway generation. The pre-existing ``LengthTruncationRecoveryProcessor``
collapses each *individual* truncated turn and swaps the nudge in
``on_before_model``, but that edit is ephemeral (it never lands in
``state.raw_messages``) so the accumulated wall of duplicates keeps growing
and keeps priming the loop.

This processor runs at ``on_step_start`` (after context assembly / after the
CompactionProcessor at order 8). When it detects a run of >= ``min_run``
consecutive near-identical *truncated* assistant turns — each optionally
followed by the run loop's passive "continue" nudge — it **collapses the run
into a single note** plus one actionable directive. Because the run loop
persists a step_start structural history change into ``state.raw_messages``
(the auto-boundary path), the collapse is durable: the model no longer sees
its own repetition wall and is redirected to emit one concrete Bash command.

Safety / scope:
* Only touches consecutive *assistant* turns with no ``tool_calls`` whose
  normalized prefixes are near-identical, plus the immediately-following
  passive-continue *user* messages. Never touches ``tool`` messages,
  assistant turns that carry a tool call, the system message, or the first
  (task-description) message.
* Requires a run of at least ``min_run`` (default 3) duplicates before it
  fires, so ordinary iteration (a couple of retries) is left untouched —
  normal passing trajectories never repeat one truncated narration 3x.
* Never blocks or drops a tool call, never fabricates a tool result. It only
  compresses redundant self-narration the model already produced.

No task ids, paths, commands, or domain literals are hard-coded — the trigger
is a structural repetition pattern, so it generalizes to any task that falls
into the truncation loop.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    Message,
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Substring that identifies the run loop's default passive length-truncation
# nudge (see harnessx/core/runloop.py). Matched loosely so minor wording drift
# still classifies the message as a passive continue nudge.
_PASSIVE_NUDGE_MARKER = "cut off by the token limit"

_COLLAPSE_NOTE = (
    "[harness: the previous {n} turns hit the output token limit while writing "
    "long reasoning WITHOUT running any command, and repeated near-identical "
    "text. Those redundant turns were collapsed to keep context clean. "
    "STOP writing long analysis. Your next turn must be SHORT: at most two "
    "sentences, then exactly ONE concrete Bash command that makes progress "
    "(inspect a file, run a script, or write the required output). Do not "
    "re-explain the plan.]"
)


def _norm_prefix(text: str, n: int = 80) -> str:
    """Whitespace-normalized leading-``n``-char signature of a message."""
    return " ".join((text or "").split())[:n]


class TruncationLoopCompactor(MultiHookProcessor):
    """Collapse a run of repeated max_tokens-truncation turns into one directive."""

    _singleton_group = "tmax_truncation_loop_compactor"
    # Run after context assembly and after CompactionProcessor (order 8), so we
    # operate on the assembled window the model will actually see.
    _order = 20

    def __init__(
        self,
        min_run: int = 3,
        prefix_chars: int = 80,
        max_content_chars: int = 4000,
    ) -> None:
        # Minimum number of consecutive near-identical truncated assistant
        # turns before we collapse. >=1; default 3 leaves normal iteration alone.
        self.min_run = max(2, int(min_run))
        self.prefix_chars = max(16, int(prefix_chars))
        # An assistant turn only counts as a "truncated narration" candidate when
        # it is a large prose blob with no tool call. Anything shorter is treated
        # as ordinary content and never collapsed.
        self.max_content_chars = max(200, int(max_content_chars))

    async def on_task_start(self, event: TaskStartEvent):
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        yield event

    def _is_truncated_narration(self, m: Message) -> bool:
        if getattr(m, "role", None) != "assistant":
            return False
        if getattr(m, "tool_calls", None):
            return False
        content = getattr(m, "content", "") or ""
        # Must be substantive prose; tiny assistant lines (e.g. "Let me run it")
        # are normal and must not be collapsed.
        return len(content) >= 200

    def _is_passive_nudge(self, m: Message) -> bool:
        return (
            getattr(m, "role", None) == "user"
            and _PASSIVE_NUDGE_MARKER in (getattr(m, "content", "") or "")
        )

    async def on_step_start(self, event: StepStartEvent):
        msgs = list(event.messages)
        if len(msgs) < self.min_run + 1:
            yield event
            return

        # Identify the maximal run(s) of near-identical truncated assistant
        # narration turns, each optionally followed by a passive continue nudge.
        # Collapse each qualifying run in place.
        out: list[Message] = []
        i = 0
        n = len(msgs)
        changed = False
        while i < n:
            m = msgs[i]
            if not self._is_truncated_narration(m):
                out.append(m)
                i += 1
                continue

            # Start a run of near-identical truncated narration turns.
            sig = _norm_prefix(getattr(m, "content", ""), self.prefix_chars)
            run_idxs = [i]
            j = i + 1
            while j < n:
                # Skip an interleaved passive continue nudge without breaking the run.
                if self._is_passive_nudge(msgs[j]):
                    j += 1
                    continue
                if (
                    self._is_truncated_narration(msgs[j])
                    and _norm_prefix(getattr(msgs[j], "content", ""), self.prefix_chars) == sig
                ):
                    run_idxs.append(j)
                    j += 1
                    continue
                break

            run_len = len(run_idxs)
            if run_len >= self.min_run:
                # Collapse: keep the FIRST narration turn (trimmed), drop the rest
                # and any passive nudges inside the run, then append one directive
                # user message so the assembled window still ends on a "user" turn
                # when appropriate.
                first = msgs[run_idxs[0]]
                first_content = (getattr(first, "content", "") or "")[: self.max_content_chars]
                out.append(dataclasses.replace(first, content=first_content))
                note = Message(role="user", content=_COLLAPSE_NOTE.format(n=run_len))
                # Avoid emitting two consecutive user messages: if the message
                # that follows the collapsed run is itself a user turn, fold the
                # directive into it instead of inserting a separate user message.
                if j < n and getattr(msgs[j], "role", None) == "user":
                    nxt = msgs[j]
                    merged = _COLLAPSE_NOTE.format(n=run_len) + "\n\n" + (
                        getattr(nxt, "content", "") or ""
                    )
                    out.append(Message(role="user", content=merged))
                    j += 1
                else:
                    out.append(note)
                changed = True
                i = j
            else:
                # Run too short to be a loop; leave the turns untouched.
                for k in range(i, j):
                    out.append(msgs[k])
                i = j

        if not changed:
            yield event
            return

        # Guard: never emit an empty window and never drop the leading message.
        if not out or out[0] is not msgs[0]:
            # If we somehow disturbed the head, fall back to no-op to stay safe.
            if not out:
                yield event
                return
            if out[0] is not msgs[0]:
                out = [msgs[0]] + [x for x in out if x is not msgs[0]]

        yield dataclasses.replace(event, messages=tuple(out))
