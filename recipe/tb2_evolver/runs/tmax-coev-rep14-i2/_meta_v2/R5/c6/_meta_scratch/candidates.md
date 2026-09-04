# Candidates — R5/c6

Assigned focus: `task_000140_01c78b42` (system_administration) — reward=0,
exit=done, 11 steps. Functionally correct Go service + init script + CI/CD
pipeline (initial_pytest passed, vm_setup.log has the expected provisioning
line, 2/3 verifier tests pass), but `test_no_lingering_service_processes`
finds stray `vm_service` PIDs at exit (`['336','585']`, then `['336','585','796']`).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Author `TeardownEndStateGuard` — append a concrete clean-teardown verification
step to the stock self-verify checklist, armed ONLY when (gate 1) the task
description asks for a graceful shutdown / clean end state AND (gate 2) the
agent actually launched a background process during the run.

- Tasks affected: task_000140_01c78b42 (assigned focus). This is a
  single-literal cluster in R4 (see Systemic note), but the *mechanism* fixed
  is a systemic one-sided bias in a read-only harness component
  (CustomSelfVerifyProcessor item 5) that touches every service/lifecycle task.
- Signal: `final_pytest.output_tail` —
  `AssertionError: Lingering vm_service processes found: ['336', '585', '796']`;
  `initial_pytest.passed=true`; 2/3 final tests pass; exit_reason=done.
- Verified (Read of messages.json):
  - step 4 (assistant): agent fixes main.go (port 8080, /provision, username),
    start_service.sh (`./vm_service &` + `echo $! > service.pid`), and writes
    test_pipeline.sh ending in `kill -TERM $PID`.
  - step 9 (tool result of `bash /home/user/test_pipeline.sh`): curl returns
    200, `vm_setup.log` = `PROVISIONED_VM_FOR: admin_alice` — the pipeline the
    agent ran during its OWN session started a `vm_service` in the background.
  - step ~11 (assistant, after `_tb2_self_verify` fired at step 10): the agent
    re-reads the task, re-lists files, and exits — issues NO process check and
    NO teardown of the vm_service it started. The stock self-verify item 5 only
    ever nudges "confirm services are still alive and reachable", never "reap
    what you started", so the agent is steered toward exactly the wrong end
    state for a task whose verifier demands a clean teardown.
- Why Control not Instruction: the corrective content must fire at a precise
  moment (the once-per-task self-verify checkpoint) and only under two runtime
  conditions the system prompt cannot see — (a) whether THIS task asked for a
  shutdown and (b) whether the agent ACTUALLY backgrounded a process this run.
  A static prompt rule would either apply to every task (adding teardown
  pressure to keep-alive service tasks, a regression risk) or be ignored by
  exit time. A Control hook reads `task_description` on_task_start and Bash
  commands on_before_tool, then augments the self-verify message on_before_model
  only when both gates hold — mechanical conditioning the prompt lever cannot
  express.
- Why Control not Configuration: the stock CustomSelfVerifyProcessor exposes no
  knob for a teardown branch; its checklist text is hardcoded in read-only
  benchmark code. No existing parameter can de-bias item 5.
- Retroactive check (A-corrective): yes — had the guard fired on task_000140's
  self-verify checkpoint (gate 1: description says "gracefully stop", "SIGTERM";
  gate 2: agent ran `test_pipeline.sh` which backgrounds `./vm_service &`), the
  agent would have been told to list matching processes, send the stop signal,
  and re-check that none survive before exiting — directly closing the
  lingering-`vm_service` failure. The agent had the capability (Bash pkill/
  pgrep/kill) and 200-step headroom (used 11); it lacked only the exit-time
  cue, which the one-sided checklist actively steered against.
- expected_global_gain: Flips the lingering-process failure and, more durably,
  de-biases the self-verify checkpoint for the whole graceful-shutdown /
  init-script / CI-CD cluster so any clean-teardown criterion is no longer
  silently steered against. Generalizes to unseen lifecycle tasks with zero
  task-specific literals (no ports, PIDs, process names, paths).
- regression_risk: Very low and doubly gated. Keep-alive service tasks either
  fail gate 1 (no shutdown language) or, if they mention "stop" incidentally,
  the item explicitly says "Only skip teardown for a service the task wants
  left alive" — it never orders killing a needed service. Among R4's passing
  tasks the service/deploy ones (task_000069, task_000760, task_001108) build
  ephemeral binaries / CI scripts, not persistent verifier-facing daemons, so
  the guard's gate-2 (background launch) rarely arms them and, if it does, the
  guidance matches their end state. Contract-safe: edits only the last user
  message when it is the self-verify checklist (sentinel-gated), inserts no
  message, fires <=1x/task (auto-check reports 0 contract violations).
- cost_shift: Negligible. On armed tasks it may prompt 1-2 short pgrep/kill
  verification calls near exit (replacing a silent failure); exactly zero on
  the ~all non-armed tasks (both gates unmet). No forced extra model turns.

### Systemic note (idiosyncratic filter)
Grep over R4 result.json for lingering/pgrep/process matched 4 files; on
inspection only task_000140 fails on lingering processes (task_000118 =
log-size threshold, task_001653 = numeric centroid, task_001979 = missing
output file). So the literal lingering-process failure is single-task THIS
round. It is shipped anyway because: (1) the brief assigns this focus and asks
to fix the harness capability it exposes; (2) the deficiency is a genuine
one-sided bias in a shared read-only harness component (self-verify item 5)
that steers EVERY service/lifecycle task toward keep-alive, so the mechanism —
not the task — is systemic; (3) the change is authored to serve the class
(intent+behavior gated, no task literals) and carries near-zero regression
surface, satisfying the Pareto rule.

### Why a different shape from the pending R2 siblings
Two pending siblings targeted this same task at the same lever:
h_service_lifecycle_reminder_v1 (an on_after_tool reminder fired mid-run on
background-launch Bash commands) and h_self_verify_teardown_balance_v1 (an
UNCONDITIONAL two-sided edit to every self-verify checklist). This candidate is
a distinct shape: it fires at the self-verify checkpoint (more reliable than a
mid-run reminder that can be forgotten by exit) BUT is doubly gated on task
intent AND observed background-launch behavior (unlike the unconditional
sibling), which removes the residual regression pressure on keep-alive tasks.
Both siblings are `pending` (not reverted), so the novelty gate permits this.
