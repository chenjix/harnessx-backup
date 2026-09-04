# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""NumericCrossCheckSelfVerifyProcessor — strengthen the one-shot exit
verification prompt so it forces an *independent* re-derivation of any
computed numeric result before the agent is allowed to finish.

Failure mode this closes
------------------------
On the tmax evolve set, a recurring cluster of computational tasks fails not
because the agent's code crashed or wrote to the wrong path, but because the
code ran cleanly, emitted a *plausible-looking* number, and the agent declared
victory without ever checking the number against a second, independent method.
The verifier then rejects the answer on an accuracy threshold.

Observed instances (same mechanism, different inputs):

* ``task_000111_cbada64a`` (scientific_computing): OLS + bootstrap in C++.
  The agent's regression code is textbook-correct yet ``m=2.5056`` while the
  verifier expects ``m≈2.5997`` (``abs err 0.094 > 1e-3``). The agent ran the
  binary once, saw a number, and self-verified only *format / file existence*.
  A 3-line ``numpy.polyfit`` cross-check would have surfaced the discrepancy
  immediately.
* ``task_001048_14335141`` (scientific_computing): chunk integral
  ``got 165.7908`` vs ``expected 161.8028`` — again a single un-cross-checked
  numeric path.
* ``task_001937_ac874115`` (scientific_computing): optimisation result
  ``Optimal Grid: 60`` vs expected ``50`` — plausible but unverified against
  an independent evaluation of the objective.

Why the existing pipeline misses it
------------------------------------
The stock ``CustomSelfVerifyProcessor`` fires a checklist on exit, but its
"semantically correct" step (step 3) only asks the agent to *look at* the
output values — it does not require re-deriving them by a different method.
"The number looks reasonable" passes that check even when the number is wrong.

Design
------
Reuse the parent's tested one-shot exit-interception mechanism verbatim
(``on_before_model`` / ``on_after_model`` keepalive dance, ``on_before_tool``
ack, task-start/end reset) and override **only the injected message** to add
an explicit independent-cross-check step for computed numeric outputs. This is
a pure strategy: it names no task, constant, path, or algorithm — it applies to
any task whose deliverable is a computed value. Non-numeric tasks are told to
skip the step, so there is no cost imposed on them beyond a few extra prompt
tokens read once per run.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    ToolCall,
    ToolCallEvent,
)

from benchmarks.terminal_bench_2.harness import CustomSelfVerifyProcessor

_SELF_VERIFY_TOOL = "_tb2_self_verify"
_SELF_VERIFY_ACK = "Verification check initiated. See the message above for instructions."

_SELF_VERIFY_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** Does your solution address every requirement, including edge cases, accuracy thresholds, and exact output format?

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Inspect the actual file contents** — `cat` or `head` each output file and confirm the values are semantically correct, not just that the file exists or is non-empty.

4. **If any required output is a COMPUTED NUMERIC RESULT (a fit parameter, statistic, integral, optimum, count, hash-derived number, confidence bound, etc.), independently re-derive it with a DIFFERENT method or tool and compare — do not trust a single code path.** A number that "looks reasonable" is not verified. Concretely:
   - Recompute the same quantity with a second, independent implementation — e.g. a short throwaway `python3 -c "..."` using a well-tested library (numpy/scipy/statistics) when your primary solution was hand-rolled or in another language, or vice-versa.
   - Confirm you consumed ALL input rows/records (`wc -l`, array length) — an off-by-one in parsing (dropped header, dropped last line, skipped blank) silently shifts results.
   - Re-check any fixed seed, index, rounding, or precision the task specified is applied exactly as written.
   - If the two methods disagree beyond the task's stated tolerance, your primary result is wrong — find and fix the discrepancy before finishing. If they agree, you have real evidence the value is correct.

5. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

6. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class NumericCrossCheckSelfVerifyProcessor(CustomSelfVerifyProcessor):
    """Drop-in replacement for ``CustomSelfVerifyProcessor`` with an added
    independent-cross-check step for computed numeric results.

    Inherits the singleton group ``tb2_self_verify`` and the one-shot
    exit-interception mechanism from the parent; overrides only the injected
    checklist text (``on_after_model``) and the ``on_before_model`` /
    ``on_before_tool`` handlers that consume it, keeping the +1-user-message
    contract identical to the parent.
    """

    def __init__(self) -> None:
        super().__init__()

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        # last message is a tool result (role != user) -> append exactly +1 user
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._verified:
            self._verified = True
            self._pending_message = _SELF_VERIFY_MSG
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
