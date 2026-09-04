# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""BashLoopBreakerProcessor — break degenerate identical-Bash repetition loops.

Systemic failure mode observed in the Tmax R0 trajectories: on hard tasks the
model enters a *tool-level repetition loop* — it re-issues the byte-for-byte
same ``Bash`` command (same command string, same output) over and over,
narrating "I'm stuck in a loop / I need to stop repeating this" while doing
exactly that, until it exhausts the step budget (``exit_reason=budget_exceeded``
at the step cap). Nine of eleven budget-exceeded tasks in R0 showed 10-33
consecutive identical Bash calls; the loops began as early as the 3rd-7th Bash
call, wasting the remaining ~25-75 steps.

This is distinct from ``LengthTruncationRecoveryProcessor`` (which handles
``finish_reason=length`` with *no* tool call). Here the model *does* emit a
tool call every turn — a well-formed but useless repeat.

Design decisions (grounded in the R0 evidence):

* **Bash-only fingerprinting.** Internal/injected tools (e.g. the self-verify
  tool ``_tb2_self_verify``) are legitimately called many times in a row in
  *passing* trajectories (one passing task issued 22 consecutive self-verify
  calls). Fingerprinting those would produce false positives and could kill a
  passing run. Only the model-authored ``Bash`` tool is tracked.
* **Two-phase, warn-then-break.** A warning nudge is appended to the tool
  result once the same command repeats ``warn_threshold`` times, giving the
  agent a chance to abandon the dead command while it still has budget. If it
  keeps repeating up to ``break_threshold``, the processor intercepts the
  next identical call (does not execute it) and returns a strong corrective
  message instead — the loop is broken without ending the whole task, so the
  agent keeps its remaining steps for a different approach.
* **Conservative thresholds.** Every R0 *passing* task had a max consecutive
  identical-Bash run of exactly 1 — no passing task ever repeated an identical
  command even twice. So the regression surface at ``warn_threshold=3`` /
  ``break_threshold=6`` is effectively nil on the observed passing set.

Strategy-only / benchmark-agnostic: no task ids, no command literals, no
domain constants. Fires purely on the structural "identical Bash call N times
in a row" shape.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

_BASH_TOOL = "Bash"

_WARN_TEMPLATE = (
    "\n\n[LoopBreaker] ⚠️  You have now run this EXACT same command {count} times "
    "in a row and gotten the same result each time. Repeating it again will not "
    "change anything. Stop. Re-read the task requirements, look at what this "
    "output is actually telling you, and take a FUNDAMENTALLY different action: "
    "inspect a different file, change the command's arguments, try another "
    "approach, or — if the required deliverable already exists — verify it and "
    "finish. Do not issue this command again."
)

_BREAK_TEMPLATE = (
    "[LoopBreaker] BLOCKED: this exact command has now been issued {count} times "
    "consecutively with no change in result, so it was NOT executed again. You are "
    "in a dead loop. This line of attack is not working — abandon it completely.\n\n"
    "Do ONE of the following in your next turn:\n"
    "  1. If the task's required output file(s) already exist at the path named in "
    "the task, run a quick check to confirm and then stop.\n"
    "  2. Otherwise, try a genuinely different strategy: a different command, tool, "
    "algorithm, input, or file. Do not re-run the blocked command or a trivial "
    "variant of it.\n"
    "Write at most two sentences of reasoning, then take that different action."
)


class BashLoopBreakerProcessor(MultiHookProcessor):
    """Detect and break byte-for-byte repeated Bash commands.

    Args:
        warn_threshold: consecutive identical-Bash count at which a warning is
            appended to the tool result (default 3).
        break_threshold: consecutive identical-Bash count at which the next
            identical call is intercepted (not executed) and replaced with a
            corrective message (default 6). Must be >= warn_threshold.
    """

    _singleton_group = "bash_loop_breaker"
    _order = 21  # just after LoopDetectionProcessor's slot; before compaction reset logic

    def __init__(
        self,
        warn_threshold: int = 3,
        break_threshold: int = 6,
    ) -> None:
        self.warn_threshold = max(2, int(warn_threshold))
        self.break_threshold = max(self.warn_threshold + 1, int(break_threshold))
        self._last_fp: str = ""
        self._run: int = 0
        # tool_call_id -> fingerprint, staged in on_before_tool
        self._pending: dict[str, str] = {}

    # ------------------------------------------------------------------
    def _reset(self) -> None:
        self._last_fp = ""
        self._run = 0
        self._pending.clear()

    @staticmethod
    def _fingerprint(tool_input: dict) -> str:
        try:
            s = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
        except Exception:
            s = repr(tool_input)
        return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:16]

    # ------------------------------------------------------------------
    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # Only track the model-authored Bash tool. Any other tool (including
        # injected internal tools) resets the consecutive run so their repeats
        # never contribute to the loop count.
        if event.tool_name != _BASH_TOOL:
            self._last_fp = ""
            self._run = 0
            yield event
            return

        fp = self._fingerprint(event.tool_input)
        if fp and fp == self._last_fp:
            prospective = self._run + 1
        else:
            prospective = 1

        # At/over the break threshold: intercept — do not execute the command
        # again. Inject a corrective synthetic result instead.
        if prospective >= self.break_threshold:
            # keep the run counter advancing so a persistent offender keeps
            # getting blocked rather than oscillating around the threshold
            self._last_fp = fp
            self._run = prospective
            msg = _BREAK_TEMPLATE.format(count=prospective)
            yield dataclasses.replace(
                event,
                approved=False,
                synthetic_result=msg,
            )
            return

        # Otherwise stage the fingerprint for on_after_tool to consume.
        self._pending[event.tool_call_id] = fp
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        fp = self._pending.pop(event.tool_call_id, None)
        if fp is None:
            # Not a tracked Bash call (or was intercepted). Leave as-is.
            yield event
            return

        if fp and fp == self._last_fp:
            self._run += 1
        else:
            self._last_fp = fp
            self._run = 1

        if self._run >= self.warn_threshold:
            warning = _WARN_TEMPLATE.format(count=self._run)
            yield dataclasses.replace(event, result=(event.result or "") + warning)
        else:
            yield event
