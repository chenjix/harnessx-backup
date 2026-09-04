# Evolve Journal — tmax-coev-rep9-i1

## Round 1 — hard-stop identical-command loops

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-27T00:00:00Z
hypothesis_id: h_loop_detection_v1
levers: [configuration]
predicted_affected: [task_001031_a8f0eb37, task_000863_7acceb19, task_001321_658ce4a8]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=30/50; +2/-1 gained=task_000863_7acceb19,task_001090_c61c71f2 lost=task_001089_220cc46b; score 0.6000 >= incumbent(mean) 0.5800 - tol 0.0400
expected_global_gain: "0 direct flips; eliminates a 98-min exit_reason=error runaway (task_001031) and ~1300s of budget-loop compute on 2 more tasks, freeing wall-clock and removing an error-exit that degrades run stability"
regression_risk: "A false-positive loop trip on a legit passing task; measured max consecutive-identical Bash calls on any passing task = 3 (task_001089), raise threshold = 6, so risk is near-zero"
cost_shift: "Strongly negative — truncates 20-38 wasted 120s-timeout tool calls; no added cost on passing tasks"
rollback_trigger: "If pass_rate drops or any previously-passing task now exits loop_detected, revert"
-->

### Why

R0 scored 29/50 (0.58). Beyond the diverse capability failures (sysadmin 0/5,
etc.), one structural harness deficiency dominated the wall-clock and stability
picture: **byte-identical tool-call stalls with no hard stop**. task_001031
ran for 5868s (98 min) and ended in `exit_reason=error` after emitting the
exact same 2133-char reasoning + the byte-identical `cat > analyze.py` Bash
command 38 times consecutively, each timing out at 120s. Two more tasks
(task_000863, task_001321) hit the 80-step budget cap the same way (33x and
20x identical consecutive Bash calls). The pipeline's `CustomEditToolProcessor`
only emits a soft warning, which the model demonstrably ignored — it even
narrated "I've been stuck in a loop" and kept issuing the identical command.

### Changes

- `config.yaml` — added `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
  with `warn_threshold=4`, `threshold=6`, `name_warn_threshold=10`,
  `compaction_drop_threshold=5`. Strategy-1 (exact name+inputs fingerprint)
  raises `LoopDetectedError` on the 6th consecutive byte-identical tool call →
  clean `exit_reason=loop_detected` (runloop.py:781). No new code authored;
  wiring an existing, tested processor.

### Evidence

- `task_001031_a8f0eb37`: `exit_reason=error`, `elapsed_s=5868`; messages.json
  Counter shows the identical Bash arg repeated 38× (max_consec_identical=21),
  each returning `Error: command timed out after 120s`.
- `task_000863_7acceb19`: `budget_exceeded`, 80 steps, 741s; single Bash
  command repeated 33× consecutively.
- `task_001321_658ce4a8`: `budget_exceeded`, 80 steps, 620s; single command
  repeated 20× consecutively.
- Regression measurement across all 50 tasks: the only PASSING task reaching
  ≥3 consecutive identical Bash args is `task_001089_220cc46b` at exactly 3;
  none reach 4. raise=6 leaves a 2-call margin above the highest passing case.

### Uncertainty

These 3 tasks are capability failures and will still score 0 — the change is a
containment/efficiency win, not a flip. The bet is that removing a 98-min
error-runaway and 1300s of wasted loops improves overall run health with
near-zero regression surface. If any previously-passing task now exits
`loop_detected`, threshold=6 was too tight — revert or raise to 8.

## Round 2 — break no-tool truncation thrash

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T00:00:00Z
hypothesis_id: h_truncation_thrash_v1
levers: [control]
predicted_affected: [task_001032_1adaccb9, task_000958_4bb2b05d, task_000010_644ab1c2]
cited_candidates: [C-001, C-002]
gating_outcome: accepted
gating_attribution: score=31/50; score 0.6200 >= incumbent(mean) 0.6000 - tol 0.0400 (final-round scoring)
expected_global_gain: "Eliminates a 3-task no-tool-call truncation-thrash cluster (10-17 wasted 4096-token turns each) that is invisible to LoopDetectionProcessor; frees ~1500s+ budget and gives a plausible flip on 'one-write-from-done' task_001032 via a corrective nudge the dead length-recovery processor never delivers."
regression_risk: "False hard-stop on a legitimate long-reasoning task. Mitigated: passing tasks peak at 2 long-no-tool turns in an 8-window, heaviest passing task (task_000740) peaks at 4 — a 2-turn margin below raise=6."
cost_shift: "Strongly negative: truncates 6-17 wasted max-token generations per affected task; near-zero added cost on passing tasks (at most one short nudge, only after 3+ in-window truncations)."
rollback_trigger: "If any previously-passing task now exits loop_detected on TruncationLoopGuard, or pass_rate drops vs R1, raise raise_threshold to 8 or revert."
-->

### Why

R1 trajectories expose a systemic failure mode distinct from the tool-call
loops C-001 already contains: the model emits a long block of narration with
NO tool call, hits the 4096-token output cap, is truncated, receives the
run-loop's passive "Please continue from where you left off." nudge, and
re-emits near-identical truncated narration 10-17x — never writing the required
output. LoopDetectionProcessor fingerprints TOOL CALLS only, so a tool-call-free
loop is invisible to it; and LengthTruncationRecoveryProcessor's corrective
nudge never appears in any transcript (its finish_reason=="length" gate is not
firing in this pipeline). The model receives no directive to stop narrating.

### Changes

- processors/truncation_loop_guard.py — new TruncationLoopGuard
  MultiHookProcessor. on_after_model counts long (>=1500 char) tool-call-free
  turns in a sliding window of 8; at warn=3 it queues one strong "stop
  narrating, issue ONE Bash command" nudge; at raise=6 it raises
  LoopDetectedError. on_before_model injects the queued nudge by replacing the
  trailing passive user message (contract-safe, verified). Keys off observable
  content, NOT finish_reason, so it is robust to the pipeline's dead-nudge bug.
- config.yaml — register TruncationLoopGuard (window_size=8, warn_threshold=3,
  raise_threshold=6, trunc_char_threshold=1500) at _order=7, after
  length_recovery, before parse_retry. C-001's LoopDetectionProcessor kwargs
  kept byte-identical (preservative-lock).

### Evidence

- task_001032 steps 27,33,37,39,43,45,47,49: assistant turns, no tool call,
  each 1964 chars, near-identical ("The user is right - I've been stuck in a
  loop..."); final pytest: "Extracted file .../doc1.md is missing" — one write
  from done.
- task_000958: 17x run-loop "cut off by the token limit" messages; steps 45-58
  same shape; exit_reason=budget_exceeded.
- task_000010: 13x truncation; steps 9-51 same shape; only terminated via the
  unrelated tool-call loop detector.
- Passing tasks top out at 2 long-no-tool turns in an 8-window; task_000740
  (passes, heavy) peaks at 4 — below raise=6.

### Uncertainty

The three cited tasks are capability-limited; containment (raise=6) is a
stability/cost win even at 0 flips, and the warn=3 nudge is the flip path for
task_001032. If the nudge itself lengthens responses and re-triggers
truncation, raise=6 still contains it cleanly instead of a runaway. If any
R1-passing task regresses to loop_detected on this guard, raise the threshold
or revert.
