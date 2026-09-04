# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LengthTruncationRecoveryProcessor with a hard-stop escalation.

Failure mode this closes
------------------------
The stock ``LengthTruncationRecoveryProcessor`` handles the "model hits
``max_tokens`` with no tool call, run loop appends a passive 'please continue'
nudge, which re-primes the same runaway generation" loop by (a) collapsing the
runaway content and (b) replacing the passive nudge with a corrective
"issue one Bash call" instruction that escalates on consecutive truncations.

But it *only ever nudges* — it has no upper bound. When a model ignores the
nudge and keeps emitting a full ``max_tokens`` no-tool-call response every
turn, the loop runs until the whole step / wall-clock budget is gone. This is
not hypothetical: on the evolve set, ``task_000010_644ab1c2``
(system_administration) produced **13 consecutive** ``stop_reason=length``
no-tool-call turns (steps ~39-69 of a 39-effective-step run) — roughly a third
of the run spent re-narrating the same analysis-paralysis, each turn burning a
full 4096-token generation. The stock processor collapsed the content and sent
the corrective nudge every one of those 13 turns; the model ignored it every
time. Nothing in the pipeline could terminate the loop:

* ``CyclicLoopBreaker`` keys on repeating *tool-call* cycles; a
  no-tool-call length loop never enters its window.
* ``ParseRetryProcessor`` counts parse errors, not length truncations.
* ``LengthTruncationRecoveryProcessor`` (stock) nudges without any cap.

Design — nudge, then hard-stop
------------------------------
This processor is a drop-in superset of the stock one. It keeps the collapse +
escalating-nudge behaviour verbatim, and adds ONE new mechanism:

* After ``hard_stop_threshold`` *consecutive* length-truncation turns with no
  tool call, it raises ``LoopDetectedError`` from ``on_after_model``. The run
  loop catches this and exits cleanly with ``exit_reason='loop_detected'``
  (NOT ``error``), recovering the best assistant output and — crucially —
  leaving the container's final filesystem state intact for the verifier.

Why terminating is the right move here (not "nudge harder")
-----------------------------------------------------------
Unlike a tool-call loop where the agent might still recover and do real work,
a model that has emitted ``hard_stop_threshold`` back-to-back full-length
no-tool-call turns has demonstrably stopped acting: every turn is pure
narration that never reaches a command. Continuing only drains the shared
budget (step / wall-clock) that other tasks in the round need, and cannot
improve the already-frozen workspace. Stopping early reclaims that budget and
preserves whatever on-disk state the agent produced before it froze.

The counter resets to zero on ANY turn that carries a tool call or that does
not hit the length limit, so a single stray truncation followed by normal
progress never trips the hard stop. Threshold defaults to a conservative value
so only a genuinely stuck run is terminated.

Contains no task-specific constants, paths, commands, or answers — it keys
purely on the structural property "consecutive full-length no-tool-call turns".
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
from harnessx.core.runloop import LoopDetectedError

_HEAD_CHARS = 1200
_TAIL_CHARS = 600
_TRUNC_MARKER = (
    "\n\n[response truncated by harness: the model hit the output token limit "
    "without issuing a tool call; the omitted middle was discarded to prevent a "
    "repetition loop]\n\n"
)

_NUDGE_FIRST = (
    "Your last turn ran into the output token limit without running any command. "
    "Do NOT re-explain or continue the previous narration. In your next turn write "
    "at most two sentences of reasoning, then issue exactly ONE concrete Bash "
    "command that makes progress (inspect a file, run a script, or write output). "
    "Keep the response short."
)

_NUDGE_REPEAT = (
    "STOP. You have now hit the output token limit multiple turns in a row, which "
    "means you are repeating yourself instead of acting. Abandon the current line "
    "of narration entirely. Do not write any prose analysis. Respond with a SINGLE "
    "short Bash tool call that either (a) writes the required output file(s) to the "
    "path named in the task, or (b) runs a quick command to check the current state "
    "so you can decide the next concrete step. One command only."
)


class LengthTruncationRecoveryProcessor(MultiHookProcessor):
    """Break the max_tokens repetition loop, redirect, then hard-stop if unbroken.

    Args:
        repeat_threshold:   Consecutive length truncations at which the nudge
                            escalates from _NUDGE_FIRST to _NUDGE_REPEAT
                            (default 2).
        head_chars:         Head slice kept when collapsing runaway content.
        tail_chars:         Tail slice kept when collapsing runaway content.
        hard_stop_threshold: Consecutive length-truncation no-tool-call turns
                            after which the run is terminated with
                            LoopDetectedError (exit_reason='loop_detected').
                            Set to 0 (or negative) to disable the hard stop and
                            recover the stock nudge-forever behaviour.
                            Default 6 — well above repeat_threshold so the
                            escalating nudge gets several chances to work first.
    """

    _singleton_group = "tmax_length_recovery"
    _order = 5

    def __init__(
        self,
        repeat_threshold: int = 2,
        head_chars: int = _HEAD_CHARS,
        tail_chars: int = _TAIL_CHARS,
        hard_stop_threshold: int = 6,
    ) -> None:
        self.repeat_threshold = max(1, int(repeat_threshold))
        self.head_chars = max(0, int(head_chars))
        self.tail_chars = max(0, int(tail_chars))
        # <= 0 disables the hard stop.
        self.hard_stop_threshold = int(hard_stop_threshold)
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

        # Hard-stop escalation: the model has ignored the corrective nudge for
        # hard_stop_threshold consecutive full-length no-tool-call turns. It is
        # not acting; continuing only drains the shared budget. Emit the
        # (collapsed) event so the trace/state is consistent, then raise to end
        # the run cleanly (exit_reason='loop_detected', workspace preserved).
        if (
            self.hard_stop_threshold > 0
            and self._consecutive >= self.hard_stop_threshold
        ):
            yield self._maybe_collapse(event)
            raise LoopDetectedError(
                f"Length-truncation loop unbroken: {self._consecutive} consecutive "
                f"max_tokens no-tool-call turns despite corrective nudges — the "
                f"model has stopped acting; terminating to reclaim budget and "
                f"preserve the current workspace state"
            )

        self._pending_nudge = (
            _NUDGE_REPEAT if self._consecutive >= self.repeat_threshold else _NUDGE_FIRST
        )
        yield self._maybe_collapse(event)

    def _maybe_collapse(self, event: ModelResponseEvent) -> ModelResponseEvent:
        content = event.content or ""
        if len(content) > (self.head_chars + self.tail_chars + len(_TRUNC_MARKER)):
            collapsed = (
                content[: self.head_chars]
                + _TRUNC_MARKER
                + (content[-self.tail_chars :] if self.tail_chars else "")
            )
            return dataclasses.replace(event, content=collapsed)
        return event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Run-loop may already have appended a passive "continue" user message
        # for finish_reason=length. Replacing it avoids the before_model
        # contract warning (must not insert when last role is already user).
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
