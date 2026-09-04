# Candidates — Round 1 (tmax-ev9b50)

Baseline: R0 pass_rate = 0.60 (30/50). Model: Qwen/Qwen3.5-9B.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add the in-tree `LoopDetectionProcessor` to the pipeline (warn at 3
consecutive identical Bash calls, raise `LoopDetectedError` at 6) to break
runaway command-repetition loops that currently burn the entire step/time
budget and drive the task to reward 0.

- Tasks affected: task_000010_644ab1c2, task_000958_4bb2b05d,
  task_001031_a8f0eb37 (all reward 0). Also mitigates the thrash portion of
  the long-running timeout cluster (task_001818_b251e5ea, task_001032_1adaccb9).
- Signal: per-task max consecutive-identical Bash-call run length (computed
  from `<task>.messages.json` tool_calls). Exactly 3 tasks exceed 2 identical
  consecutive calls, and **all 3 fail**:
  - task_000010: identical "kill all procs on port 9090" loop **48×**
    consecutive → `finished=max_steps` (80 steps, 204.7s), reward 0.
  - task_000958: identical `pkill -9 -f backup_server; ...` loop **31×**
    consecutive → `finished=max_steps` (80 steps, 213.5s), reward 0.
  - task_001031: identical `cat > analyze.py << EOF ...` rewrite loop **41×**
    consecutive → `finished=no_tool_calls` (66 steps, 548.8s), reward 0.
  - Across all **30 passing tasks the max exact-consecutive run is 1**
    (one failing task hits 2). No passing task ever repeats a command 3×
    consecutively → the warn (3) / raise (6) thresholds cannot fire on any
    currently-passing trajectory.
- Verified (Read of messages.json, Counter over tool_call arguments):
  - task_000010 step log: `# Kill ALL processes using port 9090\nfor pid in
    $(ls /proc | grep ...` issued 49 times, 48 of them back-to-back; agent
    never escaped, hit max_steps with `/home/user/operator.py` never created.
  - task_000958: `pkill -9 -f backup_server 2>/dev/null; sleep 1\ncd
    /home/user ...` issued 33 times, 31 consecutive; hit max_steps.
  - task_001031: `cat > /home/user/analyze.py << 'EOF' ...` issued 41 times
    verbatim; 548.8s elapsed, ended `no_tool_calls` with wrong output.
- Why Control not Configuration: the existing `CustomEditToolProcessor`
  (threshold 7) only detects repeated *file writes* (redirect/sed/tee
  patterns) and only warns — it does NOT catch non-write command loops such
  as the 48× port-kill / 31× pkill loops, and it never terminates. No knob
  on any existing processor covers "same command repeated verbatim N times".
  A dedicated loop guard is the missing mechanical hook.
- Why not Instruction: the system prompt already says "take a fundamentally
  different action" style guidance implicitly; a prompt rule cannot
  mechanically detect that the agent has issued the identical command 40+
  times — only a runtime counter around the loop can. This is a mechanical
  guard that must fire uniformly across tasks → Control.
- Why not a new authored processor: the in-tree
  `harnessx.processors.control.loop_detection.LoopDetectionProcessor` already
  implements exactly this (exact name+input fingerprint, consecutive-tail
  run counting, compaction-aware reset, warn-then-raise). Reusing a
  battle-tested component beats authoring a duplicate. `LoopDetectedError`
  is caught by the run loop and mapped to `exit_reason=loop_detected` (not
  `error`), so it does not trip the replay gate.
- Config choices: `warn_threshold=3` (nudge early, while ~60+ steps of
  budget remain), `threshold=6` (terminate only after 3 unheeded nudges),
  `name_warn_threshold=999` (effectively disable the noisier name-only
  strategy — passing long tasks legitimately reach 40+ same-name Bash calls,
  so name-only warns would add noise without upside).
- Retroactive check (A-corrective): yes — on all 3 target tasks the loop
  begins well before the budget is exhausted (task_000010 loops from early
  steps to step 80; task_001031 rewrites analyze.py 41× across 66 steps).
  A warning at the 3rd identical repeat lands with the large majority of the
  budget intact, giving the agent a real chance to diagnose and change
  approach instead of grinding to max_steps / timeout. Worst case the raise
  at 6 frees compute with the same reward-0 outcome it already gets.
- expected_global_gain: 3 currently-failing runaway-loop tasks
  (system_administration 0/5 is the worst domain; task_000010 and task_000958
  are 2 of its 5 failures) get an early break-out signal; plausibly flips 1-3.
  Also reduces wasted compute on the long-running timeout cluster.
- regression_risk: essentially zero on this task set — no passing trajectory
  reaches 3 consecutive identical calls, so neither the warn nor the raise
  can fire on a currently-passing task. Residual risk: a future task that
  legitimately polls a service with the *exact same* command 6× in a row
  (none observed here); the warn-first design and threshold=6 make premature
  termination unlikely.
- cost_shift: net negative (cheaper). Runaway loops currently burn 60-80
  steps / 200-550s each; terminating or breaking them earlier reduces tokens
  and wall-clock. No added model calls — the guard piggybacks on existing
  tool-result events.
- Rollback trigger: if R2 shows any previously-passing task newly ending in
  `exit_reason=loop_detected`, raise `threshold` and `warn_threshold` or
  revert.
