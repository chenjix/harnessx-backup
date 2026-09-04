## Round 1 — break max_tokens runaway loop

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-16T09:00:00Z
hypothesis_id: h_length_recovery_v1
levers: [control]
predicted_affected: [task_000264_ab8c7253, task_000015_89886d8d, task_001818_b251e5ea, task_001031_a8f0eb37, task_001032_1adaccb9, task_001321_658ce4a8, task_001701_95e3bbcb, task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=29/50; +3/-3 gained=task_000587_9862bb19,task_001515_eed714e6,task_001701_95e3bbcb lost=task_001089_220cc46b,task_001536_acfe6c35,task_001653_c4cafa73; score 0.5800 >= incumbent(mean) 0.5800 - tol 0.0400
expected_global_gain: "Flips up to 8 failing tasks across 7 domains — the single dominant failure shape (every exit_reason=error trajectory in R0)"
regression_risk: "Very low: no R0 passing task has a max assistant turn in [14.8K,104K); 40K trigger cannot fire on any normal turn. Only touches 2 already-passing tasks that already tripped the loop expensively."
cost_shift: "Strongly negative (savings): collapses 100K-330K-char blobs and breaks 400-960s loops early."
rollback_trigger: "If R2 pass_rate is flat/down AND any of the 2 currently-passing loop-survivors (task_000338_27d6a1be, task_000818_315382d9) regress to F, revert."
-->

### Why

R0 = 29/50 (0.58). The dominant failure mode is a max_tokens runaway
loop: the small model (Qwen3.5-9B) emits an enormous no-tool-call
narration turn (104K-329K chars) that hits the provider output cap.
The runloop (harnessx/core/runloop.py:734) then appends a passive
"Your previous response was cut off by the token limit. Please
continue from where you left off." nudge, which re-primes the exact
runaway generation while the 300KB blob pollutes the next context
window. The cycle burns steps/wall-clock (up to 74 steps, 964s) and
terminates in `exit_reason=error` with the required output file never
written. All 11 `exit_reason=error` tasks in R0 exhibit >=1 cut-off
nudge; 8 of them failed. Max assistant-turn size is cleanly bimodal:
passing cluster tops out at ~14.8K chars, runaway cluster starts at
104K — a 40K threshold sits in the empty band and cannot misfire.

### Changes

- `processors/length_recovery.py` — new `LengthTruncationRecoveryProcessor`
  (`MultiHookProcessor`). `on_after_model`: detect a no-tool-call turn
  with `finish_reason=="length"` OR `content>=40000` chars, collapse
  the blob to head+tail excerpt, arm an escalating nudge, count
  consecutive occurrences. `on_before_model`: inject exactly one user
  message redirecting the model to a single concrete Bash command
  (gentle first, forceful on repeat). Trigger keys only on structural
  signals — benchmark-agnostic.
- `config.yaml` — register the processor via absolute `file://` path,
  inserted after `TaskTimeReminderProcessor` and before
  `CompactionProcessor` (so the collapsed content reaches compaction).

### Evidence

- `task_001031_a8f0eb37`: 144.7K-char assistant turn; body "I keep
  making the same mistake... stuck in a loop", then the cut-off nudge;
  74 steps, 964s, exit=error, `/home/user/results.json` never written.
- `task_000264_ab8c7253`: 328.9K-char turn re-deriving subordinate
  counts, cut-off nudge x2, 20 steps, 485s, exit=error.
- `task_000015_89886d8d`: 286.6K-char OCR-schema narration, cut-off
  nudge x2, 14 steps, 435s, exit=error.
- `task_001032_1adaccb9`: 128.4K-char tar-header hex-dump narration
  loop, cut-off nudge x2, exit=error.
- Bimodal check: passing max assistant turn = 14.8K chars
  (task_001089_220cc46b) vs runaway min = 104.8K
  (task_000818_315382d9, which passed but wasted 437s). No passing
  task lands between 14.8K and 104K.

### Uncertainty

The nudge redirects the model to "one concrete Bash command"; if the
underlying task genuinely needs the model to reason more before
acting, the redirect could push a premature command. Mitigated
because the streak resets on any normal turn, so the model regains
freedom immediately after acting. Signal to watch: if the 2
loop-survivor passes regress or overall pass_rate drops, revert.
This exact processor lineage (tmax-ev9b50 R6) was built against the
identical bimodal signature, giving prior evidence it lands.

## Round 2 — add loop detection for degenerate tool-call loops

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-16T12:00:00Z
hypothesis_id: h_loop_detection_v1
levers: [control]
predicted_affected: [task_000264_ab8c7253, task_000958_4bb2b05d, task_001090_c61c71f2, task_001089_220cc46b, task_001031_a8f0eb37, task_000015_89886d8d]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=30/50; +3/-2 gained=task_001090_c61c71f2,task_001536_acfe6c35,task_001818_b251e5ea lost=task_000740_59416444,task_001701_95e3bbcb; score 0.6000 >= incumbent(mean) 0.5800 - tol 0.0400
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


## Round 3 — break identical-command echo loop

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-16T15:00:00Z
hypothesis_id: h_repeat_action_break_v1
levers: [control]
predicted_affected: [task_000396_e56917e2, task_001321_658ce4a8, task_000264_ab8c7253, task_001031_a8f0eb37]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=31/50; +3/-2 gained=task_000740_59416444,task_001089_220cc46b,task_001701_95e3bbcb lost=task_001264_9f4ca84a,task_001706_24462a09; score 0.6200 >= incumbent(mean) 0.6000 - tol 0.0400
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


## Round 4 — survey/plan/verify system prompt

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-16T18:00:00Z
hypothesis_id: h_survey_plan_verify_prompt_v1
levers: [instruction]
predicted_affected: [task_000933_1f27096a, task_000140_01c78b42, task_000748_c9807703, task_001264_9f4ca84a]
cited_candidates: [C-001]
gating_outcome: reverted
gating_attribution: score=28/50; +1/-4 gained=task_001264_9f4ca84a lost=task_000587_9862bb19,task_001090_c61c71f2,task_001515_eed714e6,task_001536_acfe6c35; score 0.5600 < incumbent(mean) 0.6200 - tol 0.0400 -> revert to R3
expected_global_gain: "Targets the two weakest domains (system_administration 1/5, software_engineering 2/5), dominated by multi-file/multi-service tasks where the missing habit is 'planned + physically verified every output artifact at its exact path'. Plausibly flips 2-4 of the verification-gap subset."
regression_risk: "31 tasks currently pass on the bare 5-line prompt; a longer prompt adds tokens and could over-encourage exploration on trivial tasks. Mitigated: prompt is short, strategy-only, zero task literals, and explicitly scopes formal planning to multi-file/multi-service tasks."
cost_shift: "Mildly positive (small token/step increase from survey+plan+verify discipline), partially offset by fewer wrong-path/thrash steps. Net near-neutral, skewed to gain if flips land."
rollback_trigger: "If R5 pass_rate is flat/down AND >=2 currently-passing tasks regress, or mean step count inflates materially with no flips, revert system_builder to the bare SiblingSystemPromptBuilder / DEFAULT_TMAX_PROMPT."
-->

### Why

R1-R3 (all control lever, all accepted) fully neutralised the max_tokens
runaway, degenerate tool-call loops, and identical-command echo traps —
R3 = 31/50. The residual failure population has changed shape: 17 of 19
failing tasks now exit done/no_tool_calls, i.e. the agent finishes cleanly
and commits a wrong or unverified answer; only 1 error (task_001031) and 1
budget_exceeded (task_001032) remain, both capability-adjacent. The control
lever is exhausted for loop shapes. The instruction lever has never been
tried (scoreboard: instruction=0). The live system prompt is the bare 5-line
DEFAULT_TMAX_PROMPT (verified R3/system_prompt.txt) — no environment survey,
no plan-before-implement, no rigorous pre-exit artifact verification. The
tb2-playbook names exactly these three as its biggest score levers, and the
weakest domains are the multi-file/multi-service classes those habits target.
A distinct verification-gap subset is clearly harness-addressable rather than
pure capability: task_000933 asserted "tarball contains both binaries" without
ever running tar tzf (verifier: binary missing from archive); task_000140
dropped the explicitly-required SIGTERM cleanup and left 3 lingering PIDs;
task_000748 exited on a warnings-only compile without validating profiling
values. These would have surfaced under a "physically ls/cat/tar/pgrep each
enumerated deliverable at its exact path before stopping" discipline.

### Changes

- templates/tmax_system.j2 — new strategy system prompt: (1) survey the
  environment/toolchain before coding; (2) enumerate every required
  deliverable (exact path, running service, exact log line/format, accuracy
  threshold) and keep a visible plan for multi-file/multi-service tasks;
  (3) implement against the spec, not against "it ran" (exit 0 / warnings-only
  compile != correct); (4) before stopping, physically verify EACH deliverable
  with Bash (ls + cat/tar tzf for archives, port probe for services, pgrep for
  required cleanup) and re-derive computed numbers independently. Strategy-only,
  benchmark-agnostic, zero task literals.
- config.yaml — swap SystemPromptProcessor.system_builder from the
  SiblingSystemPromptBuilder (which was resolving the bare default) to the
  in-tree TemplateSystemPromptBuilder pointing at the new .j2 via absolute
  file:// path. All R1/R2/R3 control processors left unchanged — the loop
  safety nets and the existing CustomSelfVerifyProcessor stay in place; the
  prompt front-loads the discipline the checklist alone couldn't induce.

### Evidence

- task_000933_1f27096a final assistant turn: bulleted self-assertion
  "Creates tarball with both binaries / release.tar.gz - Contains both compiled
  binaries" with no tar tzf anywhere in the trajectory; verifier isfile(
  .../bin/graph_math_double) failed.
- task_001264_9f4ca84a msgs 15-23: computed Subdirectories: 46, reasoned about
  ./.. , still wrote 46; "verification" step re-listed files without recomputing;
  verifier expects 45.
- task_000140_01c78b42 msg 0 enumerates "gracefully stop the Go service ...
  SIGTERM"; final state has lingering PIDs [339,590,797].
- Shape check: exit_reason=done + finished=no_tool_calls on 17/19 failures;
  final_pytest failures are exact-path / exact-value / exact-state mismatches,
  not crashes.

### Uncertainty

The flip claim is scoped to the verification-gap subset (missing/misplaced
artifact, dropped cleanup, warnings-only "done"). The pure-numeric-wrong subset
(task_001264 count, task_000748 profiling, task_001653 centroid, task_001937
grid) is a genuine capability gap; if the prompt's re-derivation nudge does not
save those, the change is at worst near-neutral on them. Main risk is a global
token/step tax on the 31 passing tasks — watched via the rollback trigger. If
R5 shows only cost movement with no flips, the instruction lever is a weak fit
here and the next round should return to capability-gap triage / skip.

### Note (no harness fix — capability/env gaps, skipped)

- task_001653_c4cafa73 (centroid 36.37 vs 42.01), task_001937_ac874115 (grid 60
  vs 50), task_000505_50b5162d (adversarial classifier bypass), task_001781
  (deadlock logic): computed values wrong — model reasoning/capability, not
  harness. Skip.
- task_000028_7fe033ac / task_000958_4bb2b05d: verifier-phase ModuleNotFoundError
  (requests) env gap, internet blocked. Skip.
- task_000010_644ab1c2: deliverable operator.py shadows stdlib operator and
  crashes verifier import — task-specific footgun, single occurrence. Skip.
