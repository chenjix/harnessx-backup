# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandBreakerProcessor — escalating, deliverable-grounded nudge for
identical-command repetition loops.

Systemic failure mode observed on the weak-model Tmax eval (R1 trajectories):
several ``budget_exceeded``-at-80-steps tasks are dominated by the agent
re-issuing the **exact same Bash command** turn after turn, each returning the
same result (frequently ``(exit 0, no output captured)``), while never producing
the concrete output artifacts the task requires.

Verified (R1 run):
  * task_000958: 33 tool calls, ALL 33 identical (a broken server restart loop).
  * task_001818: 27 of 29 identical.
  * task_001032: 25 of 29 identical.
  * task_000264: 21 consecutive identical ``cat > /tmp/test.sql`` heredoc writes;
    the agent had a correct recursive CTE but re-wrote the intermediate SQL file
    over and over and NEVER referenced the required deliverables
    (top_managers.csv / query_plan.txt) in a single command.

Why the existing guards don't close this:
  * ``LengthLoopDeprimeProcessor`` (R1) only fires on ``finish_reason == "length"``
    truncations; these loops complete normally with a tool call each turn.
  * ``CustomEditToolProcessor`` warns on file over-editing, but (a) fired 3x on
    task_000264 and was **ignored** — a generic "try something different" nudge
    is too weak for this model — and (b) never fires on non-edit command loops
    like task_000958's ``pkill; ./server; ps`` cycle.

Why NOT the stock ``LoopDetectionProcessor`` (which *raises* LoopDetectedError
at 5 identical calls): two currently-PASSING tasks issue 27 consecutive
identical commands and still pass (task_000536 re-writes an audit script 27x;
task_001089 loops on a malformed empty tool call 27x). A hard raise would kill
both → a 2-task regression to chase 1 speculative flip. The identical-command
signature does NOT discriminate pass from fail, so this processor is
**warn-only, never raises**. It preserves the passing loopers untouched while
giving the failing loopers an escalating, deliverable-grounded directive that
the generic edit-warning did not provide.

Mechanism (Control lever): fingerprint each tool call (name + serialised
inputs) in ``on_before_tool``; in ``on_after_tool`` count the consecutive
identical run at the tail. At ``warn_at`` repeats append a directive telling the
agent it is repeating a command with no change and to STOP and instead VERIFY
the task's required output artifacts exist / are non-empty (or take a
fundamentally different action). At ``escalate_at`` the directive hardens: the
loop is confirmed unproductive; produce the required deliverable now. Interleaving
any different command resets the run count, so legitimate varied exploration is
never touched.

Contract-safe: only appends to the current ``ToolResultEvent.result`` string in
``on_after_tool`` (same surface the stock LoopDetectionProcessor mutates) — it
never touches ``event.messages``, the system prompt, or history ordering.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import deque

from harnessx.core.events import (
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


_WARN_TEMPLATE = (
    "\n\n[RepeatedCommand] ⚠️  You have run the EXACT same command {count} times "
    "in a row and the result has not changed. Repeating it again will not help. "
    "STOP repeating it. Re-read the task description and list the concrete output "
    "artifacts it requires (files at specific paths, a running service, a report). "
    "Your next command should either (a) VERIFY one of those required deliverables "
    "actually exists and is non-empty (e.g. `ls -l <path>`, `cat <path>`), or "
    "(b) take a fundamentally different action toward producing it — not another "
    "copy of the same command."
)

_ESCALATE_TEMPLATE = (
    "\n\n[RepeatedCommand] ⛔ STOP — this is the {count}th identical run of the same "
    "command and it is producing no progress. You are burning the step budget in a "
    "loop. Do NOT run this command again. The task will only be scored on its "
    "required output artifacts, which you have not yet produced. In your next turn, "
    "issue ONE different command that writes or verifies a required deliverable at "
    "the exact path the task specifies. If you believe the deliverable is already "
    "correct, verify it directly (`ls -l` / `cat` the required path) and then finish."
)


class RepeatedCommandBreakerProcessor(MultiHookProcessor):
    """Warn-only escalating breaker for consecutive identical tool-call loops.

    Args:
        warn_at:      Consecutive-identical count at which the first (verify-your-
                      deliverables) directive is appended to the tool result
                      (default 3).
        escalate_at:  Consecutive-identical count at which the hardened directive
                      is appended instead (default 5). Never raises.
        window_size:  Sliding fingerprint window (default 12).
        compaction_drop_threshold: message-count drop that signals compaction and
                      clears the fingerprint window to avoid stale counts (default 5).
    """

    _singleton_group = "tmax_repeated_command_breaker"
    _order = 21  # after loop_detection's slot; operates only on tool results

    def __init__(
        self,
        warn_at: int = 3,
        escalate_at: int = 5,
        window_size: int = 12,
        compaction_drop_threshold: int = 5,
    ) -> None:
        self.warn_at = max(2, int(warn_at))
        self.escalate_at = max(self.warn_at + 1, int(escalate_at))
        self.window_size = max(2, int(window_size))
        self.compaction_drop_threshold = max(1, int(compaction_drop_threshold))

        self._fingerprints: deque[str] = deque(maxlen=self.window_size)
        self._pending_fp: dict[str, str] = {}
        self._current_run_id: str = ""
        self._prev_message_count: int = 0

    # ------------------------------------------------------------------
    def _fingerprint(self, tool_name: str, tool_input) -> str:
        try:
            input_str = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
        except Exception:
            input_str = repr(tool_input)
        return hashlib.sha256(f"{tool_name}\x00{input_str}".encode()).hexdigest()[:16]

    @staticmethod
    def _consecutive_tail(window: deque, fp: str) -> int:
        count = 0
        for past in reversed(window):
            if past == fp:
                count += 1
            else:
                break
        return count

    def _reset(self) -> None:
        self._fingerprints.clear()
        self._pending_fp.clear()
        self._prev_message_count = 0

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
            self._fingerprints.clear()
            self._pending_fp.clear()
        self._prev_message_count = current
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        self._pending_fp[event.tool_call_id] = self._fingerprint(
            event.tool_name, event.tool_input
        )
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        fp = self._pending_fp.pop(event.tool_call_id, None)
        if not fp:
            # No fingerprint captured → treat as a run-breaking step.
            self._fingerprints.append("")
            yield event
            return

        run = self._consecutive_tail(self._fingerprints, fp) + 1
        self._fingerprints.append(fp)

        directive = ""
        if run >= self.escalate_at:
            directive = _ESCALATE_TEMPLATE.format(count=run)
        elif run >= self.warn_at:
            directive = _WARN_TEMPLATE.format(count=run)

        if directive:
            yield dataclasses.replace(event, result=(event.result or "") + directive)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
