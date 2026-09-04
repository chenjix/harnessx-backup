# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Enhanced self-verification processor for TB2.

Extends the stock ``CustomSelfVerifyProcessor`` with one extra, generalizable
checklist item: **verify computed/behavioural results against an INDEPENDENT
ground truth derived from the task's own stated definitions — never against a
re-implementation of your own logic.**

Motivation (harness deficiency, not task knowledge)
---------------------------------------------------
A recurring failure shape on TB2 is: the agent builds a solution, then
"verifies" it by running a second script that re-derives the expected answer
using the *same mental model* that produced the (buggy) solution. The two
agree because they share the bug, so the agent declares success with false
confidence and exits via ``no_tool_calls``. The stock checklist's step 4
("validate your verification method") only warns against trivial checks
(syntax / importability / exit-code-0); it does not catch the subtler
"oracle mirrors the implementation" trap.

The fix is a general strategy, applicable to any task whose correctness is a
computed value, transformation, or protocol response measured against a spec:
derive at least one expected value *by hand from the task description's own
definitions* (worked out step by step, independent of your code), and compare
the running system's actual output to that hand-derived value. If they
disagree, the implementation — not the hand derivation — is suspect.

This embeds NO task-specific constants, IDs, or algorithms. It only changes
the wording of the one-shot verification nudge.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import ModelResponseEvent, ToolCall
from benchmarks.terminal_bench_2.harness import (
    CustomSelfVerifyProcessor,
    _SELF_VERIFY_TOOL,
)


_SELF_VERIFY_MSG_INDEPENDENT = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** Does your solution address every requirement, including edge cases, accuracy thresholds, and exact output format?

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Inspect the actual file contents** — `cat` or `head` each output file and confirm the values are semantically correct, not just that the file exists or is non-empty.

4. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

5. **Verify against an INDEPENDENT ground truth, not your own logic.** If correctness is a computed value, a data transformation, or a protocol/service response, pick at least one concrete case and derive the expected answer *by hand, step by step, straight from the definitions in the task description* — do NOT re-run your implementation or a second script that re-implements the same steps, because a shared reasoning mistake will make both agree and hide the bug. Then compare the running system's ACTUAL output to your hand-derived value. If they differ, trust the task description and fix the implementation. Do this for a boundary/edge case, not just the easiest one.

6. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class IndependentSelfVerifyProcessor(CustomSelfVerifyProcessor):
    """One-shot exit-time verification nudge that adds an independent-derivation step.

    Behaviour is identical to :class:`CustomSelfVerifyProcessor` (fires at most
    once, on the first no-tool-call exit attempt); only the injected checklist
    text is stronger. Shares the same ``_singleton_group`` so it cannot coexist
    with the stock processor — the pipeline must reference exactly one of them.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pending_message = ""

    async def on_after_model(self, event: ModelResponseEvent):
        """Same one-shot gate as the parent, but injects the stronger checklist."""
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._verified:
            self._verified = True
            self._pending_message = _SELF_VERIFY_MSG_INDEPENDENT
            keepalive = ToolCall(
                id=f"sv-{uuid.uuid4().hex[:8]}",
                name=_SELF_VERIFY_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event
