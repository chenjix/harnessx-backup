# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Numeric / interpretation cross-check self-verify processor for TB2.

Motivation (evolve-set evidence, structural — carries no task-specific
constants):

A recurring failure shape on quantitative-output tasks is the agent
implementing *one* self-consistent interpretation of an ambiguous spec,
producing a plausible-but-wrong scalar, and calling ``end_turn`` after only
a shallow spec-compliance re-read.  The verifier then rejects the value.

Examples of the shape (all `exit_reason=done`, no correctness self-doubt):
  - fixed-width column parse that grabbed the wrong byte window
  - a count / boundary read off-by-one (e.g. N vs N-1 frames, grid edges)
  - a computed statistic that differs from the reference by more than the
    tolerance despite textbook-correct code

The base ``CustomSelfVerifyProcessor`` already injects a one-shot checklist
when the model tries to exit, but that checklist only asks the agent to
re-read the spec and confirm files exist — it gives no *technique* for
catching a wrong numeric result.  This subclass keeps the identical exit
interception mechanism (fire-once, keepalive tool call, +1 user message)
and only swaps in a checklist that adds an explicit
independent-recomputation / interpretation-audit step for tasks whose
output is a computed value.

The added guidance is a general verification *strategy* — recompute the key
quantity by a second independent method/tool and reconcile any mismatch,
audit data-completeness (were all input rows parsed?), and re-examine
index / boundary / unit interpretation.  It contains no task ids, file
paths, constants, or algorithms lifted from any training task.
"""

from __future__ import annotations

from benchmarks.terminal_bench_2.harness import CustomSelfVerifyProcessor


_NUMERIC_SELF_VERIFY_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** Does your solution address every requirement, including edge cases, accuracy thresholds, and exact output format?

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Inspect the actual file contents** — `cat` or `head` each output file and confirm the values are semantically correct, not just that the file exists or is non-empty.

4. **If any required output is a computed value (a number, count, statistic, coordinate, or metric), do not trust a single implementation.** A program that compiles and runs can still produce a confidently-wrong answer.
   - **Recompute independently.** Reproduce the key quantity with a second, unrelated method or tool (e.g. a short `python3`/`awk`/`numpy` one-liner) and confirm it matches your program's output. If the two disagree, at least one is wrong — reconcile before finishing.
   - **Audit input completeness.** Confirm your program consumed *every* input record — print the count of parsed rows/items and check it equals what the task states (off-by-one and dropped-last-line bugs silently shift results).
   - **Re-examine interpretation.** Re-check any ambiguous choice you made: array/loop bounds and off-by-one indices, inclusive-vs-exclusive ranges, units and scale, which column/field was read, and how ties or edge cases are handled. State the interpretation you chose and confirm it is the one the task describes.
   - **Sanity-check magnitude.** Ask whether the value is physically/plausibly reasonable given the described data; an answer that is obviously off in scale usually signals a parsing or indexing bug, not noise.

5. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

6. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class NumericSelfVerifyProcessor(CustomSelfVerifyProcessor):
    """CustomSelfVerifyProcessor variant with a numeric cross-check step.

    Identical exit-interception mechanics as the base class (fires at most
    once per task, injects a keepalive tool call plus one user message on the
    first no-tool-call exit intent).  The only behavioral difference is the
    text of the injected checklist, which adds an explicit
    independent-recomputation / interpretation-audit step for computed-value
    outputs.
    """

    _singleton_group = "tb2_self_verify"

    async def on_after_model(self, event):  # type: ignore[override]
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._verified:
            # Reuse the base mechanism but with the enriched message. We set
            # the pending message ourselves, then delegate to the base to emit
            # the keepalive tool call; the base only overwrites _pending_message
            # if it *also* sees the exit intent, so we set it after the call.
            async for out in super().on_after_model(event):
                # Base has now set self._verified=True and stashed the default
                # message; replace it with the numeric-aware variant.
                if self._pending_message:
                    self._pending_message = _NUMERIC_SELF_VERIFY_MSG
                yield out
        else:
            async for out in super().on_after_model(event):
                yield out
