# Candidates — Round 1 (c3)

Assigned focus: `task_000118_3043e92d` (system_administration) fails.

## Diagnosis of task_000118

The task: write a daemon that keeps `/home/user/logs/` under a size limit
while 20 workers each write 10 MB. The grader starts the monitor, runs the
deployment, and asserts **peak** log-dir size ≤ 45 MB during the run.

The agent's monitor was plausible but it declared success on a **hollow
verification**: it checked `du -sh /home/user/logs/` = `4.0K` *after* the
workers had exited and concluded "monitor works". Final size is always small
(workers truncate/exit); it says nothing about the **peak**. The grader's
independent clean-state run peaked at **209,715,200 bytes (= full 200 MB,
uncontrolled)** — the monitor effectively did nothing during grading. The
agent also tested from a dirty state (leftover/defunct processes, workers
already gone) rather than reproducing the grader's clean start.

## The cluster (not a one-off)

`system_administration` is the worst domain: **1/5 pass**. All four failures
share the same mechanism — agent exits `done`/`no_tool_calls` after a
self-satisfied snapshot that does not match the graded runtime invariant:

- `task_000118_3043e92d` — checked final dir size (4K), never the peak; peak
  hit 200 MB in grading.
- `task_000140_01c78b42` — verifier: `Lingering vm_service processes found:
  ['344','595','801']`; agent never verified clean process teardown.
- `task_000010_644ab1c2` — verifier: `/home/user/operator.py does not exist`;
  agent declared done without confirming the required output artifact exists.
- `task_000028_7fe033ac` — verifier import error (`No module named
  'requests'`) — **structural** (verifier-phase dependency), not fixable by
  the harness. Skipped.

Fix one (verification discipline for behavioral/stateful tasks) and 118/140
follow; 010 is the same "confirm the real artifact/invariant, not a
convenient proxy" failure the enhanced checklist also nudges.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Extend the existing one-shot TB2 self-verify exit gate with a step that
forces behavioral/dynamic tasks to reproduce the grader's clean-start run and
check the actual runtime invariant (peak resource use, no lingering
processes, service still responding) rather than a leftover snapshot.

- Tasks affected: `task_000118_3043e92d`, `task_000140_01c78b42`
  (secondary: `task_000010_644ab1c2`).
- Signal: `agent.finished=no_tool_calls`, `exit_reason=done`, `reward=0`;
  final_pytest asserts a runtime invariant (peak size / no lingering PIDs)
  that the agent's own in-session checks never exercised.
- Verified (body):
  - task_000118 assistant turns quote `du -sh /home/user/logs/` → `4.0K` and
    conclude "monitor is working correctly … well under the 40 MB threshold";
    the graded `final_pytest` shows `Peak log directory size was 209715200
    bytes … assert 209715200 <= 45000000`. The agent measured final, grader
    measured peak.
  - task_000140 `final_pytest`: `AssertionError: Lingering vm_service
    processes found: ['344','595','801']` after the agent exited `done` — no
    teardown verification was performed.
- Why Control not Instruction: the reliable, benchmark-proven mechanism is the
  existing `CustomSelfVerifyProcessor` one-shot exit gate (it already fired in
  task_000118's trajectory as `_tb2_self_verify`). Enhancing that hook keeps
  the nudge fired *at exit intent* for every task and reuses a contract-safe
  +1-message injection. A system-prompt rule would sit far from the decisive
  exit moment and compete with the 306-char sibling prompt; it also can't
  guarantee it lands right when the model tries to stop. This is not a missing
  capability (the agent has Bash and can reproduce the run), so not Action.
- Retroactive check (A-corrective): yes — had the checklist told the agent to
  reset to a clean state, re-run the deployment, and sample **peak** size
  during the run, task_000118's monitor bug (too-slow/ineffective loop) would
  have surfaced before exit; task_000140's lingering-process assertion would
  have been caught by the "no required process still lingering" check.

- expected_global_gain: targets the worst domain (system_administration 1/5).
  Plausibly flips 118 and 140; nudges 010. Generalizes to any daemon/service/
  monitor/cleanup task where the grader measures a runtime invariant.
- regression_risk: low. Same singleton group + `_order=90` as the stock
  processor, so it replaces (not duplicates) the exit gate — no double-fire.
  It only adds text to a message that already fires once at exit; it does not
  change control flow, tool schemas, or compaction. Worst case: one extra
  verification turn on tasks that don't need it (already the stock behavior).
- cost_shift: small positive — the injected message is ~15 lines longer than
  stock, and behavioral tasks may spend one or two extra Bash turns
  re-running their workload. Bounded by the existing one-shot design (fires at
  most once per task).

## Why not a smaller edit

The stock checklist already covers "file exists" and "service still alive"
but has no notion of *peak/transient* invariants or *clean-state
reproduction* — the exact gap that sank 118. Tuning an existing knob cannot
express that; a new/edited hook message is the minimal correct lever.
