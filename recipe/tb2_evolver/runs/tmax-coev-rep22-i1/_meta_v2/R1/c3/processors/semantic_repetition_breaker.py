# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""SemanticRepetitionBreaker for Tmax / TB2-style single-Bash-tool agents.

Closes a systemic *non-converging diagnostic loop* failure mode that the
existing guards miss:

* ``LengthTruncationRecoveryProcessor`` only fires on ``finish_reason=length``
  (runaway generation with no tool call).
* ``LoopDetectionProcessor`` (exact strategy) needs *consecutive, byte-identical*
  tool inputs; its name-only strategy is warn-only and never terminates.

The loop this processor targets looks different: the model keeps re-narrating
the *same conclusion in the same words* ("the monitor script is not showing up
in the process list", "I've been stuck in a loop, let me try a different
approach") while issuing slightly-varied Bash calls (`ps aux | grep X`,
`ps aux | grep Y`, re-launching the same background job). Tool args differ just
enough to defeat exact fingerprinting, the diagnostics are interleaved, and the
agent burns its entire step/budget allowance without ever committing a working
solution or trying a genuinely different tack.

Signal used here: **near-duplicate assistant narration across recent turns.**
Legitimate forward progress produces varied prose; a stuck loop repeats the
same normalized sentences. We measure token-overlap similarity between the
current assistant message and each of the last few, count how many recent turns
are near-duplicates, and:

* at ``warn_threshold`` near-duplicates → inject ONE strong corrective nudge on
  the next model call (redirect: stop re-diagnosing; either commit the current
  artifact or take a *materially different* concrete action).
* at ``raise_threshold`` near-duplicates → raise ``LoopDetectedError`` so the run
  loop exits cleanly (``exit_reason=loop_detected``) and recovers the last good
  output, instead of grinding to ``budget_exceeded``/``max_steps``.

Terminating a doomed loop early is a net win: the graded state is the sandbox's
final filesystem, so stopping sooner preserves any already-written solution and
frees budget that was being wasted. The thresholds are deliberately conservative
(observed loops repeat 5-14 times); short varied trajectories never trip it.
"""

from __future__ import annotations

import re
from collections import deque

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runloop import LoopDetectedError


_HEX_RE = re.compile(r"\b[0-9a-f]{6,}\b")
_NUM_RE = re.compile(r"\d+")
_NONWORD_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")


_NUDGE = (
    "\n\n[RepetitionBreaker] ⚠️ You have repeated essentially the same reasoning "
    "for several turns without making progress. STOP re-diagnosing and re-checking "
    "the same state. Do NOT restate your previous observation. Instead, in your "
    "next turn do exactly ONE of the following:\n"
    "  (a) If a required output/artifact already exists at the path named in the "
    "task, verify it once and then FINISH — the task grader inspects the final "
    "filesystem state, not your live processes.\n"
    "  (b) If it does not yet exist or is wrong, take a MATERIALLY DIFFERENT "
    "concrete action from what you just tried (e.g. rewrite the file, change the "
    "algorithm/logic, or run the actual end-to-end test the task describes) — not "
    "another status check.\n"
    "Note: on this benchmark a background process started with `&`/`nohup` persists "
    "for the verifier; you do NOT need to keep observing it live. Write at most two "
    "sentences, then issue one command."
)


class SemanticRepetitionBreaker(MultiHookProcessor):
    """Detect near-duplicate assistant narration and break the loop.

    Args:
        window_size:      How many recent assistant turns to compare against
                          (default 8).
        similarity:       Jaccard token-overlap threshold for two turns to count
                          as "near-duplicate" (default 0.8).
        warn_threshold:   Number of near-duplicate turns (within the window,
                          including the current one) that triggers the corrective
                          nudge on the next model call (default 3).
        raise_threshold:  Number of near-duplicate turns that raises
                          ``LoopDetectedError`` (default 4).
        min_chars:        Ignore assistant turns whose normalized content is
                          shorter than this — too short to be a meaningful
                          narration signature (default 24).
        compaction_drop_threshold:
                          Message-count drop that signals compaction and resets
                          the window (default 5).
    """

    _singleton_group = "semantic_repetition_breaker"
    # Run late in the after-model phase so length-recovery/parse-retry have had
    # their say first; run early in before-model so the nudge is present.
    _order = 60

    def __init__(
        self,
        window_size: int = 8,
        similarity: float = 0.8,
        warn_threshold: int = 3,
        raise_threshold: int = 4,
        min_chars: int = 24,
        compaction_drop_threshold: int = 5,
    ):
        self.window_size = int(window_size)
        self.similarity = float(similarity)
        self.warn_threshold = int(warn_threshold)
        self.raise_threshold = int(raise_threshold)
        self.min_chars = int(min_chars)
        self.compaction_drop_threshold = int(compaction_drop_threshold)

        self._sigs: deque[frozenset] = deque(maxlen=self.window_size)
        self._current_run_id: str = ""
        self._prev_message_count: int = 0
        self._pending_nudge: bool = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize(self, text: str) -> str:
        t = (text or "").lower()
        t = _HEX_RE.sub(" ", t)
        t = _NUM_RE.sub(" ", t)
        t = _NONWORD_RE.sub(" ", t)
        t = _WS_RE.sub(" ", t).strip()
        return t

    def _signature(self, text: str) -> frozenset:
        norm = self._normalize(text)
        if len(norm) < self.min_chars:
            return frozenset()
        return frozenset(norm.split())

    @staticmethod
    def _jaccard(a: frozenset, b: frozenset) -> float:
        if not a or not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0

    def _reset(self) -> None:
        self._sigs.clear()
        self._prev_message_count = 0
        self._pending_nudge = False

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        self._current_run_id = event.run_id
        yield event

    async def on_step_start(self, event: StepStartEvent):
        if event.run_id != self._current_run_id:
            self._reset()
            self._current_run_id = event.run_id
        current = len(event.messages)
        if (
            self._prev_message_count > 0
            and self._prev_message_count - current >= self.compaction_drop_threshold
        ):
            # Compaction happened — stale narration signatures no longer apply.
            self._sigs.clear()
        self._prev_message_count = current
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        """Inject the corrective nudge (net +1 message) if one is pending."""
        if not self._pending_nudge:
            yield event
            return
        self._pending_nudge = False
        nudged = event.messages + (Message(role="user", content=_NUDGE.strip()),)
        import dataclasses

        yield dataclasses.replace(event, messages=nudged)

    async def on_after_model(self, event: ModelResponseEvent):
        """Measure near-duplicate narration and warn / raise accordingly."""
        sig = self._signature(event.content)
        if not sig:
            # No usable narration this turn (e.g. pure tool call, empty content).
            # Do not disturb the window; a bare tool call is legitimate progress.
            yield event
            return

        # Count how many recent turns are near-duplicates of this one.
        dup_count = 1  # include the current turn
        for past in self._sigs:
            if self._jaccard(sig, past) >= self.similarity:
                dup_count += 1

        self._sigs.append(sig)

        if dup_count >= self.raise_threshold:
            self._reset()
            raise LoopDetectedError(
                "Semantic repetition loop: assistant narration near-identical "
                f"across {dup_count} recent turns without progress"
            )

        if dup_count >= self.warn_threshold:
            self._pending_nudge = True

        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
