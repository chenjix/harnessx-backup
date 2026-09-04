# Candidates — R1 / c4

Assigned focus: `task_000140_01c78b42` (system_administration) fails.

## Diagnosis

The verifier's `test_no_lingering_service_processes` asserts that **no
`vm_service` processes remain running** after the agent exits. The final
pytest shows lingering PIDs `['347','572','849','1061']`.

Root cause (from the trajectory body): the agent iterated on
`test_pipeline.sh` and **ran it multiple times to verify it** (steps ~10 and
~14; `vm_setup.log` ends with the provisioning line written *twice*). Each
run of `start_service.sh` launched a fresh `./vm_service &` background
process, but the pipeline's shutdown only killed the *single* PID recorded
in `service.pid` (the last one). Earlier instances were orphaned. The
agent's own edit/re-test loop accumulated duplicate background services it
had no visibility into — each Bash call is stateless from its perspective.

This is a **harness deficiency**, not a model capability gap: the agent
solved the actual task (main.go / start_service.sh / test_pipeline.sh were
all correct) but was sunk by the cumulative side effects of its own
iterative testing, which it could not observe.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `LingeringProcessGuard` MultiHookProcessor that, at exit intent, queries
the live sandbox for running processes and — only if the agent launched
background work during the run — injects a one-shot informational warning
listing any **duplicate instances of the same command** (the fingerprint of
re-run orphans), asking the agent to deliberately clean up before finishing.

- Tasks affected (corrective, primary): `task_000140_01c78b42`.
  Same mechanism is a live risk across the system_administration cluster
  whose verifiers inspect final process state (`task_001090_c61c71f2` also
  asserts on process state, though its specific bug — `comm=='bash'` vs
  `'monitor'` — is a distinct exec-vs-wrapper knowledge gap, so it is NOT
  claimed as a duplicate-orphan flip). The duplicate-orphan shape recurs
  wherever an agent re-runs a start/deploy/test script: background-launch
  counts are high across the batch (7 for task_000140; >=2 on ~36 of 50
  tasks), so accidental duplicates are a structural hazard, not a one-off.
- Signal: `final_pytest` assertion
  `AssertionError: Lingering vm_service processes found: ['347','572','849','1061']`.
  `agent.finished=no_tool_calls` (agent believed it was done). Trajectory
  shows two full pipeline runs (two `PROVISIONED_VM_FOR: admin_alice` lines).
- Verified (Read messages.json):
  - step ~10 (`chatcmpl-tool-bd6c9799`): agent runs `bash /home/user/test_pipeline.sh` → exit 127; `vm_setup.log` has ONE line.
  - step ~14 (`chatcmpl-tool-ac013bcf`): agent edits the pipeline and runs it AGAIN → `vm_setup.log` now has TWO lines → two service instances were started, only the last PID was killed.
  - final assistant turn: no tool calls, declares success; verifier then finds 4 lingering `vm_service` PIDs.
- Why Control not Instruction: the agent has correct *knowledge* (its
  pipeline does gracefully shut down "a" service). The gap is **runtime
  visibility** into cumulative side effects that no static prompt rule can
  supply — the set of orphaned PIDs only exists at runtime and depends on
  how many times the agent happened to re-test. A Control hook that reads
  the live `ps` table at exit is the only lever that can surface the actual
  duplicated PIDs. A prompt rule ("remember to kill old processes") would be
  ignorable and untargeted.
- Why Control not Action: the agent already has Bash (it can `kill`); it
  lacks the *trigger/awareness*, not the capability. No new action space is
  needed.
- Why informational, not a kill-guard: the existing system prompt explicitly
  tells agents to *keep* background services running after exit for tasks
  whose verifier connects to them. A destructive "kill all background jobs"
  hook would regress that whole cluster. The guard therefore fires ONLY on
  *duplicates* (>=2 instances of the same command signature) — almost never
  intentional — and leaves the decision to the agent, so single-service
  tasks are untouched.
- Retroactive check (A-corrective): yes — had this fired on task_000140, the
  agent would have seen `vm_service ×4 (PIDs 347,572,849,1061)` at its exit
  turn, prompting a `kill` of the extras. The verifier only requires zero
  lingering `vm_service`; the agent had the means and one more turn to do it.
- expected_global_gain: flips task_000140; protects any current/future task
  whose verifier checks "no lingering/duplicate service processes" from the
  same re-run-orphan failure. Generalizes to the system_administration
  cluster (0/5 in R0).
- regression_risk: LOW. Fires at most once per task, only when (a) the agent
  launched background work AND (b) duplicate command instances exist. Tasks
  with no background work, or a single clean service instance, get zero
  injected tokens and zero behavior change. It never kills anything, so
  "keep the service alive" tasks are safe. Unit-tested: single instance and
  two *distinct* services both yield no warning. The one-extra-turn keepalive
  reuses the same pattern as the shipped `CustomSelfVerifyProcessor` and is
  ordered (92) to run *after* it, so the two never contend for the exit turn.
- cost_shift: negligible. One extra sandbox `ps` call + at most one short
  message + one extra model turn, and only on the rare duplicate-orphan path.
