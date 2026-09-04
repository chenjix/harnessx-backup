# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandGuard — break unproductive tool-call repetition loops.

Failure class (observable purely from the tool I/O stream):
    The agent issues a Bash command, gets a result, and then re-issues the
    *same* command that produces the *same* result — repeatedly — without the
    workspace state or the information available changing. This includes
    repeated identical errors (a failing tool call retried verbatim) and
    repeated identical successes (a write/inspect command run in a tight loop).
    Left unchecked, the agent burns its entire step budget on a no-progress
    loop and exits with ``budget_exceeded``.

This is distinct from the existing guards in the pipeline:
    * ``CustomEditToolProcessor`` only counts *writes to the same file* and is
      blind to non-write loops (e.g. repeated ``cat``/``head`` inspection, a
      failing compile/query retried verbatim).
    * ``LengthTruncationRecoveryProcessor`` only handles ``finish_reason=length``
      generation loops, not identical tool-call loops that finish cleanly.

Mechanism:
    Track the last-seen (normalized-command, result-signature) pair. Each time
    the *current* Bash call matches BOTH the previous command and the previous
    result signature, increment a consecutive-repeat counter. When the counter
    crosses ``warn_threshold`` we append a legible, escalating nudge to the tool
    result telling the agent that the approach is not producing new information
    and that it must change strategy (inspect inputs differently, read an error
    message, or move on). A distinct command/result resets the counter, so a
    genuinely progressing agent is never touched.

The guard only *annotates* the tool result (it never blocks execution or
mutates conversation structure), so it cannot break the message contract.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


_WS_RE = re.compile(r"\s+")


def _normalize_command(cmd: str) -> str:
    """Collapse whitespace so cosmetically-different re-issues of the same
    command still compare equal. Intentionally conservative: we do NOT strip
    arguments, so distinct commands stay distinct."""
    return _WS_RE.sub(" ", (cmd or "").strip())


def _result_signature(result: str) -> str:
    """Stable, size-bounded signature of a tool result. Uses head+tail so
    long-but-identical outputs collapse to the same signature without holding
    the whole payload."""
    r = result or ""
    r = _WS_RE.sub(" ", r.strip())
    if len(r) > 2000:
        r = r[:1000] + r[-1000:]
    return hashlib.sha1(r.encode("utf-8", "replace")).hexdigest()


_REPEAT_WARN = (
    "\n\n[RepeatedCommandGuard] You have now run the same command and gotten "
    "the identical result {count} times in a row. Repeating it again will "
    "produce the same output — this is not making progress. Stop and change "
    "approach: if the result is an error, read it carefully and fix the root "
    "cause (wrong path, missing dependency, bad input encoding, wrong tool); "
    "if the result is unchanged output you have already seen, move on to the "
    "next step of the task. Do NOT re-issue this command unchanged."
)

_REPEAT_WARN_HARD = (
    "\n\n[RepeatedCommandGuard] CRITICAL: this command has now repeated with "
    "no change {count} times and is consuming your step budget with zero new "
    "information. You must either (a) run a *materially different* command "
    "that changes inputs, environment, or tooling, or (b) accept the current "
    "state and proceed to the next required step / write your output files. "
    "Another identical attempt is wasted."
)


class RepeatedCommandGuard(MultiHookProcessor):
    """Detect and interrupt no-progress Bash command loops.

    Parameters
    ----------
    warn_threshold:
        Number of consecutive identical (command, result) pairs after which
        the first warning is appended. Counting is 1-based on repeats, so
        ``warn_threshold=3`` fires once the *3rd* identical execution lands.
    hard_threshold:
        Consecutive-repeat count at which the stronger directive replaces the
        soft warning.
    """

    _singleton_group = "tb2_repeated_command_guard"
    _order = 31  # right after CustomEditToolProcessor (30)

    def __init__(self, warn_threshold: int = 3, hard_threshold: int = 5) -> None:
        self.warn_threshold = max(2, int(warn_threshold))
        self.hard_threshold = max(self.warn_threshold + 1, int(hard_threshold))
        self._pending_cmd: dict[str, str] = {}
        self._last_cmd: str | None = None
        self._last_sig: str | None = None
        self._repeat_count: int = 0

    def _reset(self) -> None:
        self._pending_cmd.clear()
        self._last_cmd = None
        self._last_sig = None
        self._repeat_count = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_before_tool(self, event):
        # Stash the command text keyed by call id so we can pair it with its
        # result in on_after_tool.
        if getattr(event, "tool_name", None) == "Bash":
            cmd = ""
            try:
                cmd = event.tool_input.get("command", "") or ""
            except Exception:
                cmd = ""
            self._pending_cmd[event.tool_call_id] = _normalize_command(cmd)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        cmd = self._pending_cmd.pop(getattr(event, "tool_call_id", None), None)
        if cmd is None:
            # Not a Bash call we tracked (or missing id) — pass through and do
            # not let it break the repeat chain.
            yield event
            return

        sig = _result_signature(event.result or "")

        if cmd and cmd == self._last_cmd and sig == self._last_sig:
            self._repeat_count += 1
        else:
            self._repeat_count = 1
            self._last_cmd = cmd
            self._last_sig = sig

        if self._repeat_count >= self.hard_threshold:
            warn = _REPEAT_WARN_HARD.format(count=self._repeat_count)
        elif self._repeat_count >= self.warn_threshold:
            warn = _REPEAT_WARN.format(count=self._repeat_count)
        else:
            warn = None

        if warn:
            yield dataclasses.replace(event, result=(event.result or "") + warn)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
