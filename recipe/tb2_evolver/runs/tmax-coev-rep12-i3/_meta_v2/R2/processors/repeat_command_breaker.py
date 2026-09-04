# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatCommandBreakerProcessor — break identical-Bash-command loops.

Closes a systemic ``budget_exceeded`` failure mode observed across many tasks:
the agent issues the **exact same Bash command** turn after turn, gets the same
(often empty or failing) tool result each time, and keeps re-issuing it — burning
the entire step budget on a single unproductive command.

This is NOT the ``finish_reason=length`` degenerate-narration loop that
``LengthTruncationRecoveryProcessor`` already handles: here the model *does*
emit a well-formed tool call every turn, so that processor never fires. The
loop slips through and only ``budget_exceeded`` stops it, after ~25 wasted steps.

Mechanism (non-destructive):
* Track the normalized command string of the single Bash tool call each turn.
* When the same command is issued ``repeat_threshold`` times in a row, inject a
  firm corrective *user* message before the next model call telling the agent
  the command produced an identical result N times and it must change strategy
  (inspect state differently, fix the actual error, or move to the next step).
* The offending tool call is still allowed to execute — we never block or kill
  the run, we only add a redirect. So a legitimately-slow retry (e.g. a package
  install re-run a few times) is at worst nudged, never broken.
* Escalates wording if the loop persists past the first nudge.

Generality: keys off the *shape* "same command repeated consecutively", with no
task-specific commands, paths, or ids. It closes a class of loops, not one task,
and is a no-op on any run whose consecutive-command count never reaches the
threshold (the overwhelmingly common case for passing tasks).
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


_NUDGE_FIRST = (
    "STOP — loop detected by the harness. You have just issued the SAME command "
    "{n} times in a row and it produced the same result every time. Repeating it "
    "again will not change the outcome. Do NOT run that command again. Instead, in "
    "your next turn do ONE of the following: (a) if the command failed or returned "
    "nothing useful, diagnose *why* with a different command (check the exact path "
    "exists, inspect the real file/tool, read the actual error), or (b) if you have "
    "the information you need, move on to the NEXT concrete step of the task. "
    "Change your approach now."
)

_NUDGE_REPEAT = (
    "STOP. You are STILL repeating a command that does not work. This has now "
    "wasted many steps. Completely abandon this line of attack — the current "
    "command will never succeed as written. Re-read the task requirements, pick a "
    "DIFFERENT strategy, and either fix the underlying problem (wrong path, missing "
    "dependency, wrong tool/flags) or proceed to a different required step. Issue a "
    "genuinely different command in your next turn."
)


def _bash_command(event: ModelResponseEvent) -> str | None:
    """Return the normalized command of a single-Bash-call turn, else None."""
    calls = event.tool_calls or ()
    if len(calls) != 1:
        return None
    call = calls[0]
    if getattr(call, "name", None) != "Bash":
        return None
    inp = getattr(call, "input", None) or {}
    cmd = inp.get("command") if isinstance(inp, dict) else None
    if not isinstance(cmd, str):
        return None
    norm = cmd.strip()
    return norm or None


class RepeatCommandBreakerProcessor(MultiHookProcessor):
    """Detect consecutive identical Bash commands and redirect the agent."""

    _singleton_group = "tmax_repeat_command_breaker"
    _order = 6  # right after LengthTruncationRecoveryProcessor (_order=5)

    def __init__(self, repeat_threshold: int = 3) -> None:
        # Fire when the same command has been issued this many times in a row.
        self.repeat_threshold = max(2, int(repeat_threshold))
        self._last_cmd: str | None = None
        self._run_len: int = 0
        self._pending_nudge: str = ""
        self._nudges_sent: int = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._last_cmd = None
        self._run_len = 0
        self._pending_nudge = ""
        self._nudges_sent = 0
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        cmd = _bash_command(event)
        if cmd is None:
            # Any non-single-Bash turn breaks the run (real progress / narration).
            self._last_cmd = None
            self._run_len = 0
            yield event
            return

        if cmd == self._last_cmd:
            self._run_len += 1
        else:
            self._last_cmd = cmd
            self._run_len = 1

        if self._run_len >= self.repeat_threshold:
            self._nudges_sent += 1
            self._pending_nudge = (
                _NUDGE_REPEAT
                if self._nudges_sent >= 2
                else _NUDGE_FIRST.format(n=self._run_len)
            )
            # Re-arm: require another full run of repeats before nudging again,
            # so we escalate rather than nudge on every subsequent identical turn.
            self._run_len = 0
            self._last_cmd = None

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # If the run loop already appended a trailing user message, replace it so
        # we don't create two consecutive user messages (contract-safe).
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._last_cmd = None
        self._run_len = 0
        self._pending_nudge = ""
        self._nudges_sent = 0
        yield event
