# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""FuzzyLoopTerminatorProcessor — force a clean stop on NEAR-identical turn loops.

Motivation (R5, grounded in the R4 trajectories)
-------------------------------------------------
Every Tmax evolve round R0-R4 is flat at pass_rate 0.20. Across those rounds the
single dominant *harness-level* waste is a degenerate assistant-turn loop: on a
hard subgoal the model regenerates essentially the same "I've been stuck in a
loop, let me try a fundamentally different approach ..." turn over and over,
burning 30-60 steps and 600-1600s of wall-clock, and in the worst case the
accumulating truncated/length-recovery state crashes the whole run
(``exit_reason=error`` / ``agent_error``).

The R1 ``BashLoopBreakerProcessor`` fingerprints only the Bash tool INPUT, so it
is blind to no-tool-call / narrative loops. The R4 ``DegenerateTurnTerminator``
fixed part of that by fingerprinting the whole assistant turn — but it requires
the turns to be **byte-identical**, and measurement on the R4 trajectories shows
the real loops DRIFT: each regenerated turn differs by a few words (a different
truncation point under the 4096-token cap, a reordered clause), so the
byte-identical consecutive run tops out at 4 and never reaches R4's
``terminate_threshold=6``. The R4 terminator's banner fired **zero** times on the
two ``exit_reason=error`` crash tasks and on the budget-exceeded loopers.

Measured on the R4 run (max consecutive run of assistant turns whose normalized
token-set similarity >= 0.85):

    FAILING loopers : 4, 5, 6, 10, 17  (task_000032, 001044, 001465, 001201, 001028)
    PASSING set      : max = 2 (nine of ten passers = 1)

So a *similarity-based* run counter separates the loopers from the passing set
even more sharply than the exact-match one did, with a wide safety margin: a
terminate threshold of 5 (warn at 3) sits far above the passing-set ceiling of 2
yet catches every drifting loop the R4 exact matcher missed.

What this processor does
------------------------
Fires ``on_before_model``. It keeps a short window of recent assistant-turn
fingerprints (a normalized token *set* per turn) and counts the current run of
consecutive turns that are near-duplicates of the run's anchor (similarity
>= ``similarity_threshold`` via Jaccard on the token set — cheap, order- and
truncation-insensitive, no external deps).

* At ``warn_threshold`` near-identical turns it appends exactly one ``user``
  nudge (contract-safe: the last context message at that point is the repeated
  assistant turn, so +1 user is valid).
* At ``terminate_threshold`` it sets ``skip_model=True`` + ``synthetic_output``;
  the run loop converts that into a ``finish_reason="stop"`` response with no
  tool calls — its own clean-exit condition — so the task ends
  ``exit_reason=done`` instead of crashing to ``error`` or burning the whole
  budget. Partial deliverables on disk are preserved and scored, and exit-intent
  processors (e.g. the verifier-dep guard) still run.

This SUBSUMES the R4 exact-match terminator (byte-identical turns have
similarity 1.0) while additionally catching the drifting loops it missed, so R5
replaces the exact matcher with this one rather than stacking both.

Strategy-only / benchmark-agnostic: no task ids, no command literals, no domain
constants. Fires purely on the structural "near-identical assistant turn N times
in a row" shape.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    _extract_text,
)
from harnessx.core.processor import MultiHookProcessor

_WARN_TEMPLATE = (
    "[LoopTerminator] Your last {count} responses have been essentially the same "
    "with no change in outcome. Repeating this line of attack will not help. Take "
    "a genuinely different action this turn — inspect a different file, change the "
    "command, try another algorithm or input, or, if your required deliverables "
    "already exist at the paths the task names, verify them with `ls -lh` and "
    "finish. If you keep repeating near-identical responses the session will be "
    "ended automatically so your completed work is preserved."
)

_TERMINATE_OUTPUT = (
    "[LoopTerminator] The session was ended automatically after {count} "
    "near-identical responses with no progress. Whatever work was completed "
    "before the loop has been preserved on disk and will be evaluated. Ending "
    "now to avoid wasting the remaining budget on a dead loop."
)

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


class FuzzyLoopTerminatorProcessor(MultiHookProcessor):
    """Detect near-identical repeated assistant turns and force a clean stop.

    Args:
        similarity_threshold: Jaccard similarity on the normalized token set of
            two assistant turns at/above which they count as "the same" turn
            (default 0.85). Byte-identical turns score 1.0, so this subsumes
            exact matching.
        warn_threshold: consecutive near-identical turns at which a single user
            nudge is injected (default 3). Must be >= 2.
        terminate_threshold: consecutive near-identical turns at which the
            session is force-terminated via skip_model + synthetic_output
            (default 5). Must be > warn_threshold. Kept well above the observed
            passing-set ceiling (max run = 2) for a wide safety margin.
        min_tokens: turns with fewer than this many tokens are ignored for
            similarity (too short to compare meaningfully; resets the run).
    """

    _singleton_group = "fuzzy_loop_terminator"
    # After compaction / token-budget so the fingerprint reflects the context the
    # model will actually see this step; before the model call.
    _order = 23

    def __init__(
        self,
        similarity_threshold: float = 0.85,
        warn_threshold: int = 3,
        terminate_threshold: int = 5,
        min_tokens: int = 5,
    ) -> None:
        self.similarity_threshold = float(similarity_threshold)
        self.warn_threshold = max(2, int(warn_threshold))
        self.terminate_threshold = max(self.warn_threshold + 1, int(terminate_threshold))
        self.min_tokens = max(1, int(min_tokens))
        self._anchor: frozenset[str] | None = None
        self._run: int = 0
        self._terminated: bool = False

    # ------------------------------------------------------------------
    def _reset(self) -> None:
        self._anchor = None
        self._run = 0
        self._terminated = False

    @staticmethod
    def _token_set(text: str) -> frozenset[str]:
        return frozenset(_WORD_RE.findall((text or "").lower()))

    @staticmethod
    def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0

    @staticmethod
    def _last_assistant_text(messages: tuple) -> str:
        for m in reversed(messages):
            role = getattr(m, "role", None)
            if role == "assistant":
                return _extract_text(getattr(m, "content", "")) or ""
            # Skip trailing tool/user messages injected after the assistant turn.
        return ""

    # ------------------------------------------------------------------
    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if self._terminated:
            yield event
            return

        text = self._last_assistant_text(event.messages)
        toks = self._token_set(text)

        if len(toks) < self.min_tokens:
            # No usable assistant turn yet, or too short to compare — reset run.
            self._anchor = None
            self._run = 0
            yield event
            return

        if self._anchor is not None and self._jaccard(self._anchor, toks) >= self.similarity_threshold:
            self._run += 1
        else:
            self._anchor = toks
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

        # Warn: append exactly one user message (contract-safe; the last context
        # message here is the repeated assistant turn, so +1 user is valid).
        if self._run >= self.warn_threshold:
            warn = _WARN_TEMPLATE.format(count=self._run)
            yield dataclasses.replace(
                event,
                messages=event.messages + (Message(role="user", content=warn),),
            )
            return

        yield event
