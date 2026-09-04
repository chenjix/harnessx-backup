# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LengthLoopBreaker — escalate (prune + hard-stop) a *repeated* length-truncation
loop that the passive continue-nudge cannot break.

Failure mode this closes
------------------------
The Tmax agent repeatedly hits ``max_tokens`` mid-generation with **no tool
call** (``finish_reason == "length"``). The run loop appends a passive
"Your previous response was cut off by the token limit. Please continue from
where you left off." user message, which re-primes the *same* runaway
narration. The model emits another 4096-token block of near-identical prose,
truncates again, and the cycle repeats — burning the entire step budget with
zero tool calls and zero progress.

Observed on the R0 evolve set (all reward=0):

* ``task_000010_644ab1c2`` — 16 of 33 assistant turns were content-only
  length-truncations; the model even narrated "stop repeating myself" but
  never emitted a Bash call, exhausted 80 steps (budget_exceeded).
* ``task_000958_4bb2b05d`` — 16/33 content-only length-truncations; model
  narrated "I'm stuck in a loop and hitting token limits", 80 steps.
* ``task_001857_24daeef3`` — 13/33.  ``task_001116_4c65c2f5`` — 10/48.
  ``task_001098_f5acdd79`` — 7.  ``task_001032_1adaccb9`` / ``task_001837``
  — 6 each.  A recurring, systemic driver of failed rounds.

Why the existing pipeline misses it
-----------------------------------
* ``LengthTruncationRecoveryProcessor`` collapses the *current* truncated
  turn and rewrites the passive nudge into a "issue one Bash call" nudge —
  but it only ever *nudges*. It never (a) removes the growing pile of
  collapsed narration turns that keep re-priming the loop, nor (b) escalates
  to a hard stop when nudging has demonstrably failed for many turns. So a
  genuinely stuck model spins its whole budget.
* ``CyclicLoopBreaker`` keys on identical *tool-call* cycles
  (``on_after_tool``); these turns carry **no tool call**, so it never fires.
* ``ParseRetryProcessor`` counts parse errors, not length truncations.

Design — escalate: prune, then stop
------------------------------------
This processor complements (does not replace) ``LengthTruncationRecovery``:

1. ``on_after_model``: count *consecutive* length-truncations (a
   ``finish_reason == "length"`` model turn with no tool calls). Any turn
   that carries a tool call resets the counter to 0 — real progress clears
   the loop.
2. ``on_before_model``: once the consecutive count reaches
   ``prune_after``, the accumulated content-only truncation turns in the
   context tail are the fuel of the loop. Drop them (keeping the first
   message and every tool exchange intact) and replace the trailing passive
   "continue" nudge with a single hard directive to emit ONE Bash command.
   This removes the re-priming narration so the next generation starts clean.
3. Safety net: if the consecutive count reaches ``hard_stop_after`` the
   nudging+pruning has failed — the model is not recovering. Raise
   ``LoopDetectedError`` so the remaining step budget is reclaimed for the
   orchestrator rather than fully burned on a dead loop.

Contains no task-specific constants, paths, commands, or answers — it keys
purely on the structural property "consecutive content-only length
truncations". Fires on ANY task that enters the loop; is inert otherwise.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runloop import LoopDetectedError

# Substrings identifying the run loop's passive continue nudge and the sibling
# LengthTruncationRecoveryProcessor's collapse marker. Matching either lets us
# recognise a truncation turn / its follow-up nudge without task knowledge.
_PASSIVE_NUDGE_MARKER = "cut off by the token limit"
_COLLAPSE_MARKER = "response truncated by harness"

_HARD_DIRECTIVE = (
    "You have hit the output token limit several turns in a row without running "
    "any command — that is a repetition loop, not progress, and your earlier "
    "narration has been dropped from the context. Do NOT explain, plan, or "
    "reason in prose now. Emit exactly ONE short Bash tool call and nothing "
    "else: either write the required output file(s) to the exact path named in "
    "the task, or run a single quick command to inspect the current state. If "
    "the deliverable already exists, run one command to verify it and then stop."
)


class LengthLoopBreaker(MultiHookProcessor):
    """Prune and hard-stop a repeated content-only length-truncation loop.

    Args:
        prune_after:    Consecutive content-only length truncations after which
                        the accumulated truncation narration is pruned from the
                        context tail and a hard directive is injected
                        (default 3).
        hard_stop_after: Consecutive content-only length truncations after which
                        the run is terminated with ``LoopDetectedError`` to
                        reclaim budget (default 6). Must exceed ``prune_after``.
        keep_recent_pairs: How many of the most-recent (assistant, nudge) pairs
                        to leave in place when pruning, so the model still sees
                        that it was just told to stop (default 1).
    """

    _singleton_group = "length_loop_breaker"
    # After LengthTruncationRecoveryProcessor (_order=5) so its per-turn
    # collapse+nudge runs first; this layer only escalates when that has
    # repeatedly failed. Before CompactionProcessor so pruning happens on the
    # pre-compaction message list.
    _order = 6

    def __init__(
        self,
        prune_after: int = 3,
        hard_stop_after: int = 6,
        keep_recent_pairs: int = 1,
    ) -> None:
        self.prune_after = max(1, int(prune_after))
        self.hard_stop_after = max(self.prune_after + 1, int(hard_stop_after))
        self.keep_recent_pairs = max(0, int(keep_recent_pairs))
        self._consecutive: int = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._consecutive = 0
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        length_truncated = event.finish_reason == "length" and not event.tool_calls
        if length_truncated:
            self._consecutive += 1
        else:
            # Any productive turn (tool call, or a clean stop) clears the loop.
            self._consecutive = 0
        yield event

    @staticmethod
    def _is_truncation_narration(msg: Message) -> bool:
        """An assistant turn with content and no tool calls — i.e. prose-only.

        A plain content-only assistant turn inside a truncation streak is loop
        fuel; the collapse marker (if the sibling processor collapsed it) is a
        strong positive but not required.
        """
        if getattr(msg, "role", None) != "assistant":
            return False
        if getattr(msg, "tool_calls", None):
            return False
        content = getattr(msg, "content", None) or ""
        return bool(content.strip())

    @staticmethod
    def _is_passive_nudge(msg: Message) -> bool:
        if getattr(msg, "role", None) != "user":
            return False
        content = getattr(msg, "content", None) or ""
        return _PASSIVE_NUDGE_MARKER in content or _COLLAPSE_MARKER in content

    def _apply_directive(self, event: BeforeModelEvent, msgs: list) -> BeforeModelEvent:
        """Ensure the context ends with the single hard directive as a user turn.

        Replaces a trailing passive nudge in place (avoids a +1 insertion the
        contract validator flags); otherwise appends one user message.
        """
        out = list(msgs)
        if out and getattr(out[-1], "role", None) == "user":
            out[-1] = Message(role="user", content=_HARD_DIRECTIVE)
        else:
            out.append(Message(role="user", content=_HARD_DIRECTIVE))
        return dataclasses.replace(event, messages=tuple(out))

    async def on_before_model(self, event: BeforeModelEvent):
        if self._consecutive < self.prune_after:
            yield event
            return

        # Hard safety net: nudging + pruning has failed for many turns running.
        if self._consecutive >= self.hard_stop_after:
            raise LoopDetectedError(
                f"Length-truncation loop unbroken: {self._consecutive} consecutive "
                f"content-only max_tokens truncations with no tool call; the agent "
                f"is not recovering. Terminating to reclaim the remaining step "
                f"budget."
            )

        msgs = list(event.messages)
        if len(msgs) <= 1:
            yield event
            return

        first = msgs[0]
        tail_start = 1  # never touch the first (task) message

        # Walk backward from the tail collecting loop fuel (content-only
        # assistant turns + passive continue nudges). Stop at the first turn
        # that is neither — a real tool exchange or assistant-with-toolcall
        # bounds the fuel region.
        fuel_idx: list[int] = []
        i = len(msgs) - 1
        while i >= tail_start:
            m = msgs[i]
            if self._is_passive_nudge(m) or self._is_truncation_narration(m):
                fuel_idx.append(i)
                i -= 1
                continue
            break

        if not fuel_idx:
            yield self._apply_directive(event, msgs)
            return

        fuel_idx.sort()
        # Keep the most-recent fuel messages so the model retains the immediate
        # "you were just told to stop" context; prune the rest.
        keep_n = self.keep_recent_pairs * 2
        prunable = fuel_idx[: max(0, len(fuel_idx) - keep_n)]
        prunable_set = set(prunable)

        if not prunable_set:
            yield self._apply_directive(event, msgs)
            return

        kept = [m for j, m in enumerate(msgs) if j not in prunable_set]
        # Contract guard: never empty the list, always keep the first message.
        if not kept:
            kept = [first]
        elif kept[0] is not first:
            kept = [first] + [m for m in kept if m is not first]

        yield self._apply_directive(event, kept)
