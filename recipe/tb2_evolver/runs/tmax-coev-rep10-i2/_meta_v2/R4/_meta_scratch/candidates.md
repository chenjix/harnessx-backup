# Candidates — R4

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a warn-only `WindowedRepeatBreakerProcessor` that catches interleaved
(non-consecutive) no-progress loops the existing consecutive-only
`LoopDetectionProcessor` structurally misses: warn when the same command
appears >=5 times within the last 8 tool calls, and when >=3 empty/no-op
commands are issued in a row.

- Tasks affected (failing, budget_exceeded, R3): task_000958_4bb2b05d,
  task_000118_3043e92d, task_002073_a89d5ed6 (primary catches);
  secondary support across the wider 7-task budget_exceeded cluster
  (task_000028_7fe033ac, task_000442_502566d6, task_000010_644ab1c2,
  task_000585_049544a3) where the same interleaved-repeat waste appears at
  lower intensity.
- Signal: `exit_reason=budget_exceeded` at the 80-step cap on 7/24 R3
  failures. Command-stream measurement (window=8): task_000958 top command
  repeated 16x with max 8-of-8 in a single window (first tripwire step ~9);
  task_000118 top command repeated 19x, max 8-of-8 (tripwire step ~6);
  task_002073 15 empty commands interleaved with the same verify command.
  The R2 `LoopDetectionProcessor` warn (warn_threshold=3, CONSECUTIVE run
  only) never fires on any of these because the repeats are interleaved with
  an inspection / empty command that breaks the consecutive tail count.
- Verified (Read of `.messages.json` command streams):
  - task_000958_4bb2b05d: the command
    `pkill -9 -f "./server"; sleep 1; cd /home/user && ./server &; sleep 2;
    curl -s http://127.0.0.1:9090/` is re-run 16x — a service restart+retest
    cycle that never changes the C++ source producing the failing result.
  - task_000118_3043e92d: the command
    `rm -f /home/user/logs/*; python3 /home/user/deployment_monitor.py &;
    sleep 1; ...test...` re-run 19x — a monitor start+test cycle, same
    failing result each time.
  - task_002073_a89d5ed6: last ~20 tool calls alternate between an empty
    command (`''`) and `# Final check ... echo "=== Script Verification ==="`
    (13+ repeats), a degenerate verify/empty oscillation to budget_exceeded.
- Why Control not Instruction: R2 (control, consecutive warn) and R3
  (instruction, loop-escape protocol) both already shipped and the agent
  DEMONSTRABLY already has the "I'm looping" awareness (R3 body: "I'm stuck
  in a loop, let me try a different approach" said 16-17x). The missing piece
  is a *mechanism-level* signal that fires on the interleaved shape the
  consecutive detector cannot see — a prompt rule cannot count windowed
  repeats across tool calls; only an `on_after_tool` hook can. This is a
  detector-coverage gap, not a knowledge gap.
- Why not Configuration (retune LoopDetectionProcessor): its counter is
  `_consecutive_tail` — strictly consecutive by construction. No knob makes
  it count windowed/interleaved repeats; lowering warn_threshold would fire
  more aggressively on consecutive runs but still miss every interleaved
  case here (all have consecutive-run <=3). A new detector is required, not a
  retune.
- Retroactive check (A-corrective): partial-yes. For task_000958/000118 the
  windowed warn fires very early (step 6-9) — well before budget exhaustion —
  redirecting the agent from "restart+retest" to "read/fix the source"
  reclaims ~70 steps to actually change the code. For task_002073 the
  empty-run warn + "finish if deliverable correct" reclaims the ~20 steps
  lost to the empty/verify oscillation. The warn is soft (agent may ignore
  it), so this is the R2/R3-style bet: the gain is conditional on the model
  reacting. No task is made worse — a warn-only advisory cannot fail a task.
- expected_global_gain: Flip a subset of the 7-task budget_exceeded cluster
  by converting late-onset interleaved spin into an early redirect toward the
  actual upstream bug (source/config), and free budget on tasks currently
  exhausting all 80 steps in a no-progress oscillation. Generalizes across
  domains (C++ microservice, Python monitor daemon, PSV pipeline) because the
  shared root cause is loop-shape (interleaved repeat) not domain knowledge.
- regression_risk: A currently-PASSING task (task_000865_d9a96bd4,
  maxwin8=8) legitimately repeats a command 8x within an 8-window, and
  task_001197 hits 5. Mitigated by making the guard WARN-ONLY (no raise ever)
  — those tasks only ever see a harmless advisory line appended to a tool
  result; they cannot be flipped to `loop_detected`. Cooldown=4 prevents the
  advisory from spamming every subsequent step once a loop is established.
- cost_shift: Near-neutral to negative. A few advisory tokens on affected
  tasks; but converting 80-step budget_exceeded runs into earlier
  completions / earlier bug-fixes should reduce median steps on the cluster.
  Net positive if it flips >=2 budget tasks.
- rollback_trigger: If R5 pass_rate is flat/down AND the budget_exceeded
  count is unchanged, OR any of task_000865 / task_001197 flips T->F, revert
  the WindowedRepeatBreakerProcessor.
