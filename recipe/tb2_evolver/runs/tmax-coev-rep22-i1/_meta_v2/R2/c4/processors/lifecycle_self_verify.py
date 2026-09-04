# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Process-lifecycle-aware self-verification nudge for TB2 service tasks.

Extends the stock ``CustomSelfVerifyProcessor`` one-shot exit checklist.

Motivation
----------
The stock checklist's "running services" item only nudges the agent to
confirm a service is *still alive*. A whole class of system-administration
tasks are graded on the *final runtime process state* in ways the stock
checklist never surfaces, and sometimes biases against:

  * teardown tasks — the verifier asserts **no lingering** service
    processes remain after a lifecycle/pipeline script runs (e.g.
    ``pgrep -f <svc>`` must be empty). "Confirm it is still alive" pushes
    the agent the wrong way.
  * identity tasks — the verifier reads a PID file and asserts the process
    named there is the compiled service binary, not a wrapper shell
    (``/proc/<pid>/comm`` must equal the service name, not ``bash``).
  * resource-bound tasks — background workers must stay within a size /
    resource budget rather than run unbounded.

These are harness-verification-nudge deficiencies, not model-knowledge
gaps: the agent already has ``Bash`` and can check ``pgrep`` /
``/proc/<pid>/comm`` / directory sizes; it simply is not prompted to
reconcile the *final* process state against the task's stated lifecycle.

The fix is a strictly additive checklist item describing the *general*
strategy ("reconcile final process state with the lifecycle the task
demands"), with no task-specific identifiers, constants, or paths. It
fires at most once per task, exactly like the stock processor, so it does
not inflate step count on tasks with no service component.

R2 refinement (cleanup branch only)
------------------------------------
A start-then-stop pipeline failure was observed where the agent's *code*
was correct but its *exit hygiene* was not: after the nudge fired it
"verified" by re-running its own start-then-stop pipeline several times.
Re-running such a pipeline **starts the service again**, and killing only
the single most-recently-saved PID leaves orphans from earlier runs alive,
so the verifier's ``pgrep`` was non-empty at exit. The cleanup branch now
names this trap in general terms: re-running a start-then-stop pipeline is
not a clean verification; the *final* action on a teardown task must be a
teardown whose success is confirmed by an empty ``pgrep`` — because killing
one saved PID can miss orphans. This is general strategy only (no task ids,
ports, paths, or binary names) and adds no new firing trigger — it is text
inside the branch that already fires for teardown tasks, so keep-alive
tasks (which read the unchanged "keep" branch) are untouched.
"""

from __future__ import annotations

from benchmarks.terminal_bench_2.harness import CustomSelfVerifyProcessor

# Additive block appended to the stock self-verify checklist. General
# strategy only — no task-specific identifiers, ports, paths, or constants.
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
     process is left behind — `pgrep -f <name>` must return nothing. Beware two
     traps: (a) re-running a start-then-stop pipeline is NOT a way to verify a
     clean shutdown — each run *starts the service again*, so your very last
     action must be a teardown you then confirm, not another pipeline run;
     (b) killing only one saved PID can miss orphans left by earlier runs or
     child processes. So after any teardown, run `pgrep -f <name>` as your final
     check; if it prints anything, kill every listed PID and re-check until it
     is empty.
   - If the task bounds a background workload (log size, disk, memory, run
     time): confirm the observed value stays within the stated limit rather
     than assuming the workers self-regulate.
"""


class LifecycleSelfVerifyProcessor(CustomSelfVerifyProcessor):
    """Stock self-verify checklist plus a process-lifecycle final-state item.

    Reuses all of the parent's one-shot machinery (fires at most once, on the
    first no-tool-call exit attempt); only the injected checklist text is
    extended.
    """

    _singleton_group = "tb2_self_verify"
    _order = 90

    def _lifecycle_message(self) -> str:
        # Read the parent's message lazily so we stay in sync if it changes.
        from benchmarks.terminal_bench_2 import harness as _h

        return _h._SELF_VERIFY_MSG + _LIFECYCLE_ADDENDUM

    async def on_after_model(self, event):
        # Delegate to the parent to preserve exit-detection semantics, then
        # upgrade the pending message to the lifecycle-aware variant.
        async for out in super().on_after_model(event):
            if self._pending_message:
                self._pending_message = self._lifecycle_message()
            yield out
