# Candidates

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add a general "final-state hygiene" rule to the system prompt: the
verifier inspects the post-exit container state, so the agent must
not leave background processes/services it spawned during testing
lingering, and must terminate ALL matching instances (not a single
stored PID) when a service is meant to be stopped.

- Tasks affected: task_000140_01c78b42 (assigned focus). Mechanism
  is generalizable to any service-lifecycle task in the weak
  `system_administration` domain (1/5 passing), but only one task
  in this round exhibits the exact "lingering self-spawned process"
  mechanism — see regression_risk / global-gain notes below.
- Signal: `final_pytest.output_tail` on task_000140 —
  `test_no_lingering_service_processes` fails with
  `AssertionError: Lingering vm_service processes found:
  ['344', '595', '801']`. The functional tests pass (log written
  correctly). `agent.finished = no_tool_calls`, `exit_reason=done`,
  reward 0 — a pure final-state-hygiene failure, not a logic error.
- Verified (Read of task_000140 messages.json):
  - The agent wrote a pipeline whose ONLY cleanup is
    `PID=$(cat /home/user/service.pid); kill -TERM $PID` — kills a
    single stored PID.
  - Step "bash test_pipeline.sh": the agent ran the start/stop
    pipeline itself to self-verify; each build+background-launch
    produces a new service instance, and the single-PID kill leaves
    earlier/other instances alive.
  - Final assistant message declares "done" after checking the log
    file contents only — it never verified the process table was
    clean (no `pgrep`/`pkill` sweep before exit).
- Why Instruction not Control: a Control processor could try to
  `pkill` the agent's background jobs on finish, but the playbook
  documents that SOME tasks REQUIRE a `&`/`nohup` service to stay
  alive into the verifier phase. A blind kill-on-exit hook would
  regress those tasks (high collateral). The decision "should this
  service survive at exit?" is task-dependent and must stay
  agent-side; the correct lever is guidance, not a mechanical
  kill. Instruction lets the agent distinguish "my test spawned
  extras — clean up" from "the graded service must persist".
- Why Instruction not Configuration: no existing processor knob
  governs post-exit process hygiene; this is a missing behavioral
  rule, not a mis-tuned threshold.
- Retroactive check (A-corrective): yes — if the agent had been
  told the verifier inspects the final process table and that a
  single-PID kill is insufficient, at its decisive final step it
  would have run a name-based process sweep (e.g. pgrep/pkill) and
  removed the lingering instances before declaring done, flipping
  `test_no_lingering_service_processes` to pass while the already-
  passing functional tests stay green.
- expected_global_gain: Flips task_000140; more broadly reduces
  final-state-hygiene failures across the weak `system_administration`
  cluster (service/daemon lifecycle tasks) where self-spawned test
  processes or incomplete teardown silently fail state checks.
- regression_risk: Prompt grows ~15 lines. Risk that the agent
  over-applies teardown and kills a service the task wants left
  running — mitigated by the rule's explicit "unless the task
  requires the service to stay running for grading" clause and
  "decide deliberately whether the task wants the service alive at
  exit". Low risk to non-service tasks (rule is scoped to
  background-process situations). No processor/tool code added, so
  no new crash surface.
- cost_shift: +~200 prompt tokens per task (one-time system-prompt
  growth). May add 1-2 verification Bash calls on service tasks;
  negligible net cost, plausibly saves a wasted round on hygiene
  failures.
