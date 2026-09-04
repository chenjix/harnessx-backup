# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""NarrationLoopBreakProcessor — terminate the near-identical-reasoning spiral.

Motivation (R4 trajectory evidence)
-----------------------------------
A recurring, harness-visible failure mode in this benchmark is the
**budget-exhausting reasoning spiral**: the model gets stuck on a bug it
cannot solve, and emits the *same* self-narration turn after turn — literally
re-typing "I've been stuck in a loop … let me try a fundamentally different
approach" while the underlying command keeps failing the same way. The stock
``LoopDetectionProcessor`` does not stop these:

  * Its **exact** fingerprint (name + inputs) never matches, because the model
    tweaks the Bash argument slightly each turn.
  * Its **name-only** fingerprint (tool name, args ignored) is *warn-only by
    design* — it never raises, because Bash-with-different-args is a noisy
    signal on this benchmark (passing tasks legitimately issue 37-48
    consecutive Bash calls, so a name-only raise would false-positive and
    regress real long tasks).

The result: these runs are warned 12-22 times, narrate right past every
warning, and burn the entire step budget (80 steps) and 900-1100 s of
wall-clock before exiting ``budget_exceeded`` — or, worse, ``error`` when the
repeated command finally trips an unhandled exception. They are capability
gaps (the model cannot fix the bug), so no harness nudge flips them; the only
harness-appropriate action is to **stop the doomed run early** so its
step/time/token budget is not wasted.

Discriminating signal (validated against the R4 round)
------------------------------------------------------
What separates these spirals from legitimate long tasks is NOT tool-call
count — it is **repeated near-identical assistant reasoning content**. In R4:

  * failing spirals repeated one normalized narration 6-8 times
    (task_001031 = 8, task_000396 = 6);
  * the *most* any PASSING task repeated an identical narration was 4
    (task_000740, which passed), and typical passing tasks were <= 3.

So a hard-stop keyed on ``>= 6`` consecutive near-identical assistant turns
sits two full repeats above every passing task in the round — zero regression
surface — while catching the doomed spirals that the name-only loop detector
can only warn about.

Design
------
``on_after_model`` maintains a running count of how many *consecutive*
assistant turns had near-identical normalized content. Interleaving a
materially different turn (real progress) resets the streak, so exploration
and retry-with-variation are never penalised. At ``warn_threshold`` a single
concrete warning is armed and delivered on the next ``on_before_model`` (so the
model gets one explicit chance to break the pattern itself). At
``raise_threshold`` the processor raises ``LoopDetectedError``, which the run
loop turns into a clean ``exit_reason=loop_detected`` — a graceful early exit,
never ``error``.

Normalization keys on the *shape* of the reasoning, not any task content: it
lowercases, collapses whitespace, strips digits/punctuation, and keeps a
bounded prefix. There are no task ids, paths, ports, or any literal from any
training task — the mechanism is fully benchmark-agnostic.
"""

from __future__ import annotations

import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runloop import LoopDetectedError


_WS_RE = re.compile(r"\s+")
_NONWORD_RE = re.compile(r"[^a-z ]+")


_WARN_MSG = (
    "\n\n[NarrationLoop] ⚠️  Your last several turns have repeated essentially "
    "the same reasoning ('stuck in a loop', 'try a different approach', etc.) "
    "without any real change in the result. Re-typing the same plan is not "
    "progress. This turn, do ONE of two things and nothing else: (a) if the "
    "required output already exists and is correct, stop and finish; or (b) run "
    "a single concrete Bash command that is *materially different* from what you "
    "have been trying — inspect a different file, test a smaller hypothesis, or "
    "change the actual approach. If you repeat the same narration again the run "
    "will be stopped to avoid wasting the budget."
)


class NarrationLoopBreakProcessor(MultiHookProcessor):
    """Warn-then-raise on consecutive near-identical assistant reasoning turns.

    Parameters
    ----------
    warn_threshold:
        Consecutive near-identical assistant turns at which a single concrete
        warning is injected (default 4). Gives the model one explicit chance to
        break the pattern before termination.
    raise_threshold:
        Consecutive near-identical assistant turns at which the processor raises
        ``LoopDetectedError`` (clean ``loop_detected`` exit). Must be strictly
        greater than ``warn_threshold``. Default 6 — validated to sit two full
        repeats above the most any passing task in the calibration round
        repeated an identical narration, so it never fires on a healthy run.
    prefix_chars:
        How many characters of the normalized content to compare (default 220).
        Bounded so a long reasoning turn is keyed on its opening shape, which is
        where the repeated boilerplate lives.
    min_chars:
        Minimum raw content length for a turn to count toward the streak
        (default 60). Short acknowledgements never trip the detector.
    """

    _singleton_group = "tb2_narration_loop_break"
    _order = 22  # just after stock loop_detection (_order=20)

    def __init__(
        self,
        warn_threshold: int = 4,
        raise_threshold: int = 6,
        prefix_chars: int = 220,
        min_chars: int = 60,
    ) -> None:
        self.warn_threshold = max(1, int(warn_threshold))
        self.raise_threshold = max(self.warn_threshold + 1, int(raise_threshold))
        self.prefix_chars = max(40, int(prefix_chars))
        self.min_chars = max(1, int(min_chars))
        self._prev_key: str | None = None
        self._streak: int = 1
        self._warned_at: int = 0
        self._pending_warn: bool = False

    # ------------------------------------------------------------------
    def _normalize(self, content: str) -> str:
        s = content.lower()
        s = _NONWORD_RE.sub(" ", s)  # drop digits/punct so tiny arg tweaks don't matter
        s = _WS_RE.sub(" ", s).strip()
        return s[: self.prefix_chars]

    def _reset(self) -> None:
        self._prev_key = None
        self._streak = 1
        self._warned_at = 0
        self._pending_warn = False

    # ------------------------------------------------------------------
    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        content = event.content or ""
        # Only pure-reasoning turns count. A turn that actually issues a tool
        # call is (potential) forward progress and resets the streak — we never
        # penalise a model that is still acting.
        if event.tool_calls or len(content.strip()) < self.min_chars:
            self._prev_key = None
            self._streak = 1
            self._warned_at = 0
            yield event
            return

        key = self._normalize(content)
        if key and key == self._prev_key:
            self._streak += 1
        else:
            self._prev_key = key
            self._streak = 1
            self._warned_at = 0

        if self._streak >= self.raise_threshold:
            raise LoopDetectedError(
                "Narration loop detected: the model repeated near-identical "
                f"reasoning {self._streak} times consecutively without progress"
            )

        if self._streak >= self.warn_threshold and self._warned_at < self._streak:
            self._warned_at = self._streak
            self._pending_warn = True

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_warn:
            yield event
            return
        self._pending_warn = False
        import dataclasses

        messages = event.messages
        if messages and messages[-1].role == "user":
            # Contract-safe in-place rewrite of the last user message (len_delta 0).
            last = messages[-1]
            new_last = dataclasses.replace(
                last, content=(last.content or "") + _WARN_MSG
            )
            yield dataclasses.replace(event, messages=messages[:-1] + (new_last,))
        else:
            # Append exactly one user message (+1, contract legal).
            yield dataclasses.replace(
                event,
                messages=messages + (Message(role="user", content=_WARN_MSG.strip()),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
