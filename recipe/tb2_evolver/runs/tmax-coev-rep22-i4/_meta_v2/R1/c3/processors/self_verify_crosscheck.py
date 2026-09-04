# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Stronger self-verification checklist for computation-heavy TB2 tasks.

Motivation (evolve-set evidence, no task-specific constants baked in)
---------------------------------------------------------------------
The stock ``CustomSelfVerifyProcessor`` injects a one-shot checklist when the
agent tries to exit with no tool call.  Its step 3 ("inspect the actual file
contents ... confirm the values are semantically correct") is passive: it asks
the agent to *look* at the output but does not force it to *re-derive* anything
or to reconcile its own reasoning with the values it produced.

On the evolve set this is the dominant unrecovered failure shape for
computation tasks: the agent writes a program that runs cleanly, produces a
numerically WRONG answer, and exits via ``no_tool_calls`` fully convinced it is
done — even when its own narration contradicts the output.  A representative
example in the trajectories: an agent stated in prose that a data window was
entirely zero while the value it actually emitted for that window was clearly
nonzero, an internal contradiction it never reconciled because the checklist
only asked it to "inspect" the file.  The same confident-exit-on-wrong-numbers
shape recurs across several data_science / scientific_computing / security tasks
(all ``no_tool_calls`` with low step counts).

This processor keeps every mechanical property of the stock verifier (fires at
most once, on the exit turn, via the same keepalive tool-call trick, +1 user
message) but swaps the injected checklist for one that adds two general
debugging disciplines:

  * **Independent re-derivation** — recompute at least one output value by a
    *different* method (e.g. a quick shell/awk/python one-liner over the raw
    input) and confirm it matches the value your program wrote.
  * **Contradiction sweep** — compare intermediate diagnostics / your own
    stated expectations against the final output; if any value looks
    surprising or contradicts something you concluded earlier, treat that as a
    bug in your program, not a curiosity to rationalise.

None of this carries task-specific knowledge: it is a verification *strategy*
that applies to any task that produces computed outputs, seen or unseen.
"""

from __future__ import annotations

from benchmarks.terminal_bench_2.harness import CustomSelfVerifyProcessor


_CROSSCHECK_MSG = """\
Before finishing, run through this verification checklist — do not skip any \
step even if you already verified. A program that ran without errors does NOT \
mean the answer is correct.

1. **Re-read the task description now.** List every requirement, including \
edge cases, accuracy thresholds, exact output paths, and exact output format. \
Check your solution against each one explicitly.

2. **Confirm every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
Run `ls` explicitly for each required file — do not assume.

3. **Inspect the actual contents** of each output file (`cat`/`head`) and read \
the values, not just the file size.

4. **Independently re-derive at least one output value by a DIFFERENT method** \
than the program you wrote — for example a quick shell / awk / python one-liner \
that computes the same quantity straight from the raw input — and confirm it \
matches what your program emitted. If they disagree, your program has a bug: \
fix it before exiting. Silent parsing/offset/type-conversion errors routinely \
produce plausible-looking but wrong numbers.

5. **Contradiction sweep.** Compare your own earlier reasoning and any \
intermediate diagnostics against the final output. If a value looks surprising \
or contradicts something you concluded while working (e.g. you expected a \
window to be all zeros but its mean is nonzero), that is a symptom of a bug in \
your program — investigate and fix it rather than rationalising it away.

6. **Validate your verification method.** A test that only checks syntax, \
importability, or exit-code-0 on a trivial case is NOT valid — it passes even \
on a broken implementation. Make sure you exercised the real behaviour on the \
real input.

7. **For running services:** confirm they are still alive and reachable right \
now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your \
final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class SelfVerifyCrossCheckProcessor(CustomSelfVerifyProcessor):
    """``CustomSelfVerifyProcessor`` with a re-derivation + contradiction checklist.

    Reuses the parent's hook mechanics verbatim; only the injected checklist
    string is overridden, so the fires-once / exit-turn / keepalive contract is
    identical.  The override is applied by pointing the module-level constant
    the parent reads (``_SELF_VERIFY_MSG``) at the stronger text while this
    processor's message is pending, then restoring it — but simplest and safest
    is to set the pending message directly in ``on_after_model``.
    """

    _singleton_group = "tb2_self_verify"

    async def on_after_model(self, event):
        # Delegate to the parent to preserve the exact exit-intent detection and
        # keepalive-tool-call wiring, then upgrade the pending checklist text.
        async for out in super().on_after_model(event):
            if self._pending_message:
                self._pending_message = _CROSSCHECK_MSG
            yield out
