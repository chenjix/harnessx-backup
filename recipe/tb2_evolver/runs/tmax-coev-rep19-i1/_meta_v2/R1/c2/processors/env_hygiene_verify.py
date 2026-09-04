"""EnvHygieneVerifyProcessor — extend the exit-time self-verification checklist.

Motivation (harness deficiency, generalizable class):
    In Terminal-Bench-style tasks the verifier runs *inside the same
    container* after the agent exits. If the agent leaves the container in
    a state where the verifier's own tooling cannot even start, the task
    fails with reward 0 even though the agent's work was functionally
    correct. Two distinct, recurring shapes of this appeared in the round:

      * The verifier's Python test could not be COLLECTED because a client
        library it imports was not installed in the container (e.g. an HTTP
        client for a service task). The agent's service was correct and
        reachable, but the verifier crashed at import time.
      * The agent created a script in the working/home directory whose name
        collided with a Python standard-library / common package module,
        shadowing it, so the verifier's interpreter crashed at collection.

    Both are environment-hygiene gaps the agent can detect and fix before
    exiting, but the existing self-verify checklist never prompts for them.

This processor is a thin subclass of the benchmark's built-in
CustomSelfVerifyProcessor. It reuses all of the base one-shot / keepalive /
before-model injection mechanics unchanged, and only swaps the *text* of the
checklist injected on the agent's exit turn to add two general hygiene items.
No task-specific literals — the guidance is a strategy that applies to any
task validated by an in-container automated checker.
"""

from __future__ import annotations

from benchmarks.terminal_bench_2.harness import CustomSelfVerifyProcessor


# The base checklist, plus two general environment-hygiene items. Kept as a
# self-contained string so this processor does not depend on the base class's
# private module-level message constant.
_ENV_HYGIENE_VERIFY_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** Does your solution address every requirement, including edge cases, accuracy thresholds, and exact output format?

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Inspect the actual file contents** — `cat` or `head` each output file and confirm the values are semantically correct, not just that the file exists or is non-empty.

4. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

5. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

6. **Leave the environment ready for an automated checker.** After you exit, an automated verifier runs inside this same container — you will not be here to fix things. Two failure modes make correct work score zero:
   - **Missing check-time dependencies.** If a checker would likely inspect your work by running a program (e.g. importing a standard HTTP/JSON/data client to call your service or parse your output), confirm that program's dependencies are actually importable now — do not assume common libraries are preinstalled. If a needed library is absent and can be installed, install it.
   - **Shadowed standard modules.** A script you created in the working or home directory whose filename matches a standard-library or common package module name can break the checker's interpreter at import time. Check for such name collisions and rename or relocate the offending file.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class EnvHygieneVerifyProcessor(CustomSelfVerifyProcessor):
    """CustomSelfVerifyProcessor with two extra environment-hygiene checklist items.

    Overrides only the injected checklist text; all hook mechanics
    (one-shot firing, keepalive tool call, before-model injection,
    task-start/end reset) are inherited unchanged from the base class.
    """

    _singleton_group = "tb2_self_verify"

    async def on_after_model(self, event):
        # Delegate to the base to reuse its exit-intent detection and
        # keepalive-tool-call insertion, then swap the pending checklist text
        # for the extended one when the base decided to fire.
        async for out in super().on_after_model(event):
            if self._pending_message:
                self._pending_message = _ENV_HYGIENE_VERIFY_MSG
            yield out
