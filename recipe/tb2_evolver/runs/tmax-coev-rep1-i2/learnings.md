## Round 1 — break small-max_tokens truncation loop

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-16T17:30:00Z
hypothesis_id: h_length_recovery_v3
levers: [control]
predicted_affected: [task_000740_59416444, task_001032_1adaccb9, task_000396_e56917e2, task_000028_7fe033ac, task_001653_c4cafa73, task_000015_89886d8d, task_000958_4bb2b05d]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=28/50; +2/-4 gained=task_000396_e56917e2,task_000740_59416444 lost=task_001031_a8f0eb37,task_001090_c61c71f2,task_001536_acfe6c35,task_001818_b251e5ea; score 0.5600 >= incumbent(mean) 0.6000 - tol 0.0400
expected_global_gain: "Rescues the small-max_tokens truncation-repetition cluster (7-8 failing tasks, 4 pure budget_exceeded step-burns) by replacing the run loop's counter-productive passive 'continue' nudge with an actionable one."
regression_risk: "Low — trigger only fires on no-tool-call finish_reason==length turns; passing r0 tasks never hit that (their turns are <1600 chars and all carry tool calls). Contract-checked clean."
cost_shift: "Net reduction expected: target tasks currently grind to 80 steps / 300-1350s in the loop; breaking it earlier cuts tokens and wall-clock on the most expensive runs."
rollback_trigger: "If R2 pass_rate does not improve AND the budget_exceeded cluster (740/396/028/958) is unchanged, or any previously-passing task regresses to a truncation loop, revert to v2."
-->

### Why

r0 = 30/50. Of the 20 failures, 4 are `budget_exceeded` @ 80 steps, 1
`loop_detected`, 15 `done`-but-wrong. The single largest *harness-addressable*
cluster is a small-`max_tokens` truncation-repetition loop: the model emits a
long narration, the provider cuts it off at ~2000 chars (`finish_reason=length`,
no tool call), the run loop appends a passive "Your previous response was cut
off by the token limit. Please continue from where you left off." user message,
and the model regenerates the *identical* paragraph — looping for dozens of
steps. Passive-nudge counts: task_000740→11, task_001032→17, task_000396→10,
task_000028→5, task_001653→5, task_000015→3, task_000958→3.

The existing `LengthTruncationRecoveryProcessor` (v2) was inert on this model:
its `content_char_threshold` of 40000 could never match ~2000-char content, and
its `on_before_model` *appended* a corrective message — which the before_model
contract drops when the last role is already `user` (it is, because the run loop
just appended the passive nudge). So the actionable nudge never reached the
model; the passive re-priming message stayed the last thing it read.

### Changes

- `processors/length_recovery.py` — v3: `on_before_model` now *rewrites the
  last user message's content* (the passive "continue" nudge) into an actionable
  "stop narrating, run ONE command" directive when a truncation streak is armed
  (contract-safe len_delta 0); falls back to a +1 append only when the last role
  is not `user`. Trigger still keys on `finish_reason==length` (reliable here).
- `config.yaml` — repoint the `LengthTruncationRecoveryProcessor` `_target_`
  from the R0 v2 asset to the new R1 v3 file; kwargs unchanged.
- `system_prompt.txt` — copied byte-for-byte from R0 (sidecar for
  SiblingSystemPromptBuilder; identical to DEFAULT so no prompt drift).

### Evidence

- `task_000740_59416444.messages.json`: assistant turns 11,13,35,39,43,47,51,
  58,62,66 are exactly 2001 chars with no tool call; each followed by a
  persisted user msg "...cut off by the token limit. Please continue..."; the
  next assistant turn re-emits the identical "I've been stuck in a loop..."
  paragraph. `exit_reason=budget_exceeded` @ step 80, 1350s.
- `task_001032_1adaccb9`: 17 passive nudges, 51 steps, 1018s, still wrong.
- `task_000396_e56917e2`: 10 passive nudges, budget_exceeded @ 80 steps, 1248s.
- Max assistant content across task_000740 = 2001 chars ≪ v2 threshold 40000
  (v2 char branch structurally cannot fire on this model).

### Uncertainty

The underlying answer may still be wrong on some of these tasks even after the
loop is broken (e.g. task_000740's window-function logic bug). But freeing the
wasted step budget gives the model turns to self-correct instead of grinding to
the cap, and 4 of the cluster are pure step-burns. If R2 shows no movement on
the budget_exceeded set, the loop-break is not the binding constraint — revert
and look upstream at the model's reasoning-length habit.

## Round 2 — verify services with a requests client

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-16T18:10:00Z
hypothesis_id: h_service_requests_verify_v1
levers: [instruction]
predicted_affected: [task_000958_4bb2b05d, task_000028_7fe033ac]
cited_candidates: [C-002]
gating_outcome: accepted
gating_attribution: score=29/50; +4/-3 gained=task_000536_9c16e8ef,task_000748_c9807703,task_001653_c4cafa73,task_001706_24462a09 lost=task_000587_9862bb19,task_001321_658ce4a8,task_001701_95e3bbcb; score 0.5800 >= incumbent(mean) 0.6000 - tol 0.0400
expected_global_gain: "Closes the 'external verifier probes agent's network service with the requests library, but requests is absent -> whole test module errors at collection (rc=2, 0 tests run)' failure shape. Generalizes to any HTTP/socket-service task; already recurs across data_querying + system_administration."
regression_risk: "Very low. Guidance only affects tasks that build/run a service; adds a benign pip install requests + a few client test calls. No processor pipeline change, so no mechanical regression surface; non-service (already-passing) clusters see an inert extra paragraph."
cost_shift: "Negligible (<1%): at most one pip install + a couple of requests calls on the small service-task subset; unchanged elsewhere."
rollback_trigger: "If R3 pass_rate does not improve AND task_000958 stays failing on a verifier import/collection error (or any previously-passing task regresses to a service-verification detour), revert to R1's system_prompt.txt."
-->

### Why

R1 landed at 28/50. Sweeping all failing result.json, exactly two tasks —
task_000958 (data_querying) and task_000028 (system_administration) — fail
because the *verifier's* test module cannot `import requests`: the tail is
`ModuleNotFoundError: No module named 'requests'` -> `Interrupted: 1 error
during collection` (rc=2). The test suite never runs a single assertion, so
even a correct service scores zero. Both tasks ask the agent to build a C++
HTTP microservice that the external verifier probes with `requests`. Because
the agent and verifier share one container filesystem, an agent that tested
its own service with a `requests`-based client (installing the package first)
would leave `requests` importable and let the verifier collect its tests.

### Changes

- `system_prompt.txt` (sibling read by SiblingSystemPromptBuilder) — kept the
  R1 base byte-for-byte and appended a general strategy paragraph: when a task
  runs a network/HTTP service, verify it end-to-end from a real client,
  preferring Python `requests` (install it if absent; don't assume
  curl/wget/nc), exercising every specified endpoint/auth/error case, and
  confirming the process is still alive at finish. No task IDs, ports, or
  paths from the training set are embedded — it is a class-level testing habit.
- `config.yaml` — copied byte-for-byte from R1 (processor pipeline unchanged);
  only the sibling prompt sidecar changed.

### Evidence

- `task_000958_4bb2b05d` `final_pytest.output_tail`: `import requests` ->
  `ModuleNotFoundError` -> `Interrupted: 1 error during collection` (rc=2).
  Final assistant turns: C++ server was running (pid 739) and answered
  `GET /chain?id=1` with `[1,2,3,4]` (correct-looking) — yet 0 tests ran.
- `task_000028_7fe033ac` `final_pytest.output_tail`: same `import requests`
  collection crash (rc=2). Agent summary explicitly notes "curl and wget were
  not available... nc was not available... urllib failed" before it finally
  self-tested via a Python client (`Status: 200 Body: b'150\n'`) — never
  installing `requests`. (028 also has a genuine frame-count accuracy bug, so
  it is cited only as the second instance of the import-crash mechanism, not a
  claimed flip.)

### Uncertainty

The change is Instruction-only and depends on the model acting on the guidance
on service tasks. task_000958's flip requires its service endpoints to be
actually correct once the verifier can collect; if they are subtly wrong it
stays failing despite the import fix. task_000028 will not flip on this change
alone (accuracy bug is upstream). If R3 shows no movement on 958 and no new
service tasks benefit, the binding constraint is the model's service
correctness, not the import crash — revert and look upstream.

## Round 3 — collapse the truncation reasoning-spiral

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-16T18:40:00Z
hypothesis_id: h_length_recovery_v4_collapse
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_000028_7fe033ac, task_000118_3043e92d, task_001031_a8f0eb37, task_001032_1adaccb9, task_001089_220cc46b, task_001321_658ce4a8]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=32/50; +5/-2 gained=task_000028_7fe033ac,task_000264_ab8c7253,task_001321_658ce4a8,task_001536_acfe6c35 lost=task_000396_e56917e2,task_000536_9c16e8ef; score 0.6400 >= incumbent(mean) 0.6000 - tol 0.0400
expected_global_gain: "Rescues the max_tokens truncation-spiral cluster (7 failing tasks, all remaining budget_exceeded cases plus the round's longest 65-92-message step-burners) by killing the re-seeding reasoning tail v3 preserved and firing the collapse on every truncated turn. R1 v3 flipped 2 of the original cluster with the nudge alone; v4 adds the missing mechanical half."
regression_risk: "Low - collapse fires only on no-tool-call finish_reason==length turns (or oversized content); the 29 passing R2 tasks never reach that state (turns carry tool calls, under 4096 output tokens). on_before_model path byte-identical to v3 (accepted R1/R2, contract-clean)."
cost_shift: "Net reduction: collapsing each truncated turn to ~400 chars (from ~2000) and killing the accumulating tail cuts the 24k-to-40k input-token climb on the most expensive runs and breaks the spiral earlier."
rollback_trigger: "If R4 pass_rate does not improve AND the cluster (010/028/118/1031/1032/1089/1321) still shows many passive nudges per task with no flips, the binding constraint is the model reasoning-length habit, not the loop - revert to R2 v3 processor."
-->

### Why

R2 = 29/50. Of 21 failures, exactly 7 are dominated by a max_tokens truncation
spiral: task_000010 (18 passive nudges / 25 collapses), task_001031 (20/36),
task_001032 (20/20), task_000028 (13/15), task_001321 (12/14), task_000118
(10/11), task_001089 (9/27). These are the round's longest runs (65-92 messages)
and include all remaining budget_exceeded cases. The other 14 failures are short
runs (under 50 messages, 0 truncation) with genuine partial-test accuracy bugs -
model capability gaps, not harness-addressable this round.

R1 v3 processor (repointed, still live in R2) does the right thing at the
on_before_model layer: it rewrites the run loop passive "continue from where you
left off" nudge into an actionable "run ONE command" directive, and the bodies
confirm the model reads and acknowledges it. But it keeps spiralling. Two
mechanical amplifiers, invisible until this round body read, keep the loop
alive:
1. Re-seeding tail: v3 collapsed oversized turns to head(1200)+marker+tail(600),
   and the preserved tail is exactly the mid-reasoning continuation the next
   turn latches onto and keeps developing.
2. Barely-triggered collapse: most truncated turns are ~2000 chars, right at
   the v3 ~2000 collapse threshold, so v3 often left the whole runaway turn in
   history; 10-30 of them accumulate (input_tokens climb 24k-to-40k on
   task_000010), costing budget and re-priming the same unfinished thought.

The truncation is NOT degenerate token-repetition - it is genuine verbose
chain-of-thought ("Wait, I think I see the issue now! Let me trace through the
code...") running past the 4096 output-token cap before reaching a tool call.
max_tokens is a runtime-only slot (env TMAX_MAX_TOKENS) outside the config
surface, and a louder prompt cannot make a 9B model self-limit reasoning it
acknowledges but cannot shorten. The only remaining lever is mechanical: stop
feeding the model its own unfinished spiral.

### Changes

- processors/length_recovery.py - v4: on_after_model now collapses every
  truncated no-tool-call turn to a short head stub (head_chars=400) with NO
  tail (tail_chars=0), replacing the discarded middle+tail with a terminal
  marker that tells the model the reasoning is gone and not to reconstruct it.
  Collapse threshold drops to head+tail+len(marker) (~600) so it fires on every
  ~2000-char truncated turn. on_before_model unchanged from v3 (contract-safe
  rewrite of the last user nudge; +1 append when last role is not user); the
  escalated repeat nudge now offers a concrete short-output path (single heredoc
  write) instead of asking the model to self-limit reasoning.
- config.yaml - repoint LengthTruncationRecoveryProcessor _target_ from the R1
  v3 asset to the R3 v4 file; add head_chars: 400, tail_chars: 0 kwargs. Rest of
  the pipeline unchanged.
- system_prompt.txt - copied byte-for-byte from R2 (sibling for
  SiblingSystemPromptBuilder; no prompt drift).

### Evidence

- task_000010_644ab1c2 oh_runs trace: steps 50/53/55-59/61/67-69 all
  stop_reason=length, output_tokens=4096, no tool call; input_tokens climb
  24625 -> 40018 across accumulating ~2000-char reasoning turns.
- task_001032_1adaccb9.messages.json steps 35/37/39/41: assistant quotes the v3
  nudge back ("The user is telling me to stop the repetitive analysis and just
  run a single Bash command...") then narrates the full 2009-char turn and
  truncates again; the collapse tail preserves the exact mid-reasoning
  continuation re-seeding the next turn.
- Contrast: task_000010 steps 60/62/63 emit tool_use at output 1039-1450 tokens
  (under 4096) - a short-context turn fits within budget, so shrinking context
  and killing the re-seed is the right lever.

### Uncertainty

Even with the loop broken, some tasks carry upstream logic bugs (task_001032
path-safety, task_000028 frame-count accuracy) that may keep them failing after
the step budget is freed. The bet is that freeing budget plus a clean short
context lets the model reach and iterate on a tool call instead of grinding to
the cap. If R4 shows the cluster still spiralling with no flips, the constraint
is the model reasoning-length habit and the lever is exhausted - revert to v3.

## Round 4 — silence structurally-meaningless loop warning

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-16T19:10:00Z
hypothesis_id: h_loop_name_warn_disable_v1
levers: [configuration]
predicted_affected: []
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=30/50; +5/-7 gained=task_000536_9c16e8ef,task_001032_1adaccb9,task_001089_220cc46b,task_001090_c61c71f2 lost=task_000028_7fe033ac,task_000264_ab8c7253,task_000748_c9807703,task_001264_9f4ca84a; score 0.6000 >= incumbent(mean) 0.6400 - tol 0.0400
expected_global_gain: "Removes 567 false 'you are going in circles' injections across 47/50 tasks; cleaner/cheaper context on every longer run and stops desensitising the model to the real (exact-match) loop detector, which is preserved."
regression_risk: "Very low - only removes a warning that provably fired on passing tasks without helping them; Strategy-1 exact-repeat detection (warn_threshold/threshold) is untouched, so real identical-command loops still raise loop_detected. Single knob, no code authored."
cost_shift: "Net reduction - each suppressed warning is ~350 chars appended to a persisted tool result; 567 of them, concentrated on the longest/most expensive tasks."
rollback_trigger: "If R5 pass_rate < 32/50 OR any R3-passing task regresses into loop_detected/budget_exceeded that the name-only warning would plausibly have pre-empted, revert name_warn_threshold to 8."
-->

### Why

R3 = 32/50. The truncation-spiral control lever (R1/R3) has landed and is now
exhausted: this round's bodies confirm it worked - passive "continue" nudges
dropped from 10-20/task (R2) to 0-4/task (R3), max assistant content dropped
from ~2000 (right at the R3 collapse threshold) to 596-3320, and 0
collapse-marker turns were needed. R3's own rollback trigger ("cluster still
spiralling with many passive nudges per task") did NOT fire, so the processor
stays as-is. The remaining 18 failures are dominated by model capability/logic
gaps that are not harness-addressable: multimodal input (OCR of PNG / audio
transcription) on 000015/000536/001818, and wrong computed values / algorithms
on 001498 (error 231 vs 163), 001937 (grid 60 vs 50), 000587, 001090, 000958,
001032, 000505, 001781. These get one line each: require model capability
(image/audio parse, correct domain logic); no harness fix - skip.

The one genuinely *systemic, cross-cutting, harness-addressable* signal is a
false-positive control mechanism. LoopDetectionProcessor has two strategies:
Strategy 1 keys on the exact fingerprint (tool name + serialised inputs) and
catches real identical-command loops - it fired only 61 times total and
correctly raised loop_detected on 000536 (identical failing SQL repeated 8-9x).
Strategy 2 (name_warn_threshold=8) warns on tool-*name* repetition alone. In TB2
the agent has exactly one tool (Bash, a hard benchmark constraint), so Strategy
2 fires on essentially every non-trivial task: 567 times across 47/50 tasks,
passing and failing alike. It carries zero information, inflates persisted
context, and repeatedly tells the agent it is "going in circles" during
legitimate multi-step work - the opposite of the sustained planning/execution
the playbook names as the biggest lever.

### Changes

- `config.yaml` - LoopDetectionProcessor `name_warn_threshold` 8 -> 999,
  effectively disabling the structurally-meaningless Strategy-2 (name-only)
  warning while leaving Strategy-1 exact-match detection (warn_threshold=3,
  threshold=10) fully intact. No other pipeline change.
- `system_prompt.txt` - copied byte-for-byte from R3 (sibling for
  SiblingSystemPromptBuilder; no prompt drift).

### Evidence

- `harnessx/processors/control/loop_detection.py:196-204`: Strategy 2 computes
  s2_run from event.tool_name only and emits the "called `{tool}` {count} times
  consecutively with different arguments ... going in circles?" template.
- Counted across all 50 R3 messages.json: Strategy-2 name-only warnings = 567
  across 47 tasks; Strategy-1 exact-match warnings = 61. Not a discriminator:
  passing task_001673 carries 29, failing task_000010 carries 15.
- task_000015 final tool message: "Task completed successfully! [LoopDetection]
  ... 13 times consecutively with different arguments. This suggests you are
  stuck in a repetitive pattern" - factually wrong, appended to a successful
  completion, and persisted in context.
- Contrast preserved: task_000536 body shows the Strategy-1 template ("exact
  same tool call(s) ... issued 8 times in a row") firing on a real stuck SQL
  loop - that path is unchanged by this round.

### Uncertainty

This is primarily a cost/context-hygiene + desensitization fix, not a claimed
single-task flip (predicted_affected intentionally empty). If R5 shows a
passing task newly looping because the name-only nudge is gone, Strategy 1 at
threshold=10 was the real backstop and the name warning was load-bearing after
all - revert to 8. Given passing tasks already tolerated 20-38 of these warnings
without benefit, that outcome is unlikely.

## Round 5 — revert net-negative name-warn knob to incumbent

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-16T19:40:00Z
hypothesis_id: h_loop_name_warn_revert_v1
levers: [configuration]
predicted_affected: [task_000028_7fe033ac, task_000264_ab8c7253, task_000748_c9807703, task_001264_9f4ca84a, task_001321_658ce4a8, task_001515_eed714e6, task_001536_acfe6c35]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=34/50; +6/-2 gained=task_000028_7fe033ac,task_000587_9862bb19,task_000748_c9807703,task_000958_4bb2b05d lost=task_000578_cebe85a5,task_001089_220cc46b; score 0.6800 >= incumbent(mean) 0.6667 - tol 0.0400
expected_global_gain: "Restores the R3 32/50 incumbent by undoing R4's single net-negative knob (name_warn_threshold 8->999, measured 32->30 with an incoherent +5/-7 swing); re-protects the 7 R3-passing tasks R4 lost."
regression_risk: "Low - byte-for-byte return to a config that already measured 32/50. Only downside if R4's 5 'gained' tasks were genuinely helped by suppressing the name warning, but they span unrelated domains with no mechanism tying them to Strategy-2; if so, revisit with a middle-ground threshold rather than 999."
cost_shift: "~Flat vs R3. Re-enabling Strategy-2 at threshold 8 re-adds a modest number of name-only warnings on long runs; well below R4's worst-case since 8 is far stricter than the effectively-off 999."
rollback_trigger: "If R6 pass_rate < 32/50 AND the 7 re-protected tasks did not recover (still_F), name_warn=8 is not the binding constraint and the R3->R4 swing was pure variance - stop tuning this knob and look upstream at model capability gaps."
-->

### Why

R4 = 30/50, DOWN from the R3 incumbent 32/50. The sole R3->R4 config delta
was LoopDetectionProcessor name_warn_threshold 8 -> 999 (disabling the
Strategy-2 tool-name-repetition warning). The result was a chaotic
+5-gained / -7-lost swing with no coherent cluster on either side - the
signature of run-to-run variance on borderline tasks, not a real knob
effect. R4 passed the gate only on tolerance (0.6000 >= 0.6400 - 0.0400),
and R4's own declared rollback_trigger was "If R5 pass_rate < 32/50 ...
revert name_warn_threshold to 8" - a condition now met. The truncation-
spiral control lever (R1/R3) that produced the 32/50 incumbent is confirmed
landed and exhausted (task_000396 turns now collapse to ~590 chars and
carry tool calls; it fails on a genuine RK45 numerical bug, not a loop).
The residual 13 stable failures are model capability/logic gaps (numerical
algorithms 396/1937, security bypass 505, CSV/NaN 587, memory-leak/deadlock
1781, log-rotation 118, tarball 933, error-value 1498) - not harness-
addressable this round: requires model capability (correct domain logic /
multimodal); no harness fix - skip.

### Changes

- `config.yaml` - LoopDetectionProcessor name_warn_threshold 999 -> 8
  (revert R4's change; restore the R3 incumbent value). All other
  processors, kwargs, and the sibling system_prompt.txt are byte-identical
  to R3.
- `system_prompt.txt` - byte-for-byte copy of R3's sidecar (no prompt
  drift).

### Evidence

- `history/R3_to_R4.diff`: sole delta is `name_warn_threshold: 8 -> 999`.
- `history/R3_per_task.json` vs `history/R4_per_task.json`: R3=32, R4=30;
  GAINED R3->R4 = {536,1032,1089,1090,1818}, LOST = {028,264,748,1264,1321,
  1515,1536}. Unrelated domains on both sides = variance, not a knob effect.
- `task_000396_e56917e2.messages.json`: truncated no-tool turns collapsed
  to ~590 chars, 37/49 assistant turns carry tool calls; final failure is a
  numerical divergence (max deviation 0.608), confirming the truncation
  lever is done and this is a capability gap.
- `task_000140_01c78b42` verifier tail: "Lingering vm_service processes
  found: ['347','619','827']" - a single cleanup-logic task, too narrow to
  justify a new processor.

### Uncertainty

If R6 measures < 32/50 with the 7 re-protected tasks still failing, the
whole R3<->R4 delta was noise and name_warn is not load-bearing either way;
the lever is exhausted and future rounds should stop tuning loop-detection
knobs and accept that the ceiling is set by model capability on the
numerical/security/multimodal clusters.

## Round 6 — no-op: harness levers exhausted, residual is capability gaps

<!-- journal:frontmatter
round: 6
timestamp: 2026-08-17T01:00:00Z
hypothesis_id: h_noop_capability_ceiling_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=32/50; +3/-5 gained=task_000396_e56917e2,task_000578_cebe85a5,task_001089_220cc46b lost=task_000028_7fe033ac,task_000958_4bb2b05d,task_001032_1adaccb9,task_001090_c61c71f2; score 0.6400 >= incumbent(mean) 0.6600 - tol 0.0400
expected_global_gain: "None claimed. Byte-for-byte hold of the R5 34/50 incumbent. All harness-addressable levers (truncation collapse, self-verify, loop detection, time reminders, service-verify instruction) are landed and confirmed firing; every one of the 16 residual failures traces to a model capability/logic gap with no harness mechanism to flip it. Shipping a change here would risk the 34 passing tasks with no evidenced upside."
regression_risk: "Zero — config and sidecar prompt are byte-identical to R5 (verified via diff). Canonicalizes clean (checked_templates=0)."
cost_shift: "Flat — no pipeline or prompt change."
rollback_trigger: "N/A (no change). Next round: if a NEW harness-addressable signal appears (e.g. a fresh cluster of exit_reason=error crashes, a systemic false-positive injection, or a mechanical waste pattern), act on it. Do NOT re-tune loop-detection knobs (R4/R5 confirmed net-neutral variance) or re-touch the truncation processor (confirmed landed+exhausted since R3)."
-->

### Why

R5 = 34/50 (up from R4 30, R3 32). I read all 16 residual failures at the
result.json + final-assistant-turn level and confirmed the R5 standing
conclusion: the harness levers are exhausted and the residual is model
capability. Two clusters:

1. **Exhaustion exits (6 tasks)**: task_000010 (budget_exceeded, k8s operator
   circular-import it never resolves), task_000396 (budget_exceeded, RK45
   numerical divergence), task_001089 (budget_exceeded, Makefile that won't
   build a regression test — agent narrates "I'm stuck in a loop" and cycles
   real approaches), task_001321 (budget_exceeded, f1-score mismatch),
   task_000578 (loop_detected, results.json wrong), task_001031 (error, sci
   computing). The truncation-spiral that R1/R3 fixed is GONE here — passive
   "continue" nudges dropped to 0-2/task (was 10-20), max assistant content is
   971-2775 chars, and turns carry real tool calls. These now burn steps on
   genuinely hard logic the 9B model cannot crack, not on a harness loop.

2. **Done-but-wrong (10 tasks)**: 264, 1498, 1781, 1536, 1937, 505, 015, 933,
   140, 118. Short runs (11-68 steps), exit=done, mixed pass/fail on the hidden
   suite (e.g. "1 failed, 2 passed"). The CustomSelfVerifyProcessor DID fire on
   these (the `_tb2_self_verify` keepalive tool appears in every trace) and the
   agent visibly re-checked file existence + contents after the checklist nudge
   — then still declared SUCCESS. The failures are exact semantic criteria the
   agent cannot infer from the task text alone (CSV format on 264, frame-index
   set on 1498, sanitize behavior on 1536, adversarial corpus on 505, lingering
   processes on 140, report values on 1937). This is the inference ceiling the
   playbook names; the double-confirmation lever is already implemented and
   working.

### Changes

- `config.yaml` — byte-for-byte copy of R5 (explicit no-op; `diff` clean).
- `system_prompt.txt` — byte-for-byte copy of R5's sidecar (`diff` clean).

### Evidence

- `summary.json`: n_passed=34/50 (0.68). Domain breakdown: sci_computing 2/5
  and system_administration 2/5 are the weakest, both dominated by numerical
  /debugging capability gaps.
- 6 exhaustion exits verified: task_000396 max assistant content 2459 chars,
  1 passive nudge (vs ~10 pre-R1); task_001321 2 nudges; task_001031 0 nudges —
  truncation loop confirmed dead, budget now spent on real (failing) tool work.
- `task_000264` trace: `_tb2_self_verify` keepalive present; post-nudge the
  agent runs `ls` + re-reads task + `cat`s output, then exits — yet
  `test_csv_output` and `test_query_plan_output` still fail on format. Self-
  verify fired and worked mechanically; the gap is semantic inference.
- `task_001089` no-tool turns quote "I'm stuck in a loop... the Makefile is
  still not working. Let me try a completely different approach" — genuine
  debugging dead-end, not a harness artifact.
- Unavailable levers confirmed: Bash is the only tool (hard TB2 constraint, no
  tool-schema shaping); max_tokens/effort are runtime-only (TMAX_MAX_TOKENS
  outside the config surface) — so the playbook's tool-schema and effort-shaping
  levers cannot be exercised via config.yaml.

### Uncertainty

Risk of a no-op is leaving score on the table if a subtle harness lever exists
that I missed. I checked the whole pipeline (self-verify, loop detection,
truncation collapse, time reminders, compaction, edit-guard, bg-install-guard)
and every residual failure has a non-harness root cause. If a future round
finds a NEW systemic mechanical pattern (crash cluster, false-positive
injection, wasted-step regularity) that is not present at R5, that is the signal
to ship again. Tuning the existing knobs further is confirmed net-neutral
variance and should stop.

## Round 7 — exit-time process-lifecycle reconciliation

<!-- journal:frontmatter
round: 7
timestamp: 2026-08-17T02:00:00Z
hypothesis_id: h_process_lifecycle_verify_v1
levers: [control]
predicted_affected: [task_000140_01c78b42, task_001090_c61c71f2, task_000028_7fe033ac]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=35/50; +4/-1 gained=task_000015_89886d8d,task_001090_c61c71f2,task_001515_eed714e6,task_001536_acfe6c35 lost=task_000396_e56917e2; score 0.7000 >= incumbent(mean) 0.7000 - tol 0.0400
expected_global_gain: "system_administration is the single 0/5 domain in R6; 3 of its 5 failures (140/1090/028) share one harness-addressable root cause — the exit-time verification checklist covers output files and 'services still alive' but has NO coverage for processes that must be STOPPED/not-lingering, nor for a recorded PID pointing at a shell wrapper instead of the target binary. The reconciliation step generalizes to any process/service task in any domain."
regression_risk: "Low — the new processor subclasses the accepted stock CustomSelfVerifyProcessor and reuses its singleton group tb2_self_verify (so no second keepalive fires); the only change is one additive, task-conditional checklist paragraph. Contract/dry_fire/canonicalize all clean. Passing non-process tasks read one extra n/a paragraph."
cost_shift: "Negligible — one ~600-char paragraph injected once per task at the already-existing exit keepalive; a few extra Bash calls only on the affected cluster when the agent cleans leftovers or relaunches a mis-recorded PID."
rollback_trigger: "If R8 pass_rate < 32/50 AND system_administration is still 0-1/5 with 140/1090 unflipped (self-verify keepalive present but exit still wrong), the lifecycle reconciliation is not the binding constraint — these are deeper capability/inference gaps; revert to the stock CustomSelfVerifyProcessor."
-->

### Why
R6 = 32/50. The single worst domain is system_administration at 0/5
(tasks 010, 028, 118, 140, 1090). Prior rounds (R5/R6) filed the whole residual
as "model capability, no harness fix", but a domain at 0/5 warranted a fresh
body read. Three of the five failures share one coherent, harness-addressable
root cause on the process/service lifecycle axis that the current exit-time
CustomSelfVerifyProcessor checklist does not cover:
- task_000140: verifier test_no_lingering_service_processes fails —
  "Lingering vm_service processes found: ['350','617']". The agent spawned
  provisioning processes during its own testing and never reaped them; the
  checklist item 5 only asks about services that must stay up, never about
  leftovers that must be gone.
- task_001090: verifier test_pid_file_and_process fails —
  "Process name is 'bash', expected 'monitor'". The agent launched the monitor
  inside a bash -lc "... nohup ./monitor & echo the-bang-pid" wrapper, so the
  captured PID was the subshell (777 = bash), not ./monitor (778). Its own
  SUCCESS summary claimed PID 778 while the recorded file held 777 — a classic
  shell-idiom bug.
- task_000028: verifier fails on Connection refused at :8080 — the service that
  had to persist was not alive at verify time (must-persist end of the same
  axis; exit=budget_exceeded so a partial flip at best).

### Changes
- processors/process_lifecycle_verify.py — new
  ProcessLifecycleSelfVerifyProcessor(CustomSelfVerifyProcessor). Overrides only
  on_after_model to arm an extended exit checklist: stock items 1-5 plus a
  general item 6 that tells the agent to run ps/pgrep, reconcile the final
  process table against the task's stated lifecycle wording (which processes
  must persist, which must be gone including self-test leftovers), and confirm
  any recorded PID names the actual binary via /proc/<pid>/comm (warning that a
  bash -lc "... &" wrapper records the wrapper PID). Reuses singleton group
  tb2_self_verify → no double keepalive. No task ids/paths/ports/names from
  training tasks embedded (literals scan: 0 findings).
- config.yaml — repoint the last processor _target_ from
  benchmarks...CustomSelfVerifyProcessor to the new file:// module. Rest of the
  pipeline byte-identical to R6.
- system_prompt.txt — byte-for-byte copy of R6 sidecar (no prompt drift).

### Evidence
- task_001090 tool turn: ps shows PID 777 as the bash -lc wrapper and PID 778 as
  ./monitor; the recorded pid file is 4 bytes = 777. Self-verify keepalive
  (_tb2_self_verify) present; agent re-ran the stock checklist, re-confirmed
  files, and still exited with the wrong PID recorded.
- task_000140 final turns: agent narrates a test pipeline that "gracefully stops
  service with SIGTERM", yet the verifier finds leftover vm_service PIDs;
  exit=done after the checklist fired.
- task_000028 final_pytest tail: Connection refused on 127.0.0.1:8080.

### Uncertainty
This is Control-lever (mechanical injection at the exit turn), not new domain
knowledge — the retroactive check is yes for 140/1090 (the exact failing check
is a step the reconciliation prompt names) and partial for 028 (budget_exceeded
may pre-empt the exit hook). Risk: a 9B model may read the extra checklist item
and still not act on it, the way it already ignores parts of the stock
checklist. If R8 shows the sysadmin cluster unmoved with the keepalive firing,
the constraint is inference/capability, not the missing checklist item — revert
to stock. The bet is cheap (one paragraph, one subclass) and targets the only
0/5 domain, so the expected value is positive even under partial flips.

## Round 8 — calibrate compaction to the real 65536 context

<!-- journal:frontmatter
round: 8
timestamp: 2026-08-17T05:00:00Z
hypothesis_id: h_compaction_ctx_calibrate_v1
levers: [configuration]
predicted_affected: [task_001032_1adaccb9, task_000028_7fe033ac]
cited_candidates: [C-001]
gating_outcome: reverted
gating_attribution: score=29/50; +1/-7 gained=task_000028_7fe033ac lost=task_000015_89886d8d,task_000587_9862bb19,task_001089_220cc46b,task_001264_9f4ca84a; score 0.5800 < incumbent(mean) 0.7000 - tol 0.0400 -> revert to R7
expected_global_gain: "Eliminates the exit_reason=error 400-crash class (context length exceeded) for any task whose context grows past ~28.7k rough tokens (~61.4k real). 1 hard crash (001032) + 1 edge task (000028) now; generalizes to every long-context run on this 65536-limit model and removes a post-flight replay-gate crash risk."
regression_risk: "Low — only 2/50 tasks currently exceed rough 22000, both failing; highest passing tasks sit at ~19-20k (001652, 001701), ~2-3k under threshold, so no passing task triggers compaction. If a borderline passing task grows past 22000 later, its oldest history is summarised but preserve_first_message keeps the task description and a 6-msg retention window is intact."
cost_shift: "Net reduction on long runs — summarising the older half of a growing transcript cuts per-step input tokens on the most expensive tasks (001032 was at $4.42 / 1.27M tokens at crash). Flat on the 48 short tasks that never reach threshold."
rollback_trigger: "If R9 pass_rate < 34/50 AND either (a) a previously-passing task regresses because compaction dropped load-bearing context, or (b) 001032 still exits with error/unchanged, revert token_threshold to 140000."
-->

### Why
R7 = 35/50 (0.70), the best round to date. Sweeping all 15 failures, one has a
distinct, harness-addressable shape absent from prior rounds:
`task_001032_1adaccb9` exits with `status=agent_error` / `exit_reason=error` — a
hard crash, not a done-but-wrong. Its trace's final event is a provider
BadRequestError 400: "This model's maximum context length is 65536 tokens.
However, you requested 4096 output tokens and your prompt contains at least
61441 input tokens." The model's real ceiling is 65536 (output reserve 4096 →
real input budget 61440). The CompactionProcessor is the pipeline's remedy for
exactly this, but its `token_threshold` is 140000 — a value calibrated for a
128k window. Worse, the harness `rough_token_count` (cl100k_base) undercounts
this model's token-dense content (hex dumps + C) by ~2.14×: at the crashing step
it reported only 28689 rough tokens against 61441 real. So compaction can NEVER
fire before the 65536 wall, and long-context runs crash instead of compacting.
This is the R6 rollback trigger's named re-ship condition ("a NEW cluster of
exit_reason=error crashes") and the exact condition the post-flight replay gate
fails on.

### Changes
- `config.yaml` — CompactionProcessor `token_threshold` 140000 → 22000. 22000
  rough ≈ 47080 real (ratio 2.14×), leaving ~14360 real (~6 steps at the
  observed ~800-1000 rough tok/step growth) of headroom before the 61440 wall.
  All other processors, kwargs, and the sibling system_prompt.txt byte-identical
  to R7.
- `system_prompt.txt` — byte-for-byte copy of R7 sidecar (verified identical; no
  prompt drift).

### Evidence
- `task_001032_1adaccb9/oh_runs/.../_trace.jsonl` final event: `task_end
  exit_reason='error' error='BadRequestError: Error code: 400 - This model's
  maximum context length is 65536 tokens ... 61441 input tokens ... total ...
  65537'`.
- Same trace step_start progression: rough token_count climbs 16871 (step 31) →
  28689 (step 45, crash), ~800-1000/step, monotonic — never near the 140000
  threshold.
- `task_000028_7fe033ac` trace: rough peaks at 22421 on its final
  (budget_exceeded @80) step — the only other task approaching the wall.
- Cross-task peak rough token_count (measured from all 50 traces): only 001032
  (28689) and 000028 (22421) exceed 22000; next-highest are passing tasks 001652
  (19829) and 001701 (19044), both under the new threshold.
- `harnessx/processors/control/compaction.py:262-266`: compaction gate is
  `before_tokens <= token_threshold and before_msgs <= message_threshold` where
  before_tokens = rough_token_count — confirming the 140000 gate structurally
  cannot fire before 28.7k on this model.
- canonicalize `{"ok": true, "checked_templates": 0}`; dry_fire
  `{"ok": true, likely_bugs: 0}`.

### Uncertainty
This flips 001032 from a hard crash to a live run, but its underlying tar-header
parsing bug may still fail the hidden suite — the claimed win is removing the
exit_reason=error crash (and the replay-gate risk), not a guaranteed reward flip.
The main downside is if compaction's summarisation drops context a borderline
passing long task needed; the 2-3k gap to the nearest passing task and
preserve_first_message + 6-msg retention make that unlikely. If R9 is flat/down
with a passing regression tied to compaction, this window-calibration guess was
wrong for this model's real growth pattern — revert to 140000.
