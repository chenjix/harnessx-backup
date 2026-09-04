# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandLoopBreaker — break identical-Bash-command repetition loops.

Closes a systemic ``budget_exceeded`` failure mode observed across multiple
tmax tasks: the model issues the **same non-trivial shell command verbatim**
turn after turn (e.g. a recursive-CTE query that references a table that will
never exist, an mpi4py snippet that always errors, an extractor invocation that
never captures output). Each repeat returns the same failure, the model
re-narrates "let me try a fundamentally different approach", then re-issues the
identical command. The step / wall-clock budget drains with zero progress and
the required output files are never written.

The existing ``LengthTruncationRecoveryProcessor`` only fires on
``finish_reason == "length"`` with no tool call, so it never sees these
tool-calling loops. The generic ``LoopDetectionProcessor`` *would* catch them,
but its exact-fingerprint strategy also counts degenerate **empty-argument**
tool calls (``{}``) as identical repeats — and at least one *passing* task
recovers after ~20 consecutive empty calls, so a naive low-threshold raise
would regress it.

This processor is deliberately narrow:

* It fingerprints **only substantive Bash commands** — non-empty, above a
  minimum length. Empty-argument calls, verification pings, and trivially
  short commands are treated as neutral interludes: they neither increment the
  loop counter nor reset it. This is what protects the "recovers after many
  empty calls" passing pattern.
* On the ``warn_threshold``-th consecutive identical substantive command it
  appends a single decisive redirect to that command's result, telling the
  model to stop re-issuing the exact command and either change the command
  materially or write the required output / finish.
* On the ``raise_threshold``-th consecutive identical substantive command it
  raises :class:`LoopDetectedError`, which the run loop converts into a clean
  ``exit_reason == "loop_detected"`` (not ``error``) and recovers the best
  available output — reclaiming the remaining step and wall-clock budget
  instead of burning it on a command that will never succeed.

Content-agnostic: names no task, path, query, or constant.
"""

from __future__ import annotations

import dataclasses
import hashlib

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runloop import LoopDetectedError


_WARN = (
    "\n\n[RepeatedCommandBreaker] STOP. You have now issued this EXACT command "
    "{count} times in a row and it keeps returning the same result. Re-issuing "
    "the identical command will not help. Before your next turn, reconsider the "
    "root cause of the repeated failure (e.g. a CTE / temporary name only exists "
    "inside the statement that defines it and cannot be referenced from a "
    "separate command; a syntax or environment problem the identical retry will "
    "never fix). Then do ONE of the following, and CHANGE the command "
    "materially: (a) run a small diagnostic to inspect the current state (list "
    "files, show table schema, read a few lines) so you can decide a genuinely "
    "different next step; (b) if the required output file(s) already exist and "
    "are correct, stop; (c) write the required output with a substantively "
    "different command. Do not repeat the command above verbatim."
)


class RepeatedCommandLoopBreaker(MultiHookProcessor):
    """Detect and break loops of an identical, non-trivial Bash command.

    Args:
        warn_threshold:  Consecutive-identical count that injects a redirect
                         into the tool result (default 3).
        raise_threshold: Consecutive-identical count that raises
                         :class:`LoopDetectedError` to end the task cleanly
                         (default 6).
        min_command_chars: Commands shorter than this are treated as trivial
                         interludes and never counted (default 12).
        tool_name:       Only commands issued through this tool are fingerprinted
                         (default ``"Bash"``).
    """

    _singleton_group = "tmax_repeated_command_breaker"
    _order = 21  # after the generic loop detector's slot; before compaction

    def __init__(
        self,
        warn_threshold: int = 3,
        raise_threshold: int = 6,
        min_command_chars: int = 12,
        tool_name: str = "Bash",
    ) -> None:
        self.warn_threshold = max(2, int(warn_threshold))
        self.raise_threshold = max(self.warn_threshold + 1, int(raise_threshold))
        self.min_command_chars = max(0, int(min_command_chars))
        self.tool_name = tool_name
        self._last_fp: str = ""
        self._run: int = 0
        # tool_call_id -> fingerprint (or "" for a neutral/skipped call)
        self._pending: dict[str, str] = {}

    # ------------------------------------------------------------------ helpers
    def _fingerprint(self, event: ToolCallEvent) -> str:
        """Return a fingerprint for a *substantive* command, else "" (neutral)."""
        if event.tool_name != self.tool_name:
            return ""
        cmd = ""
        ti = event.tool_input
        if isinstance(ti, dict):
            raw = ti.get("command", "")
            if isinstance(raw, str):
                cmd = raw
        cmd = cmd.strip()
        if len(cmd) < self.min_command_chars:
            return ""
        return hashlib.sha256(cmd.encode("utf-8", "replace")).hexdigest()[:16]

    def _reset(self) -> None:
        self._last_fp = ""
        self._run = 0
        self._pending.clear()

    # -------------------------------------------------------------------- hooks
    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        self._pending[event.tool_call_id] = self._fingerprint(event)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        fp = self._pending.pop(event.tool_call_id, "")

        # Neutral / skipped calls (empty args, trivial, non-Bash) are interludes:
        # do not increment, do not reset. This preserves the loop count across
        # verification pings while never firing on degenerate empty-call runs.
        if not fp:
            yield event
            return

        if fp == self._last_fp:
            self._run += 1
        else:
            self._last_fp = fp
            self._run = 1

        if self._run >= self.raise_threshold:
            raise LoopDetectedError(
                f"Identical '{self.tool_name}' command repeated {self._run} times "
                "consecutively without progress"
            )

        if self._run >= self.warn_threshold:
            warn = _WARN.format(count=self._run)
            yield dataclasses.replace(event, result=(event.result or "") + warn)
            return

        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
