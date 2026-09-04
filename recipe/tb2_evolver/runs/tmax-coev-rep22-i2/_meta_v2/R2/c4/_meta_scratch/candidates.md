# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `CyclicLoopBreaker` `MultiHookProcessor` that detects a repeating
identical period-k tool-call cycle and **blocks-and-redirects the repeated
call (approved=False + synthetic_result) while letting the run continue**,
rather than hard-terminating it, so a stuck agent reclaims its remaining
step budget for the real task.

- Tasks affected: task_000118_3043e92d, task_001857_24daeef3, task_001979_a1e24b6f
- Signal: `exit_reason=budget_exceeded` / `error` with a long tail of
  byte-identical (tool_name, tool_input, result) fingerprints. task_000118 =
  period-1 (28× consecutive identical); task_001857 = period-2 A-B-A-B (~8
  cycles, so `maxconsec_run=1` — period-1 detectors are blind to it);
  task_001979 = one fingerprint ×37.
- Verified (Read of trajectory bodies):
  - task_000118_3043e92d: messages 2–59 repeat the same zombie-process probe
    (`kill -9`-style call → identical `<defunct>` output) ~28 times; the agent
    even emits "I'm stuck in a loop" but cannot break out. It only reaches the
    actual `deployment_monitor.py` work at step ~60 with <20 steps of budget
    left; log dir peaks at 209MB vs the 45MB requirement → reward 0.
  - task_001857_24daeef3: two commands with byte-identical outputs alternate
    (fingerprints `f2ccf2, 2bf091, f2ccf2, 2bf091, …`) for ~8 cycles at
    message indices 8–25 before budget_exceeded.
  - task_001979_a1e24b6f: a single fingerprint recurs 37× across the run;
    exit=error with no forward progress between repeats.
- Why Control not Configuration: the stock `LoopDetectionProcessor` is not in
  this pipeline and its exact detector only counts a *period-1 consecutive*
  tail — it can never see task_001857's period-2 A-B cycle no matter how its
  knobs are tuned, so a Configuration tweak on an absent/limited component
  cannot close the cluster. A new hook is required.
- Why block-and-redirect not the R1 hard-kill detector (`h_cyclic_loop_v1`):
  R1's `CyclicToolLoopDetector` raised `LoopDetectedError` on this same shape.
  Its gating attribution was **+3 / −4** — it flipped 4 previously-passing
  tasks to F because a hard `loop_detected` exit ends the run and can never let
  a *recovering* agent finish. task_000118's body proves recovery is possible
  (the agent does escape and start real work) but too late. Blocking only the
  offending repeat and injecting a corrective synthetic result keeps the run
  alive and hands the reclaimed budget back to the agent — strictly dominating
  hard-kill on the recover-able cases while preserving the budget-reclaim
  benefit on the non-recover-able ones (bounded by `max_total_blocks`).
- Retroactive check (A-corrective): yes for task_000118 (blocking the loop at
  the ~3rd repeat instead of the ~28th returns ~25 steps of budget — enough to
  iterate on the monitor script; standalone sim on its real fingerprint
  sequence blocks at index 3). Partial for task_001857/task_001979: reclaiming
  budget is necessary but the agent must still do correct work afterward — the
  primary, certain win is round-robustness (one stuck task can no longer starve
  the batch), the reward-flip is plausible but not guaranteed.
- expected_global_gain: Closes the degenerate-tool-loop cluster (>=3 tasks
  across system_administration / debugging / data_processing). Unlike the R1
  hard-kill variant this reclaims budget WITHOUT the −4 regression surface,
  because a false trigger costs at most a blocked repeat + a nudge, never a run
  termination.
- regression_risk: Low. The fingerprint includes the observable *output*, so
  productive exploration (an install that finally succeeds, a Traceback that
  becomes success) changes output each cycle and never matches. A legitimate
  double-run of an identical command gets at most one soft warning and is never
  blocked (`break_cycles=3`). Worst case on a genuinely-identical-3x pattern is
  one blocked call + a redirect message; the run continues. No `loop_detected`
  termination unless the agent re-forms the same loop `max_total_blocks=12`
  times.
- cost_shift: Net negative (savings). Early loop-breaking curtails dozens of
  wasted identical tool executions per stuck task; steady-state overhead is one
  sha256 per tool result. Only appends text on a real detection.
