# SPDX-License-Identifier: MIT
"""StrictSelfVerifyProcessor — a stronger pre-exit verification checklist.

Drop-in replacement for ``CustomSelfVerifyProcessor`` (same singleton
group, same one-shot fire mechanism). The only behavioural difference is
the text of the checklist injected on the model's first attempt to exit
without tool calls.

Motivation (harness deficiency, not task knowledge)
---------------------------------------------------
The stock self-verify checklist is correct in spirit but too abstract to
catch a recurring failure shape on this benchmark: the agent declares the
task complete while an *explicitly stated* requirement is silently unmet,
because its self-check inspects output "semantically" instead of matching
it against the literal wording of each requirement.

Observed shapes (mechanism, not memorised tasks):
  * a spec says "redirect (append) stdout to <log>" and the agent uses a
    truncating ``>`` instead of ``>>``; a "run it twice" idempotency check
    that only *eyeballs* one output can't detect the missing second line.
  * a spec says "rounded to 2 decimal places" and the agent emits ``33.0``
    where ``33.00`` was required; a "cat the file, looks right" check
    passes anyway.

Both are exact-wording / observable-invariant mismatches. The fix is to
make the injected checklist force two general disciplines that any unseen
spec-detail task benefits from:

  1. Enumerate each explicit requirement as its own line and confirm the
     implementation matches the *literal wording* — append vs overwrite,
     exact output format / precision / separators, exact counts and
     ordering, exact paths.
  2. For any "idempotent / repeatable / run multiple times" requirement,
     actually re-run and compare the resulting observable state to what
     the spec says should happen after repetition — never conclude
     "idempotent" from a single inspection.

No task IDs, constants, or task-specific strings are embedded; the
guidance is a general verification strategy.
"""

from __future__ import annotations

from benchmarks.terminal_bench_2.harness import CustomSelfVerifyProcessor

_STRICT_SELF_VERIFY_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now, requirement by requirement.** For EACH explicit requirement, write one line: the requirement's literal wording, then how your implementation satisfies it. Pay special attention to wording that is easy to satisfy the *wrong* way:
   - append vs overwrite (`>>` vs `>`, add-a-line vs replace-file)
   - exact output format: decimal precision, trailing zeros, separators, quoting, units, capitalisation, and whitespace
   - exact counts, number of lines/rows/records, and their ordering
   - exact file paths, names, and extensions
   A requirement you cannot point to a concrete line of your solution for is a requirement you probably have not met.

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Inspect the actual file contents** — `cat`/`head` each output file and compare byte-for-byte against the required format from step 1, not just "looks about right". If the spec pins a number's precision or a line's exact text, confirm the characters match exactly.

4. **Validate your verification method against the real invariant.** A test that only checks syntax, importability, exit code 0, or that a file is non-empty is NOT valid. If a requirement is stated in terms of behaviour after repetition or over time — e.g. "idempotent", "run multiple times", "does not append duplicates", "safe to re-run" — you MUST actually reproduce that scenario: run the operation the stated number of times (at least twice), then re-measure the observable state (line counts, row counts, file contents, process state) and confirm it matches what the spec says should hold after repetition. Do not conclude a repeatability property from a single execution or a single glance at the output.

5. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class StrictSelfVerifyProcessor(CustomSelfVerifyProcessor):
    """``CustomSelfVerifyProcessor`` with a stricter, exact-wording checklist.

    Inherits the one-shot fire mechanism and message-injection contract
    verbatim; only the pending checklist text differs. Because the parent
    stores the message on ``self._pending_message`` and reads it in
    ``on_before_model``, overriding ``on_after_model`` to set our own text
    keeps the +1-user-message contract identical to the parent.
    """

    _singleton_group = "tb2_self_verify"
    _order = 90

    async def on_after_model(self, event):
        # Reuse the parent's decision logic, then swap the injected text.
        async for out in super().on_after_model(event):
            if self._pending_message:
                self._pending_message = _STRICT_SELF_VERIFY_MSG
            yield out
