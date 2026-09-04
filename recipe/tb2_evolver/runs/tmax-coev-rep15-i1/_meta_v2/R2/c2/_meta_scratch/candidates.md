# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a one-shot exit-intent Control processor (`GradedArtifactRerunReminderProcessor`)
that, when the agent has authored an executable deliverable (a script / daemon /
monitor / service the task states the grader will *run / execute / start / test*),
nudges it — before finishing — to re-run that deliverable ONCE MORE from a clean,
fresh-process baseline (kill leftover background processes, reset the working
state) using the exact invocation contract the task describes, rather than trusting
a hand-rolled convenience test whose timing/ordering differs from the grader.

- Tasks affected: task_000118_3043e92d, task_000140_01c78b42
  (both `exit_reason=done`, `finished=no_tool_calls`, `reward=0`; both ship an
   executable artifact that an external grader restarts from scratch and both
   passed the agent's own ad-hoc test but failed the grader's clean re-run).
- Signal: `agent.exit_reason=done` + `finished=no_tool_calls` + `reward=0`;
  final_pytest shows a *behavioural* assertion on the artifact's re-execution
  (peak log-dir size, lingering process count) that the agent's own test never
  reproduced. The agent tested the artifact under a self-chosen invocation that
  differs from the grader's `Popen(monitor); sleep(0.5); Popen(deploy)` ordering.
- Verified (Read):
  - task_000118_3043e92d msg 33 (agent's final self-test): starts the monitor,
    `sleep 1`, THEN runs `run_deployment.sh` in the same shell — the monitor
    catches the workers only by import-lag luck. result.json final_pytest:
    `Peak log directory size was 209715200 bytes ... exceeds ... 45000000`
    (i.e. in the grader's clean `Popen+sleep(0.5)` ordering the monitor's
    "exit when no worker_sim.py found" loop fires on its FIRST iteration —
    before any worker exists — so it dies immediately and never truncates).
    The agent's own test "passed" (msg 34 shows 12 KB total) purely because of
    timing; it never re-ran the artifact under the grader's fresh-restart order.
  - task_000140_01c78b42 msgs 20-26: self-verify fired at msg 20; the agent's
    follow-up only re-checked output files (`ls`, `cat vm_setup.log`) and never
    re-ran the pipeline from a clean process baseline; result.json final_pytest:
    `Lingering vm_service processes found: ['338','591','790']` — the grader
    re-runs the pipeline and the racy single-PID `kill -TERM` misses copies.
    A clean fresh re-run + `pgrep` check would have surfaced the lingering
    processes the ad-hoc test masked.
- Why Control not Instruction: the miss is mechanical and timing-shaped, not a
  knowledge gap — the agent *did* test, but under the wrong invocation, and an
  always-on prompt rule fires on every task (including the ~30 that already pass
  with a cheap exit) and is easy to narrate past. A Control hook delivers the
  reminder exactly at the exit-intent turn, only on tasks whose own Bash activity
  shows an executable deliverable was authored, and bounds it to one fire — the
  same proven keepalive-tool-call exit-intercept path the existing
  `ServiceDepsReminderProcessor` uses. Instruction would also duplicate the
  general "double-confirm before exit" rule already in the prompt without adding
  the *clean re-run* discipline that is the actual gap.
- Why Control not Configuration: no existing knob expresses "re-run the graded
  artifact from a clean baseline at exit"; the stock `CustomSelfVerifyProcessor`
  checklist is existence/format-oriented and says nothing about re-execution
  ordering.
- Retroactive check (A-corrective): yes — if, at its exit turn, the agent had
  re-run the monitor from a fresh process (kill leftovers, empty logs) and then
  started the deployment the way the grader does, the first-iteration-exit bug
  would have been visible (logs hit 200 MB, monitor already dead) and the agent
  had budget (23/80 steps used) to fix the startup guard. For task_000140 a
  clean re-run + `pgrep vm_service` would have exposed the lingering processes.

expected_global_gain: Flips the "graded-artifact re-run mismatch" sub-cluster of
the large `done/reward=0` family — tasks that ship an executable deliverable
(daemon/monitor/service/pipeline script) an external grader restarts from
scratch, where the agent's ad-hoc self-test used a non-matching invocation.
Generalizes to any task whose success criterion is the artifact's *behaviour on
a clean re-execution*, not file contents. At least 2 tasks this round
(task_000118, task_000140) sit squarely on this axis; more of the done/reward=0
cluster plausibly share it.

regression_risk: Low. Fires at most once per task, only on tasks whose own Bash
activity shows an executable deliverable was created AND the task text uses
run/execute/start/test-the-artifact language; injects a single user message via
the proven keepalive path (contract-clean, +1 message per fire), never off task
ids. It always yields to a genuine exit on the next turn, so a finished run is
never trapped. Non-matching tasks (pure data-transform, one-shot answer) never
arm. Worst case on a false positive: one extra short re-run round-trip on a task
that would have passed anyway.

cost_shift: +1 short reminder message + typically +1-3 Bash re-run/verify
round-trips on armed tasks only; ~0 on the ~30 tasks that don't arm. Net-positive
when it converts a 0-reward run into a pass; negligible aggregate.
