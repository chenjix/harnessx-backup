

## Round 3 — break identical-command echo loop

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-16T15:00:00Z
hypothesis_id: h_repeat_action_break_v1
levers: [control]
predicted_affected: [task_000396_e56917e2, task_001321_658ce4a8, task_000264_ab8c7253, task_001031_a8f0eb37]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the dominant harness-shaped failure remaining after R1/R2: exact-repeat tool-call echo-trap loops across >=3 domains. Flips tasks where the model had a live alternative plan but re-emitted the identical command; cost win on the rest."
regression_risk: "Very low: only one passing R2 task (task_001536_acfe6c35) has any identical repetition and it tops out at run=4; repeat_threshold=5 sits strictly above it. Single +1 user-message insertion (contract-verified), fires only inside a proven degenerate loop, resets on any non-identical or no-tool-call turn."
cost_shift: "Negative (savings): six R2 tasks burn 9-11 identical steps (001031=2579s, round's most expensive); breaking 4-6 steps earlier trims the expensive tail. Worst case adds one short user message per fire."
rollback_trigger: "If R4 pass_rate is flat/down AND task_001536_acfe6c35 regresses to F, or any previously-passing task newly exits loop_detected/max_steps after a repeat_break nudge fires, revert or raise repeat_threshold."
-->

### Why

R1 (length collapse) + R2 (loop detection) eliminated the max_tokens narration
runaways and wired a hard loop-stop, but R2 stayed at 30/50. The dominant
remaining harness-shaped failure is an echo trap: the small model issues the
exact same tool call turn after turn (9-11 verbatim repeats). The in-tree
LoopDetectionProcessor detects this and appends a warning to the tool result;
the model reads it, verbally agrees ("The user is right - I've been repeating
myself", "let me take a fundamentally different approach"), then re-emits the
identical command anyway. The warning, buried in a tool result inside a context
polluted with N identical assistant turns, has no purchasing power. The rest of
the R2 failures are genuine capability/env gaps (wrong computed values, verifier
import-requests env gap on 000028/000958, adversarial-corpus classifier logic on
000505/001701) - not harness-fixable; skipped.

### Changes

- processors/repeat_break.py - new RepeatedActionBreakProcessor
  (MultiHookProcessor). on_after_model: fingerprint the assistant turn tool
  calls (name + canonicalised inputs), track a per-task consecutive-identical
  run; a differing or no-tool-call turn resets it. on_before_model: once the
  same call has repeated repeat_threshold(=5) times, append exactly one user
  message that names the repeated command and forbids re-issuing it, directing a
  materially different command or a finish; forceful variant at
  escalate_threshold(=7). Structural trigger only - benchmark-agnostic.
- config.yaml - register via absolute file path, inserted after
  LengthTruncationRecoveryProcessor (order=5) and before CompactionProcessor.
  LoopDetectionProcessor left unchanged as the hard-stop safety net.

### Evidence

- task_000396_e56917e2 steps 61-91: byte-identical rk45 C-file heredoc written
  ~10 times, each prefaced "I've been stuck in a loop ... take a fundamentally
  different approach"; had stated a concrete alternative ("fixed step size of
  0.01") at step 51 but never executed. exit_reason=loop_detected, reward 0.
- task_001321_658ce4a8: identical C rewrite repeated verbatim (maxrun~11) with
  the same loop-acknowledging prose; exit_reason=loop_detected.
- task_000264_ab8c7253: identical recursive-CTE query re-issued at LoopDetection
  counts 7,8,9 with the same "I'm stuck in a loop" prose; exit_reason=loop_detected.
- Passing-cluster safety check: only task_001536_acfe6c35 (PASS) shows any
  identical run and it is 4; every other passing task maxrun=1. repeat_threshold=5
  is strictly above 4.

### Uncertainty

Flip evidence rests on the model executing the alternative it already
articulated once the echo cadence breaks; if the block is a genuine capability
gap the change is a pure cost win, still Pareto-positive. Watch task_001536 for
regression, or any passing task newly hitting loop_detected/max_steps after a fire.

### Note (no harness fix - capability/env gaps, skipped)

- task_000028_7fe033ac / task_000958_4bb2b05d: verifier collection ERROR
  ModuleNotFoundError No module named requests - verifier-phase env gap,
  internet blocked. Skip.
- task_001937 / task_001653 / task_001781 / task_001031 / task_000015 /
  task_000740 / task_001089 / task_000505 / task_001701: agent exits done with
  files present but computed values wrong. Genuine reasoning/capability gaps. Skip.
