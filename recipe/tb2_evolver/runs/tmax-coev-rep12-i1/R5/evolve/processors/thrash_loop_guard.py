# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""SelfDeclaredThrashGuard for Tmax / TB2-style agents.

Closes a systemic failure mode distinct from the ``finish_reason=length``
degenerate-repetition loop already handled by
``LengthTruncationRecoveryProcessor``.

Failure shape (the *semantic* thrash loop):
    The model issues a normal assistant turn WITH a real Bash tool call
    (finish_reason is a normal stop, not ``length``), receives essentially
    the same result as before, and then *narrates its own stuckness*:
    "I've been stuck in a loop", "I keep repeating the same approach",
    "let me try a different approach" — over and over, without changing its
    strategy. These runs burn the entire step budget (500-1130s of
    wall-clock) and never reach exit-intent, so no downstream exit-time
    guard fires either.

Discriminating signal (verified on R4 trajectories):
    The self-stuck narration phrases appear 20-33x in the failing thrash
    cluster (task_000348, task_000470, task_000669, task_000891, ...) and
    0x in every passing task sampled. Unlike a raw "identical command"
    signature (which also appears in *passing* long tasks and got the R1
    guard reverted), the model's own repeated stuck-declaration is a shape
    that is essentially absent from passing runs — so intervening on it has
    near-zero regression risk on the passing set.

What this processor does (contract-safe, mirrors
``LengthTruncationRecoveryProcessor``):
    * On ``on_after_model`` it scans the just-produced assistant content for
      self-stuck narration phrases and counts *consecutive* thrash turns.
    * On ``on_before_model`` it injects a single escalating corrective
      user message:
        - First trip: force a structured diagnosis — stop re-running the
          same command; separate what is verified-working from what is
          failing; form ONE new, differently-shaped hypothesis and test it.
        - Repeated trips: instruct the agent to stop burning budget on the
          same dead end, save/keep the best partial solution in place, and
          cleanly finish the turn so the remaining budget is not wasted.
    * Never blocks a tool call, never mutates ``event.tool_calls`` or the
      system prompt, only appends / replaces a single trailing user message.

It is a general strategy nudge keyed on a general behavioural shape; it
carries no task-specific literals.
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

# Self-declared stuckness narration. Deliberately generic behavioural
# phrases the model emits when it recognises it is looping but keeps going.
_THRASH_PATTERNS = [
    r"stuck in a loop",
    r"repeating the same",
    r"i keep (?:doing|trying|repeating)",
    r"same (?:approach|command|thing) (?:again|over)",
    r"(?:let me )?try (?:a )?(?:completely )?different approach",
    r"going in circles",
    r"i(?:'ve| have) been (?:stuck|repeating)",
]
_THRASH_RE = re.compile("|".join(_THRASH_PATTERNS), re.IGNORECASE)

_NUDGE_DIAGNOSE = (
    "HALT THE LOOP. You have just told yourself you are stuck / repeating the "
    "same approach. Re-running the same command will keep producing the same "
    "result. Do NOT issue another variation of the command you just ran. "
    "Instead, in your next turn: (1) state in one line what is now VERIFIED to "
    "work and what is VERIFIED to fail (cite the concrete output you saw, not a "
    "guess); (2) form exactly ONE new hypothesis that is genuinely different in "
    "KIND from what you have already tried (e.g. inspect a different file, read "
    "the actual error/log, check the data format, or re-read the task's exact "
    "required output path and format) — not a cosmetic tweak of the last "
    "command; (3) run ONE command that tests that new hypothesis. One command "
    "only."
)

_NUDGE_EXIT = (
    "STOP. You have declared yourself stuck multiple turns in a row and are "
    "burning the step budget on a dead end without changing strategy. This is "
    "wasted budget. Take stock now: if any required output file or service is "
    "already in a partially-correct state, LEAVE IT IN PLACE (do not delete or "
    "revert it) and make sure it is written to the exact path/format the task "
    "asked for. Then do one of two things and then FINISH your turn: either (a) "
    "run a single command that commits your best partial solution to the "
    "required output location, or (b) if the current state is already your best "
    "effort, end the turn cleanly. Do not keep re-running the same failing "
    "probe."
)


class SelfDeclaredThrashGuard(MultiHookProcessor):
    """Break the self-declared semantic thrash loop and redirect the model."""

    _singleton_group = "tmax_self_declared_thrash_guard"
    # After length recovery (order 5) but still an early control hook.
    _order = 6

    def __init__(
        self,
        diagnose_threshold: int = 2,
        exit_threshold: int = 4,
    ) -> None:
        # Number of consecutive self-declared-thrash turns before the first
        # (diagnosis) nudge fires; a slightly higher one triggers the
        # stop-and-commit nudge. Keep >=2 so a single incidental "let me try
        # a different approach" never trips the guard.
        self.diagnose_threshold = max(1, int(diagnose_threshold))
        self.exit_threshold = max(self.diagnose_threshold + 1, int(exit_threshold))
        self._consecutive: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._consecutive = 0
        self._pending_nudge = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        content = event.content or ""
        is_thrash = bool(_THRASH_RE.search(content))

        if not is_thrash:
            # Any turn without the self-stuck narration resets the streak.
            self._consecutive = 0
            self._pending_nudge = ""
            yield event
            return

        self._consecutive += 1
        if self._consecutive >= self.exit_threshold:
            self._pending_nudge = _NUDGE_EXIT
        elif self._consecutive >= self.diagnose_threshold:
            self._pending_nudge = _NUDGE_DIAGNOSE
        else:
            self._pending_nudge = ""
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Contract-safe: if the run loop already appended a trailing user
        # message, replace its content rather than inserting a second one.
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
