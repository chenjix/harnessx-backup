# Evolve journal — tmax-coev-rep25-i1

## Round 1 — add file-edit builtins

<!-- journal:frontmatter
round: 1
timestamp: 2026-09-04T00:56:27Z
hypothesis_id: h_file_tools_v1
levers: [action, instruction]
predicted_affected: [task_000118_3043e92d, task_000015_89886d8d, task_001032_1adaccb9]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=23/50; +5/-7 gained=task_000740_59416444,task_001264_9f4ca84a,task_001498_df8254c9,task_001673_86224c91 lost=task_000536_9c16e8ef,task_000684_1a33ef37,task_000912_770802f8,task_000956_7e92337f; score 0.4600 >= incumbent(mean) 0.5000 - tol 0.0400
expected_global_gain: "Cuts heredoc whole-file-rewrite loops across software_engineering/file_operations clusters; gives a direct, gate-friendly write/edit path so file-authoring tasks stop burning toolcalls on shell quoting."
regression_risk: "New tools shift the action distribution on tasks that already pass with Bash-only; agent could pick Write/Edit where a shell op was cleaner. Low — Bash stays available and the prompt note is soft guidance."
cost_shift: "Expected net token DECREASE on file-heavy tasks (no repeated multi-line heredoc bodies); small prompt-token increase (+1 sentence) on all tasks."
rollback_trigger: "If R2 pass_rate is flat/down AND dup-command counts or edit-warning counts do not fall vs R0, revert the tool-surface change."
-->

### Why

R0 (reward 0.500) failure clusters are dominated by heredoc-based
whole-file rewrites and duplicate-consecutive-command repetition
loops. FAIL clusters showed 190 dup-commands / 28 EditDetection
warnings vs 96 / 1 on PASS. The anchor `task_000118_3043e92d`
(reward 0, 31 toolcalls, 8 heredocs) and `task_000015_89886d8d`
(33 identical `tee ... << PYEOF` calls, a fully stuck loop) both
manifest the same shape: the only file-authoring path is Bash
heredoc, which is token-heavy, quoting-fragile, and trips the
EditDetection guard on body tokens. The Tmax evolve path honors
`tool_registry` from YAML (unlike pure-Harbor TB2), so we can give
the agent real file tools.

### Changes

- `config.yaml` — add builtin `Write`, `Edit`, `Read` to
  `tool_registry.builtin` (alongside existing `Bash`). All route
  through the active sandbox's base64 `write_file`/`read_file`.
- `system_prompt.txt` (sibling of config, read by
  `SiblingSystemPromptBuilder`) — one general sentence steering the
  agent to prefer Write for whole files / Edit for targeted changes
  / Read for inspection over shell heredocs. General strategy, no
  task-specific literals.

### Evidence

- `task_000118_3043e92d`: reward 0, 31 toolcalls, 8 heredocs;
  EditDetection fired on garbage body tokens (`=`, `SIZE_THRESHOLD:`)
  instead of the real target `/home/user/deployment_monitor.py`.
- `task_000015_89886d8d` (software_engineering, FAIL): all 33
  toolcalls identical `tee /tmp/ocr.py << 'PYEOF'`, 32 dups, 6 edit
  warnings — stuck loop.
- `task_001032_1adaccb9` (file_operations, FAIL): 10 heredocs, 2
  warnings.
- R0 config already contains `LengthTruncationRecoveryProcessor`
  (repeat recovery) yet loops persisted → the missing mechanism is
  the write ergonomics, not just loop nudging.

### Uncertainty

The direct tools address write ergonomics but may not fully break
the repetition-loop failure mode (the stronger FAIL discriminator);
the existing LengthTruncationRecoveryProcessor is the only loop
guard and it did not prevent R0 loops. If dup-command counts stay
high in R2 despite the new tools, the next round should target loop
detection directly (a stuck-command interceptor) rather than tool
surface. Validated: canonicalize ok, dry_fire ok (0 likely bugs),
literals ok (0 findings).

## Round 2 — stuck-command recovery guard

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-04T02:00:00Z
hypothesis_id: h_stuck_cmd_recovery_v1
levers: [control]
predicted_affected: [task_001818_b251e5ea, task_001701_95e3bbcb, task_001673_86224c91]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=29/50; +10/-4 gained=task_000329_a3ac56b0,task_000338_27d6a1be,task_000536_9c16e8ef,task_000684_1a33ef37 lost=task_000740_59416444,task_001089_220cc46b,task_001536_acfe6c35,task_001706_24462a09; score 0.5800 >= incumbent(mean) 0.5800 - tol 0.0400
expected_global_gain: "Closes the same-command/same-error retry-loop cluster that dominates the highest-error failing trajectories (exit=error / budget_exceeded with 13-32 duplicate commands); surfaces the stuck command + recurring error in-context so the agent changes approach instead of burning budget. Generalises across domains (rust/C build loops, sqlite query loops, server bring-up loops)."
regression_risk: "Nudge fires only on the Nth IDENTICAL (command,error) pair, never on success or a new error, capped at max_nudges=3/task; passing fast cluster (short steps, 0 non-zero exits) never trips it. Worst case: one extra user message on a task about to self-correct on its 3rd try."
cost_shift: "Net token DECREASE on the loop cluster (cuts ~15-25 wasted repeat steps on 3+ tasks); ~+3 short messages only on tasks that actually loop; zero change on passing tasks."
rollback_trigger: "If R3 pass_rate is flat/down AND duplicate-command / non-zero-exit counts on the predicted tasks do not fall vs R0, revert the processor."
-->

### Why

Assigned focus: recovery from tool errors. Sweeping R0 fails (25/50)
for non-zero-exit + duplicate-command co-occurrence surfaced a tight
cluster where the agent re-issues a command that keeps failing the
SAME way and never changes approach: task_001818 (12 non-zero exits,
32 dups, exit=error), task_001701 (11 errors, 13 dups, budget), and
task_001673 (7 errors, 18 dups, budget). The observable state is a
same-normalised-command / same-error-fingerprint repetition. R0's
existing guards do not cover it: CustomEditToolProcessor counts file
over-edits (write-count, ignores exit status) and
LengthTruncationRecoveryProcessor only handles finish_reason=length.
The anchor task_000010 is partial evidence (its "recovery" was a
requirement-violating file rename rather than a blind repeat), so the
cluster stands on the three loop tasks.

### Changes

- `processors/stuck_command_recovery.py` — new MultiHookProcessor
  StuckCommandRecoveryProcessor: tracks (normalised command, error
  fingerprint) across tool results; after repeat_threshold consecutive
  identical failures injects ONE legible loop-break note (names the
  repeated command + recurring error, instructs inspect-inputs /
  change-invocation / isolate-failing-piece), then arms a cooldown
  until the command signature changes. Bounded by max_nudges.
- `config.yaml` — register the processor (repeat_threshold=3,
  max_nudges=3) just before CustomEditToolProcessor.
- `system_prompt.txt` — copied byte-for-byte from R0 (SiblingSystemPromptBuilder).

### Evidence

- task_001818 steps 33-65: repeated heredoc write of the rust source +
  `cargo build --release` producing cargo errors
  E0282/E0308/E0277/E0425 each cycle; [EditDetection] fires at step
  53/75 but is ignored (write-count based).
- task_001673 steps 25-60: verbatim cycle (start flask server &, urllib
  request returning {"cost":null}, sqlite recursive-CTE returning the
  same A|A|0.0|A) re-run ~4x, budget exhausted.
- task_001701 steps 2-45: jshon invocation on a nested JSON object
  returning the same "parse error: type 'object' is not
  simple/printable" re-issued at 2/4/6 and again 35/45 — invocation
  never changes.

### Uncertainty

The nudge is advisory: a weak model may ignore it just as it ignored
the EditDetection warning. Mitigation is specificity — it quotes the
exact repeated command and recurring error rather than a generic
"step back". If R3 shows the dup-command / non-zero-exit counts on the
predicted tasks unchanged, the next round should escalate to a harder
intervention (e.g. block the identical re-execution outright).

## Round 2 — synthesis: verify-gate ⊕ exact-repeat loop guard

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-04T02:30:00Z
hypothesis_id: h_synth_verifygate_exactloop_v1
levers: [control, configuration]
predicted_affected: [task_000740_59416444, task_001264_9f4ca84a, task_001498_df8254c9, task_001673_86224c91, task_001706_24462a09, task_000338_27d6a1be]
cited_candidates: [C-001, C-002]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Combines the two live lineages on their COMPLEMENTARY real-outcome gains: c4's IterativeVerifyGate flips 5 R0 failures (740,1264,1498,1673,1706) via bounded work-aware verification; c1's exact-repeat loop guard flips 338 by breaking a byte-identical Bash loop. Both target orthogonal failure classes (premature unverified exit vs degenerate repetition), so the union should hold ~+5/+6 on real (status=ok) outcomes."
regression_risk: "Verify-gate can over-nudge an already-correct solution into churn (c4's one real loss, 965) — bounded by max_nudges=2 and further guarded by the exact-repeat detector. Loop-raise could cut a task that legitimately repeats an identical call >=12x — threshold 12 sits above the max transient-recover run (11) AND above every c4 gain's max exact-identical run (<=9), so none are at risk. c1's own two real losses (1536,1652) came from the name-only Strategy-2 warn firing on normal Bash-only long-horizon work; that strategy is DISABLED here (name_warn_threshold=100000), repairing that regression."
cost_shift: "Net roughly flat-to-down: exact-repeat loops are cut early (token DECREASE on stuck runs); verify-gate adds a bounded <=2-nudge INCREASE only on tasks that try to exit unverified. No per-step cost added by the loop guard (warning appended to an existing tool result)."
rollback_trigger: "If R3 real (status=ok) pass_rate is below R0's status=ok baseline (25/50 minus infra errors) AND neither the exact-repeat loop cluster nor the verified-completion cluster flips as predicted, revert to the c4-only lineage (IterativeVerifyGate without LoopDetection)."
-->

### Why

Assigned focus: complementary synthesis of the live pivots. The pivot
table's headline scores (c4=0.46, c1=0.38 vs R0=0.50) are misleading: most
reported "unique losses" are `status: error` docker container-name
conflicts (parallel-scheduling infra flakes), NOT harness regressions.
Re-reading every differing task's `result.json` `status` and filtering to
`status: ok` (real agent outcomes) shows c4 is net **+4** (best lineage)
and c1 net **+2**. The two lineages are genuinely complementary: c4's
IterativeVerifyGate uniquely flips 1264 and 1706 and shares 740/1498/1673;
c1's exact-repeat loop guard uniquely flips 338. This round keeps c4 as the
base and grafts c1's *exact-repeat* mechanism, while neutralising the
sub-mechanism (name-only warn) responsible for c1's only real regressions.

### Changes

- `config.yaml` — start from the c4 lineage (IterativeVerifyGate replaces
  stock CustomSelfVerifyProcessor, max_nudges=2). Add stock
  `LoopDetectionProcessor` (window=16, warn=4, threshold=12) with
  `name_warn_threshold=100000` to DISABLE Strategy 2 (name-only warn),
  which is noise on this Bash-only benchmark.
- `processors/iterative_verify_gate.py` — copied from the c4 lineage into
  this proposal's dir so the config is self-contained (own `file://` path).
- `system_prompt.txt` — copied byte-for-byte from R0.

### Evidence

- task_000338 (c1 traj): tool result carries `[LoopDetection] ⚠️ The exact
  same tool call(s) have been issued 10 times in a row (Bash)`; next
  assistant turn stops looping and commits the correct kernel → reward 1.
  R0 run ended on the repeated call with the wrong OCR result → reward 0.
- task_000965 (c4 traj): R0 solved it (correct variance, 30 msgs); c4's
  verify-gate re-nudged an already-correct solution, agent over-edited
  Welford across 72 msgs and broke variance to 0.0 → the one real c4 loss.
  max_nudges=2 + the exact-repeat guard bound this.
- task_001536 / task_001652 (c1 traj): both carry the name-only warn
  (`called Bash N times consecutively with different arguments`) on
  legitimate multi-step work; agent then prematurely quit
  (38/64 msgs vs R0's passing 76/88) → c1's two real losses. Disabling
  Strategy 2 removes this failure mode.
- c4 gains max exact-identical consecutive Bash run (verified by
  reconstructing fingerprints): 740=1, 1264=1, 1498=1, 1673=7, 1706=9 —
  all below the raise threshold 12, so the loop guard cannot cut them.

### Uncertainty

The two mechanisms have not been observed running together, so an
interaction I can't see in single-lineage trajectories is possible (e.g.
the verify-gate nudge inducing a re-edit loop that the raise cuts before
completion). max_nudges=2 and threshold=12 are both conservative. If R3's
status=ok pass_rate does not clear R0's status=ok baseline, revert to the
c4-only lineage per the rollback trigger.

## Round 3 — stuck-reasoning loop guard

<!-- journal:frontmatter
round: 3
timestamp: 2026-09-04T04:20:00Z
hypothesis_id: h_stuck_reasoning_recovery_v1
levers: [control]
predicted_affected: [task_001031_a8f0eb37, task_000015_89886d8d, task_000140_01c78b42, task_000329_a3ac56b0, task_001818_b251e5ea, task_001321_658ce4a8]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the reasoning-repetition failure cluster (6 R0 fails, domain-diverse: MPI/numpy, OCR/tesseract, shell-pipeline, DB), where the agent emits byte-identical assistant narration 14-24 turns in a row even when the tool returns SUCCESS each turn (a shape the length- and error/command-fingerprint guards structurally miss). Two of the six exit agent_error, the hardest failure class."
regression_risk: "Advisory user-message nudge, never blocks/rewrites tool calls, capped max_nudges=3/task, armed once per distinct stuck content. repeat_threshold=12 sits above every passing task's MID-TASK identical run (task_001591 run 11, task_001652 run 10). The only passing tasks with runs at/above 12 (task_000024 run 22, task_001781 run 20) loop AFTER work is banked (post-completion verify-tail) so a nudge there is harmless."
cost_shift: "Net token DECREASE on the loop cluster (cuts 10-20 wasted repeat turns on the failing cluster plus the passing verify-tails); at most 3 short user messages only on tasks that loop 12-plus times; zero change on other passing tasks."
rollback_trigger: "If R4 pass_rate is flat/down AND the longest identical-assistant-run on the six predicted tasks does not fall vs R0, revert the processor."
-->

### Why

Assigned focus: recovery from tool errors. The anchor task_000010 is a
1-task shape (self-created operator.py shadows stdlib operator giving an
ImportError; its "recovery" renamed the required file away, violating the
path requirement) — per the generalization contract that earns a no-op, not a
special case. Tracing the anchor's observable failure ("agent hit an error
and its recovery attempt did not change the outcome") led to a recurring,
domain-diverse cluster: the agent emits the SAME assistant reasoning on
consecutive turns — often explicitly narrating "I keep getting the same
error / I've been stuck in a loop / let me try a fundamentally different
approach" — yet reproduces the identical narration and action. The agent has
recognised it is stuck but cannot self-break; its recovery IS the repeat.

### Changes

- `processors/stuck_reasoning_recovery.py` — new MultiHookProcessor
  StuckReasoningRecoveryProcessor: tracks whitespace-normalised assistant
  content; after repeat_threshold identical consecutive turns injects ONE
  legible change-approach directive before the next model call, with a
  cooldown until content changes and a max_nudges cap.
- `config.yaml` — register it (repeat_threshold=12, max_nudges=3,
  min_chars=24) just after LengthTruncationRecoveryProcessor. Distinct
  trigger from that guard (finish_reason=length) and from any
  error/command-fingerprint guard (keys on repeated reasoning even when the
  tool result is a SUCCESS).
- `system_prompt.txt` — copied byte-for-byte from R0 (SiblingSystemPromptBuilder).

### Evidence

- task_001031 msgs 87-94: assistant emits verbatim "I keep making the same
  mistake. Let me try a completely different approach - use comm.Allgatherv
  with a flat array..." 24 times; each tool reply is "Script created" (exit 0,
  SUCCESS), Bash arg head identical across msgs 71-93; then exit_reason=error.
- task_000015 (33 asst msgs, top repeat 28 times): "I keep getting the same
  error. Let me try a fundamentally different approach - using a Python script
  to call tesseract..."
- task_000140 (top repeat 22 times): "I keep hitting the token limit when
  trying to read the test_pipeline.sh file. Let me try a different approach..."
- task_001321 (top repeat 14 times): "I've been stuck in a loop trying to run
  the same command. The shell is not capturing output properly..."
- Passing-cluster guard: task_001591 (pass) mid-task run 11 at fraction 0.47;
  task_001652 (pass) run 10 at 0.39 — both below threshold 12. task_000024
  (pass) run 22 and task_001781 (pass) run 20 are post-completion verify-tails
  ("the task is complete, let me provide a final summary").

### Uncertainty

The nudge is advisory: a weak model may ignore it as it ignored its own
stuck-state narration. Mitigation is specificity (names that the prior
approach produced no change and demands one concrete different action).
threshold=12 is conservative to protect passing tasks; if R4 shows the
predicted tasks' identical-run lengths unchanged, escalate to blocking the
identical re-execution outright rather than nudging.

## Round 4 — synthesis: adopt best live lineage (guard is inert)

<!-- journal:frontmatter
round: 4
timestamp: 2026-09-04T06:00:00Z
hypothesis_id: h_synth_adopt_stuck_reasoning_lineage_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_000015_89886d8d, task_001031_a8f0eb37, task_001818_b251e5ea]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Holds the 0.580 incumbent lineage (29 of 50) instead of reverting to R0's 0.500-labelled baseline, in a self-contained config. No new flips claimed - honest reading is the reasoning-loop guard is INERT on this batch (fired 0 of 50), so realistic gain is parity-with-best-live-lineage plus no regressions introduced."
regression_risk: "Effectively zero vs the incumbent: config is functionally identical to n1/n2 (same StuckReasoningRecoveryProcessor, same threshold 12, same sibling prompt), relocated to a self-owned file path. Because the guard never fires on this batch it cannot change any task outcome vs the incumbent."
cost_shift: "Zero vs incumbent (identical pipeline; guard adds no per-step cost and fires zero times)."
rollback_trigger: "If R5 pass_rate falls below R0's 25 of 50 status=ok baseline AND the reasoning-loop guard still fires 0 times, revert to R0 (drop the inert guard)."
-->

### Why

Assigned focus: complementary synthesis of the live pivots. Reading every
pivot: n1 (R2/c0) and n2 (R2) config.yaml are BYTE-IDENTICAL and share one
trajectory dir, so there are only two distinct harnesses, R0 and
"R0 plus StuckReasoningRecoveryProcessor". current_config points at R0. The
per-task diff is R0=25/50 vs r1-fe-c0=29/50 (all status=ok): +6 gains, -2
losses (net +4). The decisive attribution finding: the StuckReasoning nudge
("LOOP DETECTED" / "STILL LOOPING") fired ZERO times across all 50 tasks in the
r1-fe-c0 run, including on every gained and every lost task. So the +6/-2 swing
is entirely run-to-run variance, not a mechanism effect; the guard's
repeat_threshold=12 is never reached because LengthTruncationRecovery
(threshold=2) breaks the common loops first. The synthesis focus (graft a
complementary mechanism to repair the winning lineage's unique losses) is
therefore unsupported: the two losses (1089, 1536) are stochastic correctness
differences the inert guard did not cause and no harness lever attributably
repairs. Per the generalization contract, that earns adopting the best live
lineage as-is rather than inventing a special case.

### Changes

- config.yaml - adopt the incumbent 0.580 lineage: R0 pipeline plus
  StuckReasoningRecoveryProcessor (repeat_threshold=12, max_nudges=3,
  min_chars=24). Repointed the processor's file path to a LOCAL copy so the
  config is self-contained.
- processors/stuck_reasoning_recovery.py - copied verbatim from the R2/c0
  lineage into this proposal's dir.
- system_prompt.txt - copied byte-for-byte from R0 (SiblingSystemPromptBuilder
  reads the sibling next to the active config).

### Evidence

- n1 (R2/c0) and n2 (R2) config.yaml read in full: identical; only structural
  delta vs R0 is the StuckReasoning processor at threshold=12.
- Zero-fire attribution: grep count of "LOOP DETECTED / STILL LOOPING" = 0 on
  all 50 r1-fe-c0 message logs, incl. gains (329,338,1032,1264,1498,1673) and
  losses (1089,1536).
- task_001089 (lost, stochastic): r1-fe-c0 ran to clean completion (exit 0,
  full summary) yet verifier scored 0; R0 actually looped ("cut off by token
  limit", identical narration) but banked reward 1. Nudge inert both runs.
- task_001536 (lost, stochastic): r1-fe-c0 ended mid-debugging cyclic-symlink
  logic (65 msgs, still failing); R0 finished correctly (76 msgs). Nudge inert.
- Threshold-lowering rejected: observed max identical-assistant run = 12; the
  two PASSING tasks with long runs, task_000344 (run 12 at frac 0.45) and
  task_001591 (run 11 at frac 0.76), loop MID-TASK then self-recover to reward
  1, so a lower threshold would nudge them at real 2-task regression risk for an
  advisory-only, low-yield upside. Pareto-negative on current evidence.

### Uncertainty

The guard is inert on the current batch, so this round is essentially a
self-contained promotion of the best live lineage; its benchmark value over R0
rests on stochastic variance and cannot be claimed as causal. If a future round
wants to actually engage the real failing loop cluster (6 tasks with long
identical-runs), it should gate a threshold sweep as its own bet with the two
passing long-loop tasks (000344, 001591) as the explicit regression probe, not
fold it into a synthesis slot. If R5 shows the guard still fires 0 times and
pass_rate does not clear R0's baseline, drop the inert guard entirely.

## Round 5 — recurring hard-error recovery guard

<!-- journal:frontmatter
round: 5
timestamp: 2026-09-04T07:30:00Z
hypothesis_id: h_recurring_error_recovery_v1
levers: [control]
predicted_affected: [task_001701_95e3bbcb, task_001818_b251e5ea]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Engages the error-thrash-WITH-VARIATION cluster the inert reasoning guard structurally misses: agent keeps hitting the SAME hard error while varying the command each attempt (consecutive-identical run only 3-4). Keys on a session-wide recurring hard-error fingerprint via on_after_tool; verified to FIRE on the two doomed thrash tasks (001701 recur=10, 001818 recur=9) and on ZERO passing tasks. Generalises across domains (rust build loops, JSON-parse loops, DB-query loops)."
regression_risk: "Advisory user message only; never blocks/rewrites tool calls; max_nudges=2; per-fingerprint cooldown; recur_threshold=5 sits strictly above the max recurring hard-error count on any passing trajectory (3, verified over all 50 R0 trajectories with the shipped _fingerprint code). Benign lines (no-output-captured successes, warnings, one-off command-not-found probes, self-verify probes) excluded so the high-recurrence PASS false-positives (001652 13x, 000344 7x, 001591 5x) do NOT count. on_before_model replaces a trailing user message rather than +2-inserting (contract-clean)."
cost_shift: "Net token DECREASE on the thrash cluster (cuts ~15-30 wasted retry cycles on 2+ tasks); +<=2 short messages only on tasks that thrash >=5x on one error; zero change on every passing task (guard never fires there)."
rollback_trigger: "If R6 pass_rate is flat/down AND the recurring-hard-error count on task_001701 / task_001818 does not fall vs R0, revert the RecurringErrorRecoveryProcessor (keep the 0.580 reasoning-guard base)."
-->

### Why

Assigned focus: recovery from tool errors; anchor task_000010. The anchor is a
1-task shape (a self-created /home/user/operator.py shadows the stdlib operator
module, breaking the interpreter; its "recovery" renamed the required file away
then back, re-poisoning the final state). The shadow-module signature appears in
EXACTLY ONE of the 50 trajectories, so per the generalization contract it earns
a no-op, not a special case. Tracing the anchor's observable class ("hit an
error; the recovery attempt did not change the outcome") to the wider set
surfaced the real, domain-diverse cluster the pipeline does NOT yet cover:
error-thrash WITH VARIATION. The agent keeps hitting the SAME hard error
(traceback / parse error / *Error: / OperationalError) while varying the
surrounding command each attempt, so no two consecutive turns are identical and
every existing guard misses it: LengthTruncationRecovery (finish_reason=length),
StuckReasoningRecovery (consecutive-identical narration; R4 found it fires 0/50
on this batch), CustomEditToolProcessor (write-count). The R2 stuck-command
guard keyed on (command,error) identical pairs, which also misses this because
the command varies.

### Changes

- processors/recurring_error_recovery.py - new MultiHookProcessor
  RecurringErrorRecoveryProcessor: on on_after_tool it extracts a normalised
  hard-error fingerprint (paths/numbers/hex stripped, benign lines excluded),
  counts it session-wide, and once any fingerprint recurs recur_threshold times
  injects ONE legible recovery directive naming the recurring error and
  demanding a genuinely different diagnostic move (read full error / re-verify
  inputs from scratch / isolate smallest failing piece). Per-fingerprint
  cooldown; bounded by max_nudges.
- config.yaml - adopt the best live lineage (R0 + StuckReasoningRecoveryProcessor
  at threshold 12, self-contained local copy) as the base and register the new
  processor (recur_threshold=5, max_nudges=2) right after it. current_config is
  R0 (0.500); the base is the 0.580 lineage.
- system_prompt.txt - copied byte-for-byte from R0 (SiblingSystemPromptBuilder).

### Evidence

- task_001701 (security, budget_exceeded): `parse error: type 'object' is not
  simple/printable` recurs 10x across msgs 3-69 with varied jshon/make/edit
  commands between; agent circles back to the identical error at 46-54 and 69.
- task_001818 (data_processing, exit=error): rust `error: could not compile
  ticket_processor` recurs 9x across recompile cycles.
- Retroactive replay of the shipped _fingerprint counter over all 50 R0
  trajectories: WOULD FIRE (recur>=5) on exactly task_001701 (10) and
  task_001818 (9), both reward=0. Max recurring hard-error count on any PASSING
  task = 3; zero passers reach 4. recur_threshold=5 therefore fires on the
  failing cluster and no observed passing task.
- Passing false-positive guard verified: task_001652 (13x "no output captured"),
  task_000344 (7x self-verify probe), task_001591 (5x intentional
  JSONDecodeError) are all EXCLUDED by the benign filter and do not count.

### Uncertainty

The nudge is advisory: a weak model may ignore it as it ignored its own error
recurrence. Mitigation is specificity (quotes the exact recurring error and
demands one concrete different diagnostic). The fingerprint heuristic could
miss an error phrased in an unusual way or over-collapse two distinct errors; if
R6 shows the recurring-error counts on the two predicted tasks unchanged,
escalate to a harder intervention (e.g. after N recurrences, force a
state-inspection step rather than nudging). Validated: canonicalize ok,
dry_fire 0 bugs, contract 0 violations, literals 0 findings.
