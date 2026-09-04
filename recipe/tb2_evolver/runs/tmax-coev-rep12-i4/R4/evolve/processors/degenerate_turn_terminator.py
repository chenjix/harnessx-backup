# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""DegenerateTurnTerminatorProcessor — force a clean stop on identical-turn loops.

Systemic failure mode observed across every Tmax evolve round (R0-R3): on hard
tasks the model falls into a *degenerate assistant-turn loop* — it emits the
byte-for-byte identical assistant message over and over, sometimes carrying a
useless repeated ``Bash`` call, sometimes carrying **no tool call at all**
(e.g. repeating a self-verify acknowledgement), until it exhausts the step
budget (``exit_reason=budget_exceeded``) or the repetition trips an internal
safety and the whole run crashes (``exit_reason=error`` / ``agent_error``).

In the R3 trajectories, 16 distinct FAILING tasks emitted the same assistant
message 4-41 times consecutively; **no PASSING task ever emitted the same
assistant message more than twice**. Four of the 16 ended in
``exit_reason=error``. The existing ``BashLoopBreakerProcessor`` only
fingerprints the ``Bash`` tool *input*, so it is blind to the no-tool-call /
self-verify half of this pattern, and its soft "BLOCKED" redirect is routinely
ignored — transcripts show the model re-emitting the identical turn 12-38 more
times after being blocked.

This processor closes both gaps with a single, input-independent guard that
acts at the model-call boundary:

* **Fingerprint the whole assistant turn, not just a tool input.** It hashes the
  text of the last assistant message in the assembled context. This catches
  Bash loops, no-tool-call loops, and self-verify loops alike — anything where
  the model regenerates the identical turn.
* **Warn, then hard-terminate.** At ``warn_threshold`` consecutive identical
  assistant turns a nudge is appended (one extra user message, contract-safe).
  At ``terminate_threshold`` the processor sets ``skip_model=True`` with a final
  ``synthetic_output``; the run loop turns that into a ``finish_reason="stop"``
  ModelResponseEvent with no tool calls, which is the run loop's own clean-exit
  condition. The task ends ``exit_reason=done`` instead of crashing to
  ``error`` or burning the whole budget — banking whatever partial deliverables
  exist and letting exit-intent processors (e.g. the verifier-dep guard) run.

Design notes (grounded in the R3 evidence):

* **Conservative thresholds.** Every observed passing task had a max identical-
  assistant run of <= 2; ``warn_threshold=4`` / ``terminate_threshold=6`` leaves
  the passing set completely untouched (regression surface is nil) while
  catching all 16 failing loopers (runs of 4-41).
* **Only fires after the loop is provably dead.** Termination is a last resort —
  by the 6th identical turn the model has demonstrably failed to self-correct
  through both the R2 prompt and the R1/soft-block redirect.
* **Contract-safe.** The warn path appends at most one ``user`` message; the
  terminate path does not mutate ``event.messages`` at all (it only sets
  ``skip_model`` / ``synthetic_output``).

Strategy-only / benchmark-agnostic: no task ids, no command literals, no domain
constants. Fires purely on the structural "identical assistant turn N times in a
row" shape.
"""

from __future__ import annotations

import dataclasses
import hashlib

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    _extract_text,
)
from harnessx.core.processor import MultiHookProcessor

_WARN_TEMPLATE = (
    "[LoopTerminator] You have now produced the SAME response {count} times in a "
    "row with no change in outcome. Repeating it will not help. Take a genuinely "
    "different action this turn — inspect a different file, change the command, "
    "try another approach, or, if your required deliverables already exist at the "
    "paths the task names, verify them with `ls -lh` and finish. If you keep "
    "repeating the identical response the session will be ended automatically."
)

_TERMINATE_OUTPUT = (
    "[LoopTerminator] The session was ended automatically after the identical "
    "response was produced {count} times consecutively with no progress. "
    "Whatever work was completed before the loop has been preserved on disk and "
    "will be evaluated. Ending now to avoid wasting the remaining budget on a "
    "dead loop."
)


class DegenerateTurnTerminatorProcessor(MultiHookProcessor):
    """Detect byte-identical repeated assistant turns and force a clean stop.

    Args:
        warn_threshold: consecutive identical assistant turns at which a nudge is
            injected (default 4). Must be >= 2.
        terminate_threshold: consecutive identical assistant turns at which the
            session is force-terminated via skip_model + synthetic_output
            (default 6). Must be > warn_threshold.
    """

    _singleton_group = "degenerate_turn_terminator"
    # After compaction (8)/token-budget(10) so the fingerprint reflects the
    # context the model will actually see this step; before the model call.
    _order = 22

    def __init__(
        self,
        warn_threshold: int = 4,
        terminate_threshold: int = 6,
    ) -> None:
        self.warn_threshold = max(2, int(warn_threshold))
        self.terminate_threshold = max(self.warn_threshold + 1, int(terminate_threshold))
        self._last_fp: str = ""
        self._run: int = 0
        self._terminated: bool = False

    # ------------------------------------------------------------------
    def _reset(self) -> None:
        self._last_fp = ""
        self._run = 0
        self._terminated = False

    @staticmethod
    def _fingerprint(text: str) -> str:
        norm = " ".join((text or "").split())
        if not norm:
            return ""
        return hashlib.sha256(norm.encode("utf-8", "replace")).hexdigest()[:16]

    @staticmethod
    def _last_assistant_text(messages: tuple) -> str:
        for m in reversed(messages):
            role = getattr(m, "role", None)
            if role == "assistant":
                return _extract_text(getattr(m, "content", "")) or ""
            # Skip trailing tool/user messages injected after the assistant turn
        return ""

    # ------------------------------------------------------------------
    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        # Once we've decided to terminate, stay terminated (idempotent).
        if self._terminated:
            yield event
            return

        text = self._last_assistant_text(event.messages)
        fp = self._fingerprint(text)

        if not fp:
            # No assistant turn yet, or empty turn — reset the run and pass through.
            self._last_fp = ""
            self._run = 0
            yield event
            return

        if fp == self._last_fp:
            self._run += 1
        else:
            self._last_fp = fp
            self._run = 1

        # Hard terminate: force a clean stop. The run loop converts skip_model
        # into a finish_reason="stop" response with no tool calls -> clean break
        # -> exit_reason="done".
        if self._run >= self.terminate_threshold:
            self._terminated = True
            yield dataclasses.replace(
                event,
                skip_model=True,
                synthetic_output=_TERMINATE_OUTPUT.format(count=self._run),
            )
            return

        # Warn: append exactly one user message (contract-safe; last context
        # message here is the repeated assistant turn, so +1 user is valid).
        if self._run >= self.warn_threshold:
            warn = _WARN_TEMPLATE.format(count=self._run)
            yield dataclasses.replace(
                event,
                messages=event.messages + (Message(role="user", content=warn),),
            )
            return

        yield event
