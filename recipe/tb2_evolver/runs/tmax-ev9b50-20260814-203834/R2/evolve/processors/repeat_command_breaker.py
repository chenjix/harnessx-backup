# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandBreakerProcessor.

Closes a systemic failure mode that survived the R1 length-recovery fix:
the small model ends each turn *normally* (a well-formed Bash tool call, not
a ``finish_reason=length`` truncation) but issues the **same command over and
over** without adapting. It never reacts to the tool result — it re-writes the
identical file, re-runs the identical port check, or re-launches the identical
process, burning the entire step / wall-clock budget and still failing.

Observed shapes (generic): re-writing an identical file heredoc many times in
a row with no intervening change; re-issuing an identical probe/status command
and cycling start/kill/check without adapting; re-launching an identical
long-running command with no code change between runs. In every shape the
successive commands are byte-identical modulo whitespace and the tool result is
never acted on.

The existing ``CustomEditToolProcessor`` (EditDetection) *does* fire a passive
text warning appended to the tool result, but the model ignores it and keeps
looping. The lever here is **Control with teeth**: detect consecutive
byte-identical (whitespace-normalized) Bash commands and, past a threshold,
(1) inject one escalating ``user`` redirect that names the loop explicitly, and
(2) at a higher threshold, *suppress the redundant execution itself* by
returning a synthetic tool result — the loop physically cannot continue, which
forces the model onto a different action.

The processor is benchmark-agnostic in mechanism: it keys purely on the
normalized text of successive ``Bash`` commands, never on task content.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


def _normalize(cmd: str) -> str:
    """Collapse whitespace so trivially-reformatted repeats still match."""
    return " ".join((cmd or "").split())


_NUDGE_SOFT = (
    "[RepeatGuard] You have now issued the SAME command {n} times in a row "
    "without changing it. Repeating an identical command will keep producing "
    "the same result. Do NOT run it again unchanged. Read the most recent tool "
    "output carefully, state in one sentence what is actually different from "
    "what you expected, then take a DIFFERENT concrete action — change the "
    "command, inspect a different file, or fix the underlying cause."
)

_NUDGE_HARD = (
    "[RepeatGuard] STOP. You are stuck in a loop: the identical command has now "
    "run {n} times with no progress. Whatever you are retrying is NOT the "
    "problem to keep poking — the approach itself is wrong. Abandon it. Pick a "
    "materially different strategy: inspect the error output line by line, "
    "check assumptions you have not verified (file paths, process state, exact "
    "error message), or solve the task a different way. Your next command MUST "
    "differ from the one you just repeated."
)

# Synthetic result returned when execution is suppressed.
_SUPPRESSED = (
    "[RepeatGuard] This command was NOT executed: it is byte-for-byte identical "
    "to the previous {n} command(s), which all produced the same result. "
    "Re-running it cannot make progress. Take a different action — the repeated "
    "command is not solving the problem. Review the last real output above and "
    "change your approach before running anything again."
)


class RepeatedCommandBreakerProcessor(MultiHookProcessor):
    """Detect and break consecutive-identical Bash command loops.

    Parameters
    ----------
    warn_threshold:
        Number of consecutive identical Bash commands at which a corrective
        ``user`` nudge is armed (soft nudge). Default 3.
    hard_threshold:
        Number of consecutive identical Bash commands at which the escalated
        nudge is used. Default 4.
    suppress_threshold:
        Number of consecutive identical Bash commands at (or above) which the
        execution itself is suppressed and replaced with a synthetic result.
        Must be >= warn_threshold to be meaningful. Default 5.
    """

    _singleton_group = "tb2_repeat_command_breaker"
    # Run after EditDetection (_order=30) so the two guards don't collide, and
    # before the self-verify keepalive (_order=90).
    _order = 40

    def __init__(
        self,
        warn_threshold: int = 3,
        hard_threshold: int = 4,
        suppress_threshold: int = 5,
    ) -> None:
        self.warn_threshold = max(2, int(warn_threshold))
        self.hard_threshold = max(self.warn_threshold, int(hard_threshold))
        self.suppress_threshold = max(self.hard_threshold + 1, int(suppress_threshold))
        self._last_norm: str = ""
        self._streak: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._last_norm = ""
        self._streak = 0
        self._pending_nudge = ""
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name != "Bash":
            yield event
            return

        cmd = _normalize(event.tool_input.get("command", ""))
        if not cmd:
            yield event
            return

        if cmd == self._last_norm:
            self._streak += 1
        else:
            self._last_norm = cmd
            self._streak = 1

        # Suppress the physical execution once the loop is entrenched. The
        # synthetic result stands in for the tool output so the run loop keeps
        # going, but the model no longer gets a fresh identical result to react
        # to — it must change course.
        if self._streak >= self.suppress_threshold:
            self._pending_nudge = _NUDGE_HARD.format(n=self._streak)
            yield dataclasses.replace(
                event,
                approved=False,
                synthetic_result=_SUPPRESSED.format(n=self._streak - 1),
            )
            return

        # Otherwise arm a redirect nudge (soft then hard) but let the command run
        # once more so the model still sees a real result alongside the warning.
        if self._streak >= self.hard_threshold:
            self._pending_nudge = _NUDGE_HARD.format(n=self._streak)
        elif self._streak >= self.warn_threshold:
            self._pending_nudge = _NUDGE_SOFT.format(n=self._streak)

        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        # A distinct command breaks the streak; that reset already happens in
        # on_before_tool for the next call. Nothing to do here except pass
        # through — kept for symmetry / future counters.
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        # Contract-safe: append exactly one user message.
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=nudge),),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._last_norm = ""
        self._streak = 0
        self._pending_nudge = ""
        yield event
