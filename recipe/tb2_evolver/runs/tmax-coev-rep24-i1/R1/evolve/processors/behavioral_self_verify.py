"""Behavioral self-verify processor for TB2 / tmax.

Extends the stock ``CustomSelfVerifyProcessor`` (which injects a one-shot
verification checklist when the model tries to exit) with an additional
step that targets *dynamic / behavioral* tasks: daemons, services, resource
monitors, and process-lifecycle tasks.

Root-cause motivation (harness deficiency, not domain knowledge):
On tmax `system_administration` tasks the agent repeatedly declares success
after checking a convenient *final* snapshot (e.g. an empty log dir, a
running process listing) that does NOT correspond to the invariant the
external verifier actually measures (peak resource usage, absence of
lingering processes, a service still answering at grade time). The agent
also tends to "verify" from a dirty state left over from its own earlier
tinkering rather than reproducing the grader's clean-start end-to-end run.

This is a mechanical verification-discipline gap that recurs across a whole
class of tasks, so it belongs in the self-verify hook, not in the system
prompt as task-specific knowledge. The added checklist step is phrased as a
general strategy ("reproduce the grader's setup from a clean state and check
the runtime invariant, not a leftover snapshot") and contains no task IDs,
paths, thresholds, or identifiers lifted from any trajectory.
"""

from __future__ import annotations

from benchmarks.terminal_bench_2.harness import CustomSelfVerifyProcessor


# General strategy guidance for dynamic / behavioral tasks. No task-specific
# literals — this must help an agent on a task it has never seen before.
_BEHAVIORAL_STEP = """\

6. **Dynamic / behavioral tasks (daemons, services, monitors, resource limits, process cleanup):**
   A convenient after-the-fact snapshot is NOT proof. The grader re-runs your
   solution from a clean state and measures the *runtime* invariant, not the
   state you happened to leave behind.
   - Reset to a clean starting state first (kill leftover background processes
     from your own earlier testing, clear any files your tinkering created),
     then reproduce the intended workflow end to end exactly as the task
     describes it.
   - Verify the invariant the task actually specifies, at the moment it matters
     — e.g. the *peak* resource usage during the run (not the final size after
     things settle), that no required process is still lingering after
     completion, or that a service is still responding right now.
   - If the task asks a process to stay under a limit "at any point", sample
     repeatedly *while the workload runs*; a low reading taken after everything
     has exited tells you nothing about the peak.
"""


def _augmented_message() -> str:
    """Insert the behavioral step before the final SUCCESS line of the base msg."""
    # Import here so we always track the current base message text.
    from benchmarks.terminal_bench_2 import harness as _h

    base = _h._SELF_VERIFY_MSG
    marker = "Fix anything that looks wrong before exiting."
    if marker in base:
        return base.replace(marker, _BEHAVIORAL_STEP + "\n" + marker, 1)
    # Fallback: append if the base message shape changed upstream.
    return base + _BEHAVIORAL_STEP


class BehavioralSelfVerifyProcessor(CustomSelfVerifyProcessor):
    """Same one-shot exit-gate mechanism, richer checklist for behavioral tasks."""

    # Keep the same singleton group so it still occupies the single
    # self-verify slot (never double-fires alongside the stock processor).
    _singleton_group = "tb2_self_verify"
    _order = 90

    async def on_after_model(self, event):  # type: ignore[override]
        # Delegate to the base logic (handles the exit-intent detection and the
        # keepalive tool-call injection), then swap the pending message for our
        # augmented version if the base decided to fire.
        async for out in super().on_after_model(event):
            if self._pending_message:
                self._pending_message = _augmented_message()
            yield out
