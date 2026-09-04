# Candidates — R1 / c5 (focus: task_000118_3043e92d)

## Assigned-focus diagnosis (task_000118_3043e92d)

Task: write `/home/user/deployment_monitor.py`, a daemon that SIGSTOPs 20
`worker_sim.py` workers + truncates `/home/user/logs/*.log` when total size
> 40 MB, then SIGCONTs, exiting when no workers remain. Verifier fails: peak
log dir size = 209 MB >> 45 MB threshold (reward=0, exit=done @ 48 steps).

Root cause verified in body: at step 14 the agent's own test printed
`Monitor PID: 91 / All worker processes have completed. Exiting. / Starting
deployment...` — the monitor's `if not worker_pids: break` sits at the TOP of
the loop, so when started *before* the deployment (exactly how the verifier
runs it: start monitor, sleep 0.5s, start deploy) it exits immediately, never
monitors, and all 20 logs reach 10 MB = 200 MB+. The agent **correctly
diagnosed this in natural language** (steps 37/39/79/95: "the monitor exits
immediately when no workers are found ... the deployment hasn't started yet")
but never implemented the fix (a startup grace / wait-for-workers phase).

**Honest verdict on the focus task:** task_000118's terminal blocker is a
MODEL CAPABILITY GAP — the agent could not translate its own correct diagnosis
into a working daemon (startup grace period + tighter threshold margin). No
harness mechanism writes that logic for it; embedding it would be task-specific
knowledge injection (forbidden). So the focus task is **not directly harness-
fixable**. Per the brief, I make the smallest defensible edit that closes a
*systemic* harness deficiency the failure exposed, rather than drifting.

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Wire the existing (currently-unwired) `LoopDetectionProcessor` in **warn-only
escalating** mode: `warn_threshold=4`, `threshold=30`, `name_warn_threshold=999`
(Strategy 2 off), `window_size=50` (> threshold so the consecutive-run tail can
reach 30). Purpose: reclaim wasted budget from pathological, NON-recovering
byte-identical tool-call loops via a clean `loop_detected` exit, while proving
safe against loops that recover.

- Tasks affected (budget/loop-waste cluster, all currently reward=0):
  task_000313_1dce9844 (33 identical consecutive tool calls, budget_exceeded @80),
  task_001098_f5acdd79 (33 identical, exit=error @42),
  task_001979_a1e24b6f (42 identical, exit=error @44),
  task_001032_1adaccb9 (13 identical, budget_exceeded @80),
  task_000118_3043e92d (6 identical rewrites of deployment_monitor.py, plus
  repeated same-file writes; the loop is the *symptom* of the capability gap).
- Signal: computed max-consecutive-identical-tool-call run per trajectory —
  6/13/33/33/42 on the tasks above; `exit_reason ∈ {budget_exceeded, error}`
  on the extreme cases; existing `CustomEditToolProcessor` fired an EditDetection
  WARN at task_000118 step 62 ("modified more than 7 times ... try a
  fundamentally different approach") and was ignored — warn-at-7 wasn't enough,
  but there is currently NO clean-exit escape for the extreme non-recoverers.
- Verified (Read):
  - task_000118 step 14 tool result: `Monitor PID: 91\nAll worker processes have
    completed. Exiting.\nStarting deployment...` then a 204812-KB logs listing —
    proves the monitor-exits-early root cause AND that the agent had a working
    reproduction it then looped on instead of fixing.
  - task_000118 steps 57,61,65,69,73,77 tool_input: byte-identical
    `cat > /home/user/deployment_monitor.py << 'EOF' ...` (6 in a row).
  - task_000313: 33 byte-identical consecutive tool calls, 0 truncation markers
    (pure loop, never recovers) → budget_exceeded.
  - task_000936_2a78f3ca (reward=1, the REGRESSION probe): 26 byte-identical
    `cd /home/user/pipeline && ls -la` at steps 2-52, then recovered at step 55
    and PASSED. threshold=30 leaves this untouched (26 < 30).
- Why Configuration not Control/Instruction: the mechanism already exists as a
  library processor (`LoopDetectionProcessor`), it is simply unwired; the only
  decision is *calibration*. A new Control processor would duplicate it; an
  Instruction rule cannot mechanically reclaim budget from a stuck loop.
- Why warn-only (threshold=30) not R1's raise-at-5: NEW evidence R1 lacked —
  task_000936 passed after 26 identical consecutive calls. Any raise threshold
  <= 26 (R1 used 5) would kill 000936 mid-recovery → a regression. threshold=30
  raises only on the extreme non-recoverers (33/42) with a 4-repeat safety
  margin; warn_threshold=4 nudges early so agents can self-break (as 000936 did).
- Retroactive check (A-corrective): PARTIAL. For the extreme cases
  (000313/001098/001979) the raise reclaims 30-70 wasted steps as a clean exit —
  but those tasks were already reward=0, so this is a **cost win, not a flip**.
  For task_000118 the warn/raise does NOT flip it (its blocker is the daemon
  logic capability gap; EditDetection already warned and was ignored). So the
  candidate's honest value is **budget/cost reclamation on the loop cluster with
  near-zero regression risk**, plus a possible marginal recovery nudge — NOT a
  guaranteed task_000118 flip. Predicted_affected is therefore scoped to the
  cost claim, and I do not over-predict a flip.
- expected_global_gain: reclaims wasted compute on the non-recovering identical-
  loop cluster (>=3 tasks with 33-42 repeats ending in budget_exceeded/error);
  clean `loop_detected` exit returns those steps to the round budget.
- regression_risk: a task that legitimately issues >=30 byte-identical calls in
  a row would be cut short. Only observed recover-after-loop case (task_000936)
  peaks at 26, safely under 30. Strategy 2 disabled to avoid Bash-only false
  positives.
- cost_shift: net NEGATIVE (lower) — extreme loops exit ~30-70 steps earlier;
  one processor adds negligible per-call hashing overhead.
- rollback_trigger: next-round pass_rate drops OR any `loop_detected` exit
  appears on a task that previously passed (esp. task_000936).
