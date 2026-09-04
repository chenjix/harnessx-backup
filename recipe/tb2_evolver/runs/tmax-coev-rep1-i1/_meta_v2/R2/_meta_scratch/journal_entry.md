
## Round 2 — add loop detection for degenerate tool-call loops

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-16T12:00:00Z
hypothesis_id: h_loop_detection_v1
levers: [control]
predicted_affected: [task_000264_ab8c7253, task_000958_4bb2b05d, task_001090_c61c71f2, task_001089_220cc46b, task_001031_a8f0eb37, task_000015_89886d8d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the dominant remaining failure shape after R1 — degenerate identical-tool-call loops burning the full step/wall budget on 6 failing tasks across 4 domains. Warn-at-3 nudge is the flip mechanism; raise-at-10 is a budget safety net."
regression_risk: "One passing task (task_001701_95e3bbcb) has a run of 7 identical jshon calls and still passed at 78 steps. raise_threshold=10 sits above 7, so it only gets the harmless warn-at-3 nudge, never a termination. name_warn_threshold=8 is warn-only. Residual: warn text mildly perturbs a passing long task's context."
cost_shift: "Strongly negative (savings): six tasks currently burn 70-80 steps / up to 1830s in loops; hard-raise near 10 repeats + best-output recovery collapses the most expensive tail of the round."
rollback_trigger: "If R3 pass_rate is flat/down AND task_001701_95e3bbcb regresses F, or if any currently-passing task newly hits exit_reason=loop_detected, revert or bump the threshold."
-->

### Why

R1 (accepted) eliminated every R0 exit_reason=error by collapsing the
max_tokens narration blobs, but pass_rate stayed at 29/50 — the runaway
loops merely re-manifested as budget_exceeded (80 steps) or slow done
finishes. The dominant remaining failure shape is a degenerate loop of
identical consecutive Bash tool calls. maxrun (longest run of identical
consecutive tool-call argument strings) cleanly separates the failing loop
cluster (16-26 repeats) from the entire passing cluster (all <=7). The
pipeline had NO loop-detection processor wired in, so exit_reason=loop_detected
never fired and these tasks consumed the full step/wall budget. Fix: wire the
in-tree, benchmark-agnostic LoopDetectionProcessor with warn-at-3 (recovery
nudge injected into the tool result before the budget is spent) and a
raise-at-10 hard stop (safely above the one passing task's run of 7).

### Changes

- config.yaml — insert harnessx.processors.control.LoopDetectionProcessor
  after CompactionProcessor (its _order=20 runs after compaction's _order=8 so
  its compaction-drop reset sees the evicted window). Params: window_size=12,
  warn_threshold=3, threshold=10, name_warn_threshold=8,
  compaction_drop_threshold=5. No new files authored — reuse tested in-tree code.

### Evidence

- task_000264_ab8c7253 msgs 2-49: ~26 verbatim repeats of the same
  sqlite3 .indexes command, each returning an identical 28-char output;
  80 steps, budget_exceeded, reward 0.
- task_000958_4bb2b05d / task_001090_c61c71f2 / task_001089_220cc46b:
  maxrun 26, 70-80 steps, reward 0.
- task_001031_a8f0eb37: maxrun 16, 1830s wall-clock — the single most
  expensive task in the round.
- task_000015_89886d8d: maxrun 20; R1 length-recovery collapse fires (30
  truncation markers) but the runloop's persistent continue nudge re-primes
  the loop, so it never self-breaks; 9 runloop cutoff nudges, 0 processor
  nudges reached raw state.
- Bimodal check: passing max identical run = 7 (task_001701_95e3bbcb, PASS,
  78 steps); failing cluster min = 16. raise=10 sits in the empty band.

### Uncertainty

Hard termination alone does not hand a looping task a correct answer — the
net-positive claim rests on the warn-at-3 recovery nudge giving the agent
~60 recovered steps (loops start early: step ~2-4) plus best-output recovery on
budget-exhausted tasks. If pass_rate is flat and only cost drops, the warn nudge
failed to flip and the change is a pure cost win (still Pareto-positive). Signal
to watch: task_001701 regression or any new loop_detected on a previously-passing
task -> revert or raise the threshold.

### Note (no harness fix — capability/env gaps, skipped)

- task_000028_7fe033ac / task_000958 verifier: ModuleNotFoundError requests —
  verifier-phase env gap, agent cannot influence (internet blocked). Skip.
- task_000010_644ab1c2: task requires a deliverable named operator.py containing
  bash; it shadows stdlib operator and crashes the verifier import — task-specific
  footgun, single occurrence, not systemic. Skip.
- task_001090/000118/000958 (non-loop portions): genuine multi-file build/debug
  capability gaps (Go/C++ compilation, resource-limit config) — model capability,
  not harness. Skip.
