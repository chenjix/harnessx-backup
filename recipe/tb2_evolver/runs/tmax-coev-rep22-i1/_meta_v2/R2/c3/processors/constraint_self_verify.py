# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Constraint-reconciliation self-verification nudge for TB2 tasks graded on
an observed quantitative limit.

Motivation
----------
A large recurring failure cluster in this benchmark is tasks that exit with a
confident "SUCCESS / task complete" claim yet score 0. Reading the bodies, the
decisive error is not a missing file or a dead service — it is that the agent
treats *the workload finishing* (a script exited 0, all workers ran to
completion, a build succeeded) as proof the *constraint* was satisfied, while
its OWN earlier tool output already contained a measurement that violated the
task's stated numeric limit.

The clearest instance: a disk-quota / log-size monitor task. The agent ran its
solution end-to-end, saw the logs directory reach the full unbounded size in a
directory listing it printed itself, and then concluded "all workers completed,
so the monitor triggered protection" — reasoning backwards from completion to
success and ignoring the over-limit number in front of it.

The stock ``CustomSelfVerifyProcessor`` checklist (steps 1-5) and the R1/c4
``LifecycleSelfVerifyProcessor`` addendum (item 6) both *ask* the agent to
confirm final state and resource bounds, but they frame it as "confirm the
observed value stays within the limit" — which the agent satisfies by
re-inspecting the artifact's source instead of RE-RUNNING the graded scenario
from a clean state and reading the peak. The missing idea is behavioural, not
domain-specific:

  * Completion of the workload is NOT evidence the constraint held.
  * A measurement you already observed that exceeds the stated limit means the
    solution FAILED, even if everything "ran cleanly".
  * The only valid check for a constraint-bound task is to reproduce the graded
    scenario from a clean state and read the peak/final observed value.

This is a harness verification-nudge deficiency (the mechanism to check is
already in the agent's hands via Bash), not a model-knowledge gap, and the
fix is a strictly additive, general checklist item with no task-specific
identifiers, constants, paths, or thresholds. It fires at most once per task,
exactly like the stock processor, so it adds no step-count cost on tasks that
have no measured-constraint component and does not block or reject any tool
call.
"""

from __future__ import annotations

from benchmarks.terminal_bench_2.harness import CustomSelfVerifyProcessor

# --- Reuse the R1/c4 lifecycle addendum (item 6) verbatim so we do not regress
# the process-lifecycle nudge that is already in the pipeline, then append the
# constraint-reconciliation item (7). General strategy only — no task-specific
# identifiers, ports, paths, thresholds, or constants.
_LIFECYCLE_ADDENDUM = """

6. **Final process / service state must match the lifecycle the task asks for.**
   Services are graded on their state *after you stop*, not on whether a script
   once ran cleanly. Reconcile the two cases explicitly:
   - If the task asks you to **start / keep** a service running: confirm the
     process is alive right now (`pgrep -f <name>` or `os.kill(pid, 0)`), and
     that any PID file you wrote points at the actual service process — check
     its identity with `cat /proc/<pid>/comm` and make sure it names your
     compiled/target program, not a wrapping `bash`/shell/`go run` launcher.
   - If the task asks you to **stop / gracefully shut down / clean up** a
     service (or you ran a start-then-stop pipeline): confirm **no** matching
     process is left behind — `pgrep -f <name>` must return nothing. Killing by
     a saved PID can miss children or a respawned process; verify the result,
     and if anything lingers, terminate every match and re-check.
   - If the task bounds a background workload (log size, disk, memory, run
     time): confirm the observed value stays within the stated limit rather
     than assuming the workers self-regulate.

7. **If the task states a numeric limit or threshold, that number is the pass
   criterion — reproduce and measure it, do not infer it.**
   - A workload finishing (a script exiting 0, every worker/process running to
     completion, a build succeeding) is **NOT** evidence the limit held. Do not
     reason "everything completed, therefore the constraint was satisfied" —
     completion and constraint-compliance are independent.
   - If any earlier command you ran already printed a measurement that
     **exceeds** the stated limit (a directory listing whose total is over the
     size budget, a timing over the deadline, a count over the cap), your
     current solution has **failed** that criterion regardless of how cleanly it
     ran. Treat that observed over-limit number as a failure signal, not noise
     to explain away — go back and fix the logic, then re-check.
   - The only valid confirmation is to reproduce the graded scenario **from a
     clean state** and read the *peak* (or final) observed value yourself, then
     compare it against the stated limit in the same message. Re-reading your
     own source code, or grepping it for the presence of the right keywords, is
     NOT a measurement and does not confirm anything.
"""


class ConstraintVerifyProcessor(CustomSelfVerifyProcessor):
    """Stock self-verify checklist + lifecycle item (6) + constraint item (7).

    Reuses all of the parent's one-shot machinery (fires at most once, on the
    first no-tool-call exit attempt); only the injected checklist text is
    extended. Supersedes ``LifecycleSelfVerifyProcessor`` by keeping its item
    (6) verbatim and adding a measurement-reconciliation item (7).
    """

    _singleton_group = "tb2_self_verify"
    _order = 90

    def _verify_message(self) -> str:
        # Read the parent's message lazily so we stay in sync if it changes.
        from benchmarks.terminal_bench_2 import harness as _h

        return _h._SELF_VERIFY_MSG + _LIFECYCLE_ADDENDUM

    async def on_after_model(self, event):
        # Delegate to the parent to preserve exit-detection semantics, then
        # upgrade the pending message to the constraint-aware variant.
        async for out in super().on_after_model(event):
            if self._pending_message:
                self._pending_message = self._verify_message()
            yield out
