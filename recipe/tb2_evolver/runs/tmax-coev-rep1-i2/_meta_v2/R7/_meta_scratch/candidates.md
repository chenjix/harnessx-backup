# Candidates — R7

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the stock `CustomSelfVerifyProcessor` with a subclass that appends a
general **process/service lifecycle reconciliation** step to the one-shot
exit-time verification checklist (which processes must still be running, which
must be stopped/not lingering, and that any recorded PID names the actual
target binary rather than a shell wrapper).

- Tasks affected: task_000140_01c78b42, task_001090_c61c71f2, task_000028_7fe033ac
  (all `system_administration`; the domain scored 0/5 in R6).
- Signal: `final_pytest` failure tails share a process-lifecycle root cause that
  the current checklist (files + "services still alive") does not cover:
  - task_000140: `test_no_lingering_service_processes` →
    `AssertionError: Lingering vm_service processes found: ['350','617']` —
    processes the task required cleaned up were left running.
  - task_001090: `test_pid_file_and_process` →
    `Process name is 'bash', expected 'monitor'` — recorded PID points at the
    shell wrapper, not the binary.
  - task_000028: `Failed to connect to Nginx on 127.0.0.1:8080: Connection
    refused` — a service that had to persist was not alive at verify time.
- Verified (Read, R6 trajectories):
  - task_001090 tool turn quotes the exact launch:
    `bash -lc cd /home/user/admin_tool && nohup ./monitor > /tmp/monitor.log 2>&1 & echo $! > .../pid.txt`
    then `ps` shows `777 ... bash -lc ... nohup ./monitor ... & echo $!` and
    `778 ... ./monitor` — `$!` captured the subshell (777=bash), so
    `pid.txt`=777 and `/proc/777/comm`=`bash`. The agent's own `SUCCESS`
    summary asserts "PID saved to pid.txt (PID 778)" — it never noticed the
    file held 777. Exit=done, self-verify keepalive present, still wrong.
  - task_000140 final assistant turns: agent built a `test_pipeline.sh` that
    "gracefully stops service with SIGTERM" for its *scripted* run, but its own
    interactive testing spawned `vm_service` provisioning processes (PIDs
    350/617) it never reaped; exit=done with the self-verify checklist fired
    (item 5 only asks about services that must stay up, never about leftovers).
  - task_000028 `final_pytest` tail = `Connection refused` on :8080; exit=
    budget_exceeded — but the failing assertion is a *dead service at verify
    time*, the same lifecycle axis (must-persist end).
- Why Control not Instruction: the exit-time reconciliation must fire
  **uniformly at the decisive no-tool-call exit turn** regardless of what the
  system prompt says — the mechanism already exists as a one-shot keepalive
  (`CustomSelfVerifyProcessor`) that reliably re-engages the model right before
  it commits. Putting the guidance only in the static system prompt (Instruction)
  would place it 20-80 steps upstream of the exit decision, where R6 shows the
  agent has already forgotten it (it re-ran the stock checklist and still
  exited wrong). Reusing the proven keepalive hook injects the reminder at
  exactly the step it is needed, with zero new mechanical surface (singleton
  group `tb2_self_verify` retained → no double keepalive; only the message
  string is extended). Not Action: no new capability is missing — `ps`,
  `pgrep`, `kill`, `/proc/<pid>/comm` are all reachable via the existing Bash
  tool; the gap is a verification step the agent skips, not an action it cannot
  take.
- Retroactive check (A-corrective): yes for 140 and 1090 —
  task_001090's decisive error is a one-line PID-capture idiom the reconciliation
  step names explicitly (`cat /proc/<pid>/comm`, "start the binary directly");
  had the agent run that check at exit it would have seen `comm=bash` and
  relaunched. task_000140's leftovers were still visible in `ps` at exit; a
  "kill everything the task says must be gone" prompt at the commit turn would
  have caught PIDs 350/617. Partial for 028 (exit=budget_exceeded, so it may
  not reach the exit hook; cited as the third instance of the must-persist end
  of the same axis, not a claimed certain flip).
- expected_global_gain: `system_administration` is the single 0/5 domain in R6;
  3 of its 5 failures share one harness-addressable lifecycle root cause. The
  reconciliation step generalizes to any process/service task in any domain
  (data_querying/software_engineering service builds also touch this axis), so
  the ceiling is broader than sysadmin.
- regression_risk: Low. The mechanism is byte-identical to the accepted stock
  processor except for an appended, additive checklist item; the singleton
  group is preserved so no second keepalive fires. Passing tasks that have no
  process/service lifecycle simply read one extra paragraph and answer "n/a"
  (the clause is explicitly conditioned on "the task's lifecycle requirements").
  The only downside surface is a slightly longer exit checklist prompt.
- cost_shift: Negligible. One extra paragraph (~600 chars) injected once per
  task at exit; no extra model turns beyond the already-existing one-shot
  keepalive. On tasks where the agent now cleans up leftovers or relaunches,
  a few extra Bash calls — bounded and only on the affected cluster.
