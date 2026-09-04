# Candidates — Round 1 (c4)

Assigned focus: `task_000140_01c78b42` (system_administration) fails.

## Diagnosis

`task_000140` final pytest failure:
`test_no_lingering_service_processes` — `AssertionError: Lingering vm_service
processes found: ['337', '596']` (and `['337','596','804']` on the second
verifier assertion). The agent's solution was otherwise functionally correct
(main.go, start_service.sh, test_pipeline.sh all fixed; `vm_setup.log` written
with the right content). The task's own `test_pipeline.sh` does
`kill -TERM $(cat service.pid)`, but:

- The agent ran `bash /home/user/test_pipeline.sh` **during its own session**
  to self-verify. That started a `vm_service` background process.
- `start_service.sh` overwrites `service.pid` on every run and backgrounds a
  fresh instance, so re-running orphans the previous instance while only the
  last PID is recorded — a stray copy survives.
- The container **stays alive for an external verifier phase** (TB2 structural
  fact, playbook "Background process dies after agent exits"). The agent's
  leftover test process is still visible when the verifier's
  `pgrep -f vm_service` runs → assertion fails.

The existing `CustomSelfVerifyProcessor` checklist even says (step 5) "For
running services: confirm they are still alive" — which is **one-sided** and
actively wrong for tasks whose required final state is *no lingering process*.

## Cluster evidence (systemic, not one-off)

`by_domain.system_administration = 0/5 passed` — worst domain. Background /
detached process lifecycle is central to 3 of the 5 sysadmin failures:

- `task_000140_01c78b42`: leftover `vm_service` from agent's own pipeline test.
- `task_001090_c61c71f2`: PID-file + long-lived background `monitor` process
  (test `test_pid_file_and_process` reads pid.txt, `os.kill(pid,0)`, checks
  `/proc/<pid>/comm`) — process lifecycle is the graded axis.
- `task_000118_3043e92d`: verifier spawns a background `monitor` + `deploy`
  and inspects live behaviour; process management under load is central.

The shared mechanism: agents spawn background processes (`&`, `nohup`,
`Popen`, `systemctl start` …) and do not reconcile the FINAL process state
with what the task requires before finishing. This is exactly the structural
gap the playbook flags and cannot be inferred from a single trajectory alone.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `BackgroundProcessReconcileProcessor`: a one-shot, exit-intent Control
hook that — only when the session actually spawned a background process —
reminds the agent that background processes survive into the verifier phase
and must be reconciled with the task's required final state (kept alive OR
cleaned up, symmetric; no task-specific literals).

- Tasks affected: task_000140_01c78b42 (target), task_001090_c61c71f2,
  task_000118_3043e92d (same mechanism: background-process lifecycle graded by
  the verifier).
- Signal: `final_pytest` on task_000140 =
  `AssertionError: Lingering vm_service processes found: ['337','596']`;
  agent's messages show it ran `bash /home/user/test_pipeline.sh` itself
  (step: tool_call `bash /home/user/test_pipeline.sh`) then exited via
  `_tb2_self_verify` without any `ps`/`pgrep` cleanup check. Domain-level:
  `system_administration 0/5`.
- Verified (Read of task_000140.messages.json):
  - Assistant step "Now let me run the test pipeline to verify everything
    works correctly" → `Bash: bash /home/user/test_pipeline.sh` — starts a
    `vm_service` background instance.
  - Final assistant turn calls `_tb2_self_verify`, then re-reads the task and
    lists files with `ls -lh` — but never runs `ps`/`pgrep` and never confirms
    the service processes are gone. It exits with a leftover process → verifier
    `pgrep -f vm_service` finds `['337','596']`.
- Why Control not Instruction: the gap is a *mechanical exit-time guard that
  must fire uniformly* — the existing self-verify checklist is one-sided
  (tells the agent to keep services alive) and lives in a fixed string; a
  static prompt rule cannot be conditional on "did this session actually
  spawn a background process" and would nag every task. A Control hook fires
  the reminder only when backgrounding was detected, at the precise exit
  moment, mirroring the proven `CustomSelfVerifyProcessor` keepalive pattern.
- Why Control not Action: no new agent capability is missing — `Bash` can
  already `ps`/`pgrep`/`kill`; the agent simply doesn't reconcile state at
  exit. Nothing to add to the action space.
- Retroactive check (A-corrective): yes — had this reminder been present,
  task_000140's final turn (which already re-reads the task and runs `ls`)
  would have been prompted to `pgrep -f vm_service` and kill the stray test
  instance before finishing, flipping `test_no_lingering_service_processes`.
- expected_global_gain: flips the worst domain's cleanest failure
  (task_000140) and plausibly helps the two other background-process sysadmin
  tasks; generalizes to any TB2 task graded on final process state.
- regression_risk: low. Fires at most once per task, only when a background
  process was detected, and only injects an advisory reminder + one forced
  continuation turn. Symmetric wording (does not tell the agent to blanket-kill
  processes) so it will not break tasks that require a service to stay alive.
  Worst case: one extra model turn on affected tasks.
- cost_shift: +1 model turn (a few hundred tokens) only on tasks that spawned
  a background process and reached exit intent. Negligible on the majority of
  tasks that never background anything.
