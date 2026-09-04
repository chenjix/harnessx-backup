"""Enhanced self-verification checklist.

Drop-in replacement for benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor.

The base processor injects a one-shot exit-time checklist that asks the agent
to confirm required output files exist and look semantically correct. That
checklist is strong on *existence* and *format*, but weak on *derivation*:
it never pushes the agent to question HOW a reported value was computed or
selected.

Observed failure shape (general, not task-specific): on tasks that ask the
agent to report an "optimal", "converged", "minimum", "best", or otherwise
*selected* value drawn from computed data (a trajectory array, a search
result, a fitted parameter, a scan over candidates), the agent trusts its
first extraction and never re-derives it by an independent route or checks it
against the objective the task actually stated. A single wrong index or a
"last element vs. the element that optimises the objective" confusion then
passes every existence/format check and the task fails on value correctness.

This subclass keeps the base processor's control flow (fire at most once on an
exit-intent turn, keep the loop alive with a synthetic tool call) and only
augments the checklist text with one additional, fully general step:
re-derive any selected/optimal value by a second method and cross-check it
against the objective stated in the task. No task IDs, constants, file paths,
or domain terms are embedded — the guidance is a reasoning discipline that
helps on any task it has never seen.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCall,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

_SELF_VERIFY_TOOL = "_tb2_self_verify"
_SELF_VERIFY_ACK = "Verification check initiated. See the message above for instructions."

_ENHANCED_SELF_VERIFY_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** Does your solution address every requirement, including edge cases, accuracy thresholds, and exact output format?

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Inspect the actual file contents** — `cat` or `head` each output file and confirm the values are semantically correct, not just that the file exists or is non-empty.

4. **Re-derive any selected, computed, or "optimal" result a second way.** If the task asked for an *optimal / converged / minimum / maximum / best / final* value chosen from data you generated (a trajectory, a search or scan, a fit, a table of candidates), do NOT trust your first extraction. State the objective the task defined for "best" (e.g. lowest error, minimum energy, exact match), then independently confirm your reported value actually satisfies it — e.g. by scanning the full data for the true optimum rather than reading a single endpoint or index, and checking it is not merely the last or first entry. If the two routes disagree, the first extraction was wrong; fix it.

5. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

6. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class EnhancedSelfVerifyProcessor(MultiHookProcessor):
    """Inject an augmented one-shot verification prompt on exit-intent turns.

    Behaviourally identical to CustomSelfVerifyProcessor (fires at most once
    per task run on the first no-tool-call exit turn, injects exactly one user
    message on the following model turn, keeps the loop alive with a synthetic
    tool call that is never actually executed) — only the checklist text is
    extended with a general re-derivation step.
    """

    _singleton_group = "tb2_self_verify"
    _order = 90

    def __init__(self) -> None:
        self._verified = False
        self._pending_message: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._verified = False
        self._pending_message = ""
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._verified:
            self._verified = True
            self._pending_message = _ENHANCED_SELF_VERIFY_MSG
            keepalive = ToolCall(
                id=f"sv-{uuid.uuid4().hex[:8]}",
                name=_SELF_VERIFY_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _SELF_VERIFY_TOOL:
            yield dataclasses.replace(event, approved=False, synthetic_result=_SELF_VERIFY_ACK)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._verified = False
        self._pending_message = ""
        yield event
