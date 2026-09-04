# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""StuckTruncationEscalator — break the *verbatim length-truncation* loop.

Distinct from ``LengthTruncationRecoveryProcessor`` (which collapses one
runaway generation per truncation and appends a passive "issue one Bash
command" nudge) and from any tool-*result* repetition breaker (which keys on
identical tool OUTPUT). This processor keys on **identical assistant CONTENT
emitted across consecutive length truncations**: the pathology where the
recovery nudge fires, is ignored, and the model re-emits the byte-identical
narration and re-hits ``max_tokens`` — turn after turn — with zero progress.

Observed shape (system_administration daemon/operator tasks): the passive
continue-nudge cycle repeated 14x on one task; the model regurgitated the
same "I need to stop the repetition and just take action" paragraph ~10x,
never issuing the concrete end-to-end test that would have exposed the
deliverable's behavioural bug. ~50 of 79 steps burned inside the loop; the
run then exited ``done`` having never once executed the deliverable against
the real inputs the task names.

Mechanism: track a rolling fingerprint of the assistant content on each
length-truncated turn (no tool call). When the same fingerprint repeats
``hard_repeat`` times in a row, the passive nudge is clearly not landing, so
escalate ONCE to a strong terminal directive: abandon the narration entirely
and, in the next turn, issue a single Bash command that WRITES the required
output file and/or RUNS it end-to-end against the real inputs named in the
task, then observe the actual result. Content-agnostic: no task ids, paths,
constants, or domain literals. Append-only on the message list (mirrors the
existing recovery processor's before-model contract); never terminates,
never drops or rewrites prior messages.
"""

from __future__ import annotations

import dataclasses
import hashlib

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

_ESCALATE_DIRECTIVE = (
    "STOP — you have hit the output token limit with the SAME text several "
    "turns in a row. Re-explaining is not progress and is burning your step "
    "budget. Abandon this line of narration completely. Write NO prose "
    "analysis in your next turn. Issue exactly ONE short Bash command that "
    "makes real progress toward the deliverable, in this priority order: "
    "(1) if the required output file/script named in the task is not yet "
    "written or is incomplete, write it now with a single heredoc; "
    "(2) otherwise, EXECUTE the deliverable end-to-end against the REAL "
    "inputs the task points at (run the actual program / start the actual "
    "service / invoke the real simulator or dataset) and capture its real "
    "observable result — do not just re-read the source or re-check a "
    "process list. Then read what actually happened before your next step. "
    "One command only."
)


def _fingerprint(content: str) -> str:
    """Stable, whitespace-normalised fingerprint of assistant content."""
    normalised = " ".join((content or "").split())
    return hashlib.sha1(normalised.encode("utf-8", "ignore")).hexdigest()


class StuckTruncationEscalator(MultiHookProcessor):
    """Escalate to a hard finalize directive on repeated identical truncations."""

    _singleton_group = "tmax_stuck_truncation_escalator"
    # After LengthTruncationRecoveryProcessor (_order=5) so we observe the
    # same truncation events but escalate only when its passive nudge fails.
    _order = 7

    def __init__(self, hard_repeat: int = 3, min_content_chars: int = 40) -> None:
        self.hard_repeat = max(2, int(hard_repeat))
        self.min_content_chars = max(0, int(min_content_chars))
        self._last_fp: str | None = None
        self._run_len: int = 0
        self._pending: str = ""
        self._fired: bool = False

    async def on_task_start(self, event: TaskStartEvent):
        self._last_fp = None
        self._run_len = 0
        self._pending = ""
        self._fired = False
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        length_truncated = event.finish_reason == "length" and not event.tool_calls
        content = event.content or ""
        if not length_truncated or len(content.strip()) < self.min_content_chars:
            # A real tool call or a non-truncated turn = progress; reset the run.
            self._last_fp = None
            self._run_len = 0
            yield event
            return

        fp = _fingerprint(content)
        if fp == self._last_fp:
            self._run_len += 1
        else:
            self._last_fp = fp
            self._run_len = 1

        # Escalate once when the identical-content truncation run is long
        # enough that the passive recovery nudge has demonstrably not landed.
        if self._run_len >= self.hard_repeat and not self._fired:
            self._pending = _ESCALATE_DIRECTIVE
            self._fired = True
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending:
            yield event
            return
        nudge = self._pending
        self._pending = ""
        msgs = list(event.messages)
        # The run loop / length-recovery may already have appended a user-role
        # continue/redirect message. Replace it so we keep a +0 message-count
        # delta when the last role is already user (before-model contract),
        # else append exactly one user message.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._last_fp = None
        self._run_len = 0
        self._pending = ""
        self._fired = False
        yield event
