# Candidates — R2/c3 (assigned focus: task_000140_01c78b42)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a one-shot, exit-time Control hook that, on tasks where the agent stood up a
BACKGROUNDED service, injects one REAL `Bash` snapshot of live processes /
listening sockets / jobs and then a single reconcile-against-required-end-state
instruction — so the ground-truth final process state lands in context instead
of a text reminder the model skims past.

- Tasks affected: task_000140_01c78b42 (primary); generalizes to the TB2
  "final container process/resource state" verifier family, e.g.
  task_001090_c61c71f2 (final process identity `bash` vs `monitor`), and any
  service task whose verifier greps for lingering/misnamed service processes.
- Signal: `final_pytest` fails `test_no_lingering_service_processes` with
  `Lingering vm_service processes found: ['349','609','810']` (MULTIPLE PIDs);
  `agent.exit_reason=done`, `finished=no_tool_calls`, only 13 steps — the agent
  believed it was done. The stock `_tb2_self_verify` fired (msg 20) but the
  agent's follow-up (msg 22) re-checked only files (`ls -lh …`) and NEVER ran
  `pgrep`/`ps`.
- Verified (Read task_000140_01c78b42.messages.json):
  - msg 0 (prompt): "gracefully stop the Go service … reading `/home/user/service.pid`
    and sending a SIGTERM kill signal" — required END state is service STOPPED.
  - msg 10 (Bash writing test_pipeline.sh): pipeline does `./vm_service &` then
    `kill -TERM $PID` on the single recorded PID — racy single-PID kill, misses
    copies from repeated pipeline runs.
  - msg 14: `bash /home/user/test_pipeline.sh` run once (spawns a service).
  - msg 20: `_tb2_self_verify` fired; msg 22: re-checked only files via `ls -lh`;
    msg 28: declared done. No `pgrep`/`ps` anywhere in the trajectory.
  - result.json final_pytest tail: two verifier runs, PIDs `['349','609']` then
    `['349','609','810']` — the verifier re-runs the pipeline, so single-PID kill
    is structurally insufficient; the agent never reconciled the process state.
- Why Control not Instruction: the missing thing is not knowledge phrased as a
  prompt rule (the R2 sibling `h_final_state_hygiene_v1` already tries a passive
  text reminder appended to the self-verify ack) — it is *ground-truth process
  state the agent never observed*. A text reminder relies on instruction
  adherence the model demonstrably lacks here (it skimmed past the existing
  self-verify item). A Control hook that mechanically EXECUTES `ps`/`ss`/`jobs`
  puts the actual lingering-PID list into context as a tool result the model
  cannot skim past — the escalation the R2 journal itself flagged as "next" if
  the reminder alone fails. This is the caller-side observation gap fixed at the
  loop, not a per-call tool (Bash already exists; the gap is that the agent
  never reaches for it at exit).
- Why not Action: Bash already gives the agent the capability; the gap is that
  the agent never invokes it at the exit decision point. A new tool would add
  nothing the injected Bash snapshot doesn't already deliver.
- Retroactive check (A-corrective): yes — had the live `pgrep`/`ps`/`ss`
  snapshot been in context at msg 20/22, the lingering `vm_service` PIDs would
  have been staring the agent in the face; the reconcile instruction directs a
  pattern-kill (`pkill -f`) + re-confirm, which produces the required
  no-lingering-process end state. The kill is by pattern (not single PID), which
  is exactly the fix for the racy multi-PID failure.
- expected_global_gain: Flips the TB2 final-process-state cluster
  (task_000140 lingering vm_service; task_001090 wrong final process identity)
  — a verifier axis the file/format self-verify checklist never touches.
  Generalizes to any service task requiring a specific stopped/running end state.
- regression_risk: Low. Fires at most once per task, only after a
  backgrounded-service Bash signal, and runs LAST (order 96) so it defers to
  self-verify (90) and svc-deps (91). It NEVER kills anything itself — it only
  surfaces state and reconciles — so it cannot regress a task that wants a
  service left running (the KEEP branch explicitly says "confirm it stays up").
  Worst case on a false positive: one read-only snapshot + one reminder message,
  then a free exit. No control-flow change, no message injected on non-service
  tasks.
- cost_shift: +1 real Bash round-trip (read-only snapshot) and +1 short user
  message on service tasks that reach exit without having audited processes;
  +0 on non-service tasks. Plus any pattern-kill/re-snapshot the agent then runs
  (1-3 short calls) on tasks that genuinely had lingering processes. Net positive
  versus a wasted 0-reward task; negligible aggregate.
