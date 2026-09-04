# Candidates

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Wire the existing `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
into the pipeline (it exists in the codebase but was never added to this
config), so repeated identical / near-identical Bash calls get a "you are
looping, try something different" warning at 3 and a graceful
`LoopDetectedError` termination at 6.

- Tasks affected: task_000118_3043e92d, task_000958_4bb2b05d, task_000206_a943669b
  (secondary/weaker: task_000010_644ab1c2)
- Signal: all four `budget_exceeded` tasks in the round exit at exactly 80
  steps with `reward=0`. Each burns its whole step budget re-issuing the
  same tool call. No loop-detection processor is present in R0 config —
  `ParseRetryProcessor`, `LengthTruncationRecoveryProcessor`, and
  `CustomEditToolProcessor` do not cover the "same Bash command repeated
  verbatim" shape.
- Verified (Read of messages.json):
  - task_000118_3043e92d steps 2,4,6,9,11,13,15,17,19,21,23,25,27 — the
    byte-identical Bash command
    `wait 83 2>/dev/null; sleep 1; ps aux | grep -E "python|worker" | grep -v grep`
    is issued **13 consecutive times**, each returning the same
    `[python3] <defunct>` line; assistant text is verbatim
    "The defunct process is still there. Let me try a different approach to clean it up."
    every time. Then steps 41-68 cycle a second loop
    (pkill worker -> ps check -> run monitor -> ps check) repeatedly until
    budget runs out at step 80.
  - task_000958_4bb2b05d — one `sqlite3 SELECT ... FROM backups ...`
    command is issued **18 consecutive times** (max-consecutive-identical
    = 18; 8 unique calls out of 25).
  - task_000206_a943669b — two near-identical `jq` variants issued 11 and
    10 times (12 unique of 33), max 3 consecutive identical; a same-tool
    varying-args thrash.
- Why Configuration not Control: the correct mechanism already exists as a
  first-class, contract-tested harness processor
  (`LoopDetectionProcessor`). This is a missing-wiring / tuning gap, not a
  missing capability — authoring a bespoke processor would duplicate a
  maintained component and add regression surface. Threshold raised from
  the default 5 to 6 so the 3 warn steps have room to unblock the agent
  before the hard raise, favoring recovery over premature termination.
- Retroactive check (A-corrective): yes. Strategy 1 warns at run=3 and
  raises at run=6.
  - task_000118: the verbatim loop reaches run=13; the warning injected at
    step ~6 ("Stop and reconsider... try something fundamentally
    different") gives the agent an explicit escape from the zombie-process
    fixation while ~74 steps of budget remain — enough to run and validate
    the monitor. If it ignores the warnings, the raise at run=6 terminates
    early rather than wasting the whole round.
  - task_000958 (18 consecutive): warns at 3, raises at 6 — 74 steps
    reclaimed for the actual task.
  - task_000206: name-only warn (Strategy 2) plus Strategy-1 warn at 3
    surfaces the thrash.
  The mechanism (repeated identical calls making zero progress) is the
  actual blocker, not a downstream symptom — the agent had budget and a
  working script but spent it re-issuing the same command.
- expected_global_gain: closes the `budget_exceeded`-at-80-steps failing
  cluster (4 tasks this round, ~8% of the batch). Reclaiming budget and
  injecting a break-out nudge plausibly flips the tasks where the solution
  was already in reach (task_000118 had a working monitor written).
- regression_risk: low. Warn threshold 3 only fires on 3 *consecutive
  byte-identical* calls — legitimate exploration varies arguments and
  breaks the run. The hard raise needs 6 consecutive identical calls, a
  pathological signal no healthy trajectory in this round produced (the
  passing tasks top out well under this). Compaction-drop reset (threshold
  5) prevents stale pre-compaction fingerprints from firing spuriously.
- cost_shift: net decrease. Terminating dead loops at run=6 instead of
  step 80 removes ~70 wasted steps per stuck task; the injected warning
  string is a few hundred tokens, negligible against reclaimed budget.

Rollback trigger: if next round shows any previously-passing task newly
exiting with `exit_reason=loop_detected`, the raise threshold is too tight
— raise `threshold` or drop to warn-only, or revert.
