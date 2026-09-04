# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LoopBudgetGuardProcessor.

Closes the dominant R4 failing shape: tasks that grind to the step / wall-clock
budget (``exit_reason=budget_exceeded`` at the 80-step cap) by issuing the SAME
Bash command over and over without ever adapting to its result. In R4 (incumbent
config, with no active loop guard) at least 4 of the 8 budget_exceeded failures
were dominated by a single byte-identical command repeated 22-33 times:

  * task_000118: ``python3 deployment_monitor.py & ... ps aux`` x33 (relaunching
    a crashing process without debugging it);
  * task_000863: identical heredoc file write x29;
  * task_001032: identical heredoc file write x31;
  * task_001652: identical failing ``strings ... | grep`` x22, with the model
    even *narrating* "I'm stuck in a loop" between repeats yet re-issuing it.

This is a **harness deficiency**, not a domain-knowledge gap: the model never
reacts to the (unchanging) tool result, so it cannot recover on its own within
budget. The prior CustomEditToolProcessor emits only a passive text warning that
the model ignores.

Mechanism (Control with teeth), keyed purely on the normalized text of Bash
commands — never on task content, so it generalizes across every domain:

1. Count how many times each whitespace-normalized command has been issued
   *in total* this task (not just consecutively). This catches both the pure
   consecutive loop and the interleaved-narration / A-B-A-B cycling loop that a
   consecutive-only detector misses.
2. Once a command has been issued ``nudge_threshold`` times, arm exactly one
   escalating ``user`` redirect naming the loop.
3. Once it reaches ``suppress_threshold`` times, *suppress the physical
   execution* by returning a synthetic result (``approved=False``). Re-running a
   command that has already produced the same result many times cannot make
   progress; blocking it forces a materially different action and reclaims the
   remaining step budget.

Thresholds are deliberately high enough that a healthy run — which never issues
the exact same command many times — is a strict no-op. The guard only bites once
a genuine degenerate loop is established.
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


_NUDGE = (
    "[LoopGuard] You have now issued the SAME command {n} times in this task "
    "without changing it, and it keeps producing the same result. Repeating it "
    "again cannot make progress. STOP retrying it. In one sentence, state what "
    "the last tool output actually shows versus what you expected — then take a "
    "MATERIALLY DIFFERENT action: inspect the underlying error line by line, "
    "verify an assumption you have not checked (a file path, a process's exit "
    "status, the exact error message), or solve the task a different way. Your "
    "next command MUST differ from the repeated one."
)

_SUPPRESSED = (
    "[LoopGuard] This command was NOT executed. It is identical to a command you "
    "have already run {n} times in this task, all with the same result. "
    "Re-running it cannot make progress and is burning your remaining step "
    "budget. You MUST change approach now: read the last real output above, "
    "identify why the repeated command is not achieving the goal, and issue a "
    "DIFFERENT command that addresses the root cause."
)


class LoopBudgetGuardProcessor(MultiHookProcessor):
    """Break degenerate identical-command loops before they exhaust the budget.

    Parameters
    ----------
    nudge_threshold:
        Total number of times a normalized Bash command may be issued before an
        escalating ``user`` redirect is armed. Default 3.
    suppress_threshold:
        Total-issue count at (or above) which the execution itself is suppressed
        and replaced with a synthetic result. Must be > ``nudge_threshold`` to
        leave the model a chance to self-correct after the first nudge.
        Default 5.
    """

    _singleton_group = "tb2_loop_budget_guard"
    # Run after EditDetection (CustomEditToolProcessor) and before the
    # self-verify keepalive, matching the prior guard's slot.
    _order = 40

    def __init__(
        self,
        nudge_threshold: int = 3,
        suppress_threshold: int = 5,
    ) -> None:
        self.nudge_threshold = max(2, int(nudge_threshold))
        self.suppress_threshold = max(self.nudge_threshold + 1, int(suppress_threshold))
        self._counts: dict[str, int] = {}
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._counts = {}
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

        count = self._counts.get(cmd, 0) + 1
        self._counts[cmd] = count

        # Suppress once the loop is entrenched: the synthetic result stands in for
        # the tool output so the run loop keeps going, but the model no longer gets
        # a fresh identical result to react to — it must change course.
        if count >= self.suppress_threshold:
            self._pending_nudge = ""  # the synthetic result already carries guidance
            yield dataclasses.replace(
                event,
                approved=False,
                synthetic_result=_SUPPRESSED.format(n=count - 1),
            )
            return

        # Otherwise arm a single redirect nudge but let the command run once more
        # so the model still sees a real result alongside the warning.
        if count >= self.nudge_threshold:
            self._pending_nudge = _NUDGE.format(n=count)

        yield event

    async def on_after_tool(self, event: ToolResultEvent):
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
        self._counts = {}
        self._pending_nudge = ""
        yield event
