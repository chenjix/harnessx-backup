# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Path-integrity + numeric cross-check self-verify processor for TB2.

Motivation (evolve-set evidence, structural — carries no task-specific
constants):

A recurring, high-volume failure shape on TB2 is "correct logic, wrong
path": the agent produces a functionally-correct deliverable but the file
does not land at the *exact* path the task mandates, so the external
verifier's ``os.path.isfile(<exact_path>)`` / ``os.path.exists(<exact_path>)``
assertion fails even though the work is otherwise done.

One especially self-inflicted variant of this shape: the agent hits a
transient runtime error tied to the required filename (e.g. a ``.py``
deliverable whose name shadows a stdlib module and triggers a circular
import when executed from its own directory), and "fixes" it by **renaming
or relocating the required deliverable**. The script then runs, but the
canonical file no longer exists at the required path — a hard verifier
failure. The agent frequently *notices* the deviation during self-verify
and still keeps the renamed file, because it lacks the general principle
that a required exact output path is a non-negotiable constraint: the
workaround must adapt (execute from a different working directory, or run a
differently-named *execution* copy) while the canonical deliverable stays
at the required path.

The base ``CustomSelfVerifyProcessor`` fires a one-shot checklist when the
model tries to exit, and ``NumericSelfVerifyProcessor`` adds a numeric
cross-check step to it, but neither addresses this path-integrity gap: they
tell the agent to *check* that required files exist at their exact path,
not what to do when the agent has *deliberately* moved a deliverable off
that path to work around a problem.

This subclass keeps the identical fire-once exit-interception mechanism and
the numeric cross-check verbatim, and prepends a **deliverable
path-integrity reconciliation** step. The added guidance is a general
verification *strategy* — enumerate every exact path the task names,
confirm the real deliverable sits at each one, and if any required file was
renamed / relocated / re-extensioned (even to dodge an error) restore it to
the exact path and adapt the workaround instead of the deliverable. It
contains no task ids, file paths, module names, constants, or algorithms
lifted from any training task.
"""

from __future__ import annotations

from benchmarks.terminal_bench_2.harness import CustomSelfVerifyProcessor


_PATH_INTEGRITY_SELF_VERIFY_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** List every deliverable the task names by an EXACT path (file or directory), plus every accuracy threshold and exact output-format requirement.

2. **Verify each required deliverable exists at its EXACT required path — not a nearby one:**
```bash
ls -lh /exact/required/path/for/each/deliverable
```
A script that ran without errors does NOT guarantee the file was written to the right place. Run `ls` explicitly for each required path.

3. **Path-integrity check — the required path is a hard constraint you may NOT renegotiate.** If, at any point, you renamed, moved, changed the extension of, or wrote to a different location than a required deliverable — even to work around a runtime error, a naming conflict, or an import/shadowing problem — that is a FAILURE state right now, regardless of whether your program ran successfully.
   - **Do not adapt the deliverable to your workaround; adapt the workaround to the deliverable.** Restore the canonical file to the EXACT path the task specifies.
   - If the required name/path itself caused the error (e.g. running a script from a directory that makes its filename shadow something, or a path collision), keep the deliverable at the required path and change *how you execute or use it* instead — e.g. run it from a different working directory, invoke it via an execution-only copy under a different name, or set the interpreter/search path so the collision no longer triggers. The graded artifact must be at the required path when you exit.
   - After restoring, re-run `ls` on the exact required path to confirm it is present.

4. **Inspect the actual file contents** — `cat` or `head` each output file and confirm the values are semantically correct, not just that the file exists or is non-empty. Watch for trailing whitespace/newlines and format drift that an exact-match verifier will reject.

5. **If any required output is a computed value (a number, count, statistic, coordinate, or metric), do not trust a single implementation.** A program that compiles and runs can still produce a confidently-wrong answer.
   - **Recompute independently.** Reproduce the key quantity with a second, unrelated method or tool (e.g. a short `python3`/`awk`/`numpy` one-liner) and confirm it matches your program's output. If the two disagree, at least one is wrong — reconcile before finishing.
   - **Audit input completeness.** Confirm your program consumed *every* input record — print the count of parsed rows/items and check it equals what the task states (off-by-one and dropped-last-line bugs silently shift results).
   - **Re-examine interpretation.** Re-check any ambiguous choice you made: array/loop bounds and off-by-one indices, inclusive-vs-exclusive ranges, units and scale, which column/field was read, and how ties or edge cases are handled. State the interpretation you chose and confirm it is the one the task describes.
   - **Sanity-check magnitude.** Ask whether the value is physically/plausibly reasonable given the described data; an answer that is obviously off in scale usually signals a parsing or indexing bug, not noise.

6. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

7. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class PathIntegritySelfVerifyProcessor(CustomSelfVerifyProcessor):
    """Self-verify variant that adds a deliverable path-integrity step.

    Identical fire-once exit-interception mechanics as the base class
    (fires at most once per task, injects a keepalive tool call plus one
    user message on the first no-tool-call exit intent). The only behavioral
    difference is the text of the injected checklist, which:

    - prepends an explicit path-reconciliation step: enumerate every
      required EXACT deliverable path, confirm the real file sits there, and
      if a required deliverable was renamed/relocated (even to dodge an
      error) restore it to the exact path and adapt the workaround instead;
    - keeps the numeric independent-recomputation / interpretation-audit
      guidance (a general strategy, no task literals).

    Shares ``_singleton_group = "tb2_self_verify"`` with the stock
    ``CustomSelfVerifyProcessor`` so it REPLACES whichever self-verify is
    otherwise wired — only one self-verify processor fires per task.
    """

    _singleton_group = "tb2_self_verify"

    async def on_after_model(self, event):  # type: ignore[override]
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._verified:
            # Delegate to the base to run the fire-once mechanism (sets
            # self._verified=True and emits the keepalive tool call), then
            # overwrite the stashed message with the path-integrity variant.
            async for out in super().on_after_model(event):
                if self._pending_message:
                    self._pending_message = _PATH_INTEGRITY_SELF_VERIFY_MSG
                yield out
        else:
            async for out in super().on_after_model(event):
                yield out
