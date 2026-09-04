# Candidates — R3

## Candidate C-003
[lens: failure | lever: control | intent: corrective]

Replace R2's ineffective "kill the parent PID" zombie-cleanup advice with
the correct general mechanism for the orphan/zombie-background-process
final-state failure: reap background test processes in the *same* shell via
`wait`, and never make a fresh service launch one of the agent's last
actions (run pipelines early, end with a `pgrep` clean-state check).

- Tasks affected (corrective, service/daemon class):
  - failing: task_000140_01c78b42 (still F after R2's cleanup nudge)
  - class-adjacent passing tasks the guidance must not regress:
    task_000028_7fe033ac, task_000958_4bb2b05d, task_001090_c61c71f2
- Signal: `final_pytest` on task_000140 →
  `test_no_lingering_service_processes` →
  `AssertionError: Lingering vm_service processes found: ['362','643','956','1219','1420']`.
  `exit_reason=done`, reward=0. This is a repeat of R2's predicted task; R2's
  checklist item #7 fired but did not flip it.
- Verified (Read of task_000140_01c78b42.messages.json):
  - Step (msg 256) `ps aux` output:
    `root 362 ... Z ... [vm_service] <defunct>` — the process is a **zombie**.
  - Step (msg 324) `ps -ef`:
    `root 362 1 0 04:55 ? 00:00:00 [vm_service] <defunct>` — **PPID=1 (init)**.
    The R2 advice "identify the parent PID and terminate that parent" is
    impossible here: the parent is init.
  - Steps (msg 336-354) the agent ran `kill -9 362` and `pkill -9 vm_service`
    (no-ops on a zombie), then **re-ran `test_pipeline.sh` again** (msg 458),
    spawning a fresh vm_service (PID 956) that became another zombie
    (msg 514 shows 362, 643, 956 all `<defunct>`). Cleanup was undone by the
    agent's own final relaunch.
  - Root cause: `./vm_service &` launched inside `start_service.sh` (a
    subshell) that exits → child reparented to init → `SIGTERM` leaves a
    `<defunct>` that `pgrep -f vm_service` matches. Not reapable by the agent.
- Why Control not Instruction: the fix lives in the existing one-shot
  self-verify Control processor (a mechanical guard that fires uniformly on
  every task's exit intent via a synthetic keepalive turn), not in the static
  system prompt. The remediation must be delivered at the decisive exit
  moment; a static prompt rule read at task start is far from that moment and
  R0/R1 evidence showed the checklist-at-exit mechanism is what actually
  reaches the agent. This is a text refinement of that existing Control
  component, not a new lever.
- Why not re-proposing the reverted R2 shape: R2's `h_lingering_process_cleanup_v1`
  was *accepted* (not reverted), but its mechanism ("kill the parent PID")
  is proven wrong by the new PPID=1 body evidence. C-003 is a distinct
  mechanism (prevention: reap-in-same-shell + ordering, not post-hoc kill),
  grounded in new evidence — a legitimate re-scope, not a re-proposal.
- Retroactive check (A-corrective): yes — had the agent (a) reaped its test
  service with `wait $SVC_PID` in the launching shell and (b) not re-run the
  pipeline as its last action but instead ended with a `pgrep -f vm_service`
  clean check, no `<defunct>` entry would exist at final state and
  `test_no_lingering_service_processes` would pass. The other two verifier
  tests (log content, script correctness) already passed.
- expected_global_gain: Flips task_000140 (the sole remaining recurring,
  structural, harness-fixable failure) and hardens the whole service/daemon
  cluster (system_administration is the weakest domain at 2/5) against the
  same trap on future unseen tasks, because the mechanism is generic to any
  "background process reparented to init" scenario.
- regression_risk: Low. Pure text refinement of an already-shipped Control
  item on the once-per-task self-verify turn; identical singleton group
  (`tb2_self_verify`), `_order=90`, and keepalive mechanism as R1/R2 — no
  double-injection risk. The item retains the explicit guard "do NOT do this
  if the task requires a service to stay running", so it will not make the
  agent kill a service the verifier needs alive (protects passing tasks
  000028, 000958, 001090). Worst case it is a no-op on non-service tasks.
- cost_shift: Negligible — item #7 grows by ~120 tokens on the single
  self-verify turn; at most 2-3 extra Bash calls (`pgrep`, one `wait`/`kill`,
  re-check) on tasks that spawned background processes.
- rollback_trigger: If task_000140 stays F AND any previously-passing
  service/daemon task (000028, 000958, 001090) flips to F — i.e. the reap/
  ordering nudge disturbed a keep-alive service — revert to the R1 checklist.
