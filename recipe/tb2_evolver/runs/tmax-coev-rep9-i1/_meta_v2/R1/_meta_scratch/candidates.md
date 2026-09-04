# Candidates — R1 (tmax-coev-rep9-i1)

Baseline R0: 29/50 pass (0.58). Worst domains: system_administration 0/5,
software_engineering 2/5, scientific_computing 2/5.

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Add the existing `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
to the TB2 pipeline (exact-fingerprint Strategy 1 raises `LoopDetectedError`
on N consecutive byte-identical tool calls), with a threshold tuned so it
never touches legitimate work.

- Tasks affected: task_001031_a8f0eb37, task_000863_7acceb19, task_001321_658ce4a8
  (a 4th, task_000010_644ab1c2, shows the same reasoning stall but varies its
  command so exact-match won't catch it — acceptable partial coverage).
- Signal: `exit_reason=error` on task_001031 with `elapsed_s=5868` (98 min);
  `budget_exceeded` (80 steps) on task_000863 (741s) and task_001321 (620s).
  Behaviour shape: the assistant emits **byte-identical** tool-call arguments
  repeatedly with no progress.
- Verified (Read, messages.json):
  - task_001031 steps 88-99: the SAME 2133-char assistant message repeats
    verbatim; the tool call is the byte-identical `cat > /home/user/analyze.py
    << 'EOF' ...` command issued **38 times consecutively** (Counter top=38,
    max_consec_identical=21), each returning `Error: command timed out after
    120s`. The agent even wrote "I've been stuck in a loop" and kept going.
  - task_000863: single Bash command repeated 33× consecutively
    (max_consec_identical=33); assistant text "I keep making the same
    mistake ... Let me write a completely different approach" repeated verbatim.
  - task_001321: single Bash command repeated 20× consecutively
    (max_consec_identical=20); "I've been over-editing the same file"
    repeated verbatim.
- Regression check (measured across ALL 50 tasks): the only PASSING task that
  ever reaches ≥3 consecutive identical Bash args is task_001089_220cc46b at
  max_consec_identical=3. No passing task reaches 4. Setting
  `warn_threshold=4`, `threshold=6` gives a wide safety margin: all three
  loop failures hit 20-38 (caught at step 6), the sole passing task at 3 is
  never even warned.
- Why Configuration not Control (new processor): the mechanism already exists
  in `harnessx.processors.control.loop_detection` — I am wiring an existing,
  battle-tested processor into the pipeline and tuning its knobs, not
  authoring new code. The existing `CustomEditToolProcessor` only *warns*
  (toothless — the agent ignored it in every cited task); this processor
  *terminates* the run cleanly via `LoopDetectedError` → `exit_reason=loop_detected`.
- Retroactive check (A-corrective): yes for the runaway containment goal —
  had this been in place, task_001031 would have raised `LoopDetectedError`
  at the 6th identical call (~step ~50 instead of running to step 72 over
  98 min), turning `exit_reason=error` into a clean `loop_detected` exit and
  reclaiming ~95 min of wall-clock. task_000863/001321 would exit at step 6
  instead of burning the full 80-step budget (600-740s each). These 3 tasks
  were capability failures and will still score 0, but the change stops them
  from consuming compute/wall-clock and — critically — removes the
  `exit_reason=error` runaway that risks the round's replay/timeout health.
- expected_global_gain: 0 direct flips, but eliminates a 98-min `exit_reason=error`
  runaway and ~1300s of wasted budget-loop compute across 2 more tasks; frees
  wall-clock budget for the rest of the set and removes an error-exit that
  degrades run stability. Generalises to any future identical-command stall.
- regression_risk: near-zero. Measured max consecutive-identical run on any
  passing task = 3 (one task); raise threshold = 6, warn = 4. Exact-match
  fingerprint (name+inputs) essentially never false-positives on legitimate
  work, which always varies command args between calls.
- cost_shift: strongly negative (saves cost). Truncates 20-38 wasted
  120s-timeout tool calls on the runaway and the two budget-loop tasks; no
  added cost on passing tasks (processor only appends a warning string on
  the rare 4th+ identical call, which no passing task reaches).
