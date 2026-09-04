# Candidates — R2 / c4

Assigned focus: `task_000140_01c78b42` (system_administration) fails
`test_no_lingering_service_processes`.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a non-destructive, one-shot exit-gate processor (`LingeringProcessGuard`)
that, on an exit turn, when the session started background processes AND the
agent's most recent shell command was itself a background launch, injects one
focused nudge to inspect the *live* process table (`ps -ef | grep ...`),
enumerate every matching PID (including duplicates from repeated test runs),
and reconcile it against the task's required end state — reap all lingering
PIDs for cleanup/graceful-shutdown tasks, or confirm still-alive for
keep-daemon-up tasks — before finishing.

- Tasks affected:
  - primary (sharp instance): `task_000140_01c78b42` —
    `test_no_lingering_service_processes` fails with
    `Lingering vm_service processes found: ['332','558','770','971']`.
  - adjacent supporting (same "runtime process invariant, not the final
    snapshot" class, different failure shape / exit reason):
    `task_000118_3043e92d` — `test_deployment_monitor` measures *peak* log-dir
    size while worker processes run; agent verified a settled snapshot.
  - Also structurally documented in `tb2-playbook` as a recurring class
    ("Background process dies after agent exits" / persists only if the
    agent's final Bash call does not kill it) — this guard covers the
    inverse (agent's final Bash call *starts* a process and leaves it).
- Signal: `result.json` `agent.finished=no_tool_calls`, `steps=12`,
  `final_pytest.passed=false` on `test_no_lingering_service_processes`.
- Verified (Read of `task_000140_01c78b42.messages.json`):
  - step ~7: agent writes `test_pipeline.sh` whose shutdown is
    `PID=$(cat /home/user/service.pid); kill -TERM $PID` — kills only the
    *last* saved PID.
  - step ~9: `bash /home/user/test_pipeline.sh` → starts `vm_service &`,
    overwrites `service.pid`, kills that one PID.
  - step ~10: self-verify (`_tb2_self_verify`) fires; agent re-reads task,
    then re-runs the workflow.
  - step ~11: "cleanup" `pkill -f vm_service` returns **exit 143** (itself
    interrupted) and is never re-checked.
  - step ~11 (same turn): `bash /home/user/test_pipeline.sh` runs AGAIN →
    starts yet another `vm_service &`.
  - final turn: agent declares "The task is complete" and exits with
    `no_tool_calls` — leaving the freshly-started background process (plus
    the accumulated earlier ones) alive. The verifier finds 3–4 lingering
    PIDs. The agent's *last action before exit was a background launch* and
    it never inspected the live process table.
- Why Control not Instruction: the behavioral self-verify checklist
  (R1/c3) already contains advisory prose about killing leftover processes,
  and the agent *read it* — yet still re-ran its pipeline (spawning a new
  background process) as its final act. Prose alone does not close the gap
  because the unclean table is created on a *later* exit turn, after the
  checklist fired. A Control hook is needed to (a) mechanically detect that
  the agent's final act was a background launch and (b) re-open the exit with
  a targeted process-table reconciliation prompt. This is a mechanical
  exit-gate timing problem, not a knowledge gap.
- Why Control not a destructive auto-kill: which process should linger vs.
  be reaped is task-dependent (some tasks require the daemon to stay alive
  for the verifier — see `tb2-playbook`). Auto-killing would regress
  keep-daemon-up tasks. The guard therefore never kills; it nudges and lets
  the agent decide from the task description.
- Retroactive check (A-corrective): yes — on `task_000140`, the guard would
  have fired on the final exit turn (background launch was the immediately
  preceding command). The nudge to `ps`-enumerate all `vm_service` PIDs and
  `pkill -f` the cleanup class would have emptied the process table before
  exit, flipping `test_no_lingering_service_processes` to pass. (The
  functional deliverable — `vm_setup.log` content — was already correct.)

### Pareto framing
- expected_global_gain: closes the "process-table end-state at exit" gap in
  `system_administration` — a documented TB2 class (lingering PIDs, daemon
  left in the wrong state). Directly flips `task_000140`; the mechanism
  generalizes to any lifecycle/service/cleanup task graded on the final
  process table.
- regression_risk: very low. Non-destructive (no auto-kill); one-shot;
  narrowly triggered (only when a background process was started AND the
  agent's most recent shell command was a background launch). On clean
  exits, on exits not preceded by a background launch, and on tasks with no
  background processes, it is silent. Worst case: one extra model turn (a
  `ps`/reconcile round) on a task whose final act happened to be a legit
  background launch — which is exactly the class where reconciliation is
  cheap insurance, and for keep-daemon-up tasks the nudge asks only to
  *confirm* the process is alive.
- cost_shift: negligible aggregate; +~1 model turn only on the subset of
  exits whose immediately-preceding command was a background launch.
- rollback_trigger: if R3 shows pass_rate flat/down AND new T→F regressions
  on tasks that previously exited cleanly with an intentional live daemon,
  revert the processor.
