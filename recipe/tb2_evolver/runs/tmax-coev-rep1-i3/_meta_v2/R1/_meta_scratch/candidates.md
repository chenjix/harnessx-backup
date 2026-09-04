# Candidates — R1 (tmax-coev-rep1-i3)

Baseline R0: 27/50 (54%). Weakest clusters by domain:
system_administration 0/5, software_engineering 2/5, data_querying 2/6.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the text-only `ProcessLifecycleSelfVerifyProcessor` with a
`LifecycleSnapshotSelfVerifyProcessor` that, on the agent's first exit-intent
turn, injects a **real `Bash` diagnostic tool call** surfacing the live
process table + every `*.pid`/`pid.txt` file's contents, then follows with a
reconciliation checklist — so the model reconciles ground-truth output it did
not author, not its own narration.

- Tasks affected: task_000140_01c78b42, task_001090_c61c71f2 (both
  system_administration; same mechanism). Related shape also present in
  task_000118_3043e92d (monitor/deploy lifecycle) — expected side-benefit,
  not counted as core evidence.
- Signal: `final_pytest` failures are lifecycle-shaped, not logic-shaped.
  - task_000140: `AssertionError: Lingering vm_service processes found:
    ['343', '606', '810']` — `test_no_lingering_service_processes` fails; the
    two logic tests pass.
  - task_001090: `pid.txt does not contain a valid integer PID ... '576\n578\n641'`
    — `test_pid_file_and_process` fails; the other 5 tests pass.
- Verified (Read of messages.json):
  - task_000140: the R0 self-verify DID fire (msg 25 "Verification check
    initiated", msg 26 the agent re-reads the task and self-reports every
    item ✓), and at msg 23 the agent narrates "Service stopped" — yet the
    verifier finds lingering `vm_service` PIDs. The agent never ran
    `pgrep -f vm_service` to confirm; it trusted its own summary.
  - task_001090: msg 49 the agent explicitly says "I need to make sure the PID
    is the actual monitor process, not a wrapper" and msg 52's `ps aux` shows
    `bash -lc "... nohup ./monitor ... & echo $! > pid.txt"` (PID 576, wrapper)
    plus `./monitor` (PID 578). msg 53 concludes "PID 578 is the actual monitor
    process" — but never re-reads `pid.txt`, which still held `576\n578\n641`
    from repeated launches. The checklist clause about `echo $!`/wrapper PIDs
    was present and ignored.
- Why Control not Instruction: the corrective Instruction (the R0
  lifecycle checklist clause) already exists verbatim and both tasks read it
  and self-reported compliance without executing the check. Adding more prompt
  text repeats a lever that demonstrably failed on this exact cluster. The gap
  is not knowledge — the agent knows to reconcile — it is that nothing forces
  the *actual* state into context. A Control hook that injects a real Bash
  snapshot puts raw, un-narratable ground truth (`pid.txt = 576\n578\n641`,
  lingering `vm_service`) in front of the model before it commits to exit.
- Why Control not Action: no new capability is needed — `Bash` already exists;
  the snapshot is just a `Bash` invocation the harness schedules at the right
  moment. The lever is *when/what to run automatically*, which is Control.
- Retroactive check (A-corrective): yes. If the raw snapshot had been in
  context at the decisive exit step, task_000140 would have seen the lingering
  `vm_service` PIDs (prompting a kill) and task_001090 would have seen
  `pid.txt` containing three lines (prompting a rewrite to the single monitor
  PID) — both are exactly the assertions the verifier failed on, and both are
  one Bash command away from being fixed.
- expected_global_gain: flips the recurring lifecycle sub-cluster in
  system_administration (currently 0/5); mechanism is domain-agnostic
  (single-instance services, correct PID recording, no test leftovers) so it
  generalizes to any background-service task in software_engineering /
  scientific_computing that records a PID or must clean up.
- regression_risk: low. Fires at most once per task, only on exit intent, and
  only appends a diagnostic + one user message — no logic-task behavior
  changes. It replaces (same singleton group `tb2_self_verify`) rather than
  augments, so no double keepalive. Worst case on a pure-logic task: one extra
  harmless Bash round-trip and a checklist the agent was already getting.
- cost_shift: +1 Bash tool round-trip and ~1 short model turn per run that
  reaches exit intent (essentially all runs). Marginal; the snapshot output is
  bounded (`head -n 60`, `head -n 20` pid files).
