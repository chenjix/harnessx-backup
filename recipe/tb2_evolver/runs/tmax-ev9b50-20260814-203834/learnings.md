# Evolve journal — tmax-ev9b50-20260814-203834

## Round 1 — break max_tokens repetition loop

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-14T21:30:00Z
hypothesis_id: h_length_recovery_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_000015_89886d8d, task_000118_3043e92d, task_000264_ab8c7253, task_001032_1adaccb9, task_001321_658ce4a8, task_001652_86e1d185]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=31/50; +2/-1 gained=task_001264_9f4ca84a,task_001652_86e1d185 lost=task_001818_b251e5ea; score 0.6200 >= incumbent(mean) 0.6000 - tol 0.0400
expected_global_gain: "Flip some of the 7 agent_error tasks (largest homogeneous failing cluster, cross-domain) by breaking the degenerate max_tokens repetition loop"
regression_risk: "None expected on the 30 passing tasks — processor is a no-op on any turn that ends normally or with a tool call; only fires on finish_reason=length with no tool call"
cost_shift: "Strongly negative — eliminates 160K-330K-char runaway output blobs and repeated max_tokens generations on the crashed tasks"
rollback_trigger: "If R2 regresses any of the 30 R0-passing tasks, or the agent_error count does not drop, revert the processor"
-->

### Why

R0 scored 30/50 (60%). Of the 20 failures, 7 share one exact mechanism:
`status=agent_error`, `agent.exit_reason=error`, and very high `elapsed_s`
(352–1160s). Every one of these 7 tasks — and no others — contains the run
loop's user nudge "Your previous response was cut off by the token limit.
Please continue from where you left off." The mechanism: the small model
(Qwen3.5-9B) enters a degenerate repetition loop, emitting the same narration
paragraph hundreds of times in the assistant `content` field with no tool
call until it hits `max_tokens` (`finish_reason=length`). The run loop's
`runloop.py` then appends a *passive* "please continue" nudge, which merely
re-primes the same runaway generation. The cycle repeats until the container
errors out. This cuts across system_administration, data_querying,
software_engineering, security, and data_processing domains — a harness
deficiency, not a domain-knowledge gap.

The remaining 13 failures are heterogeneous capability gaps (wrong recursive
SQL / index creation in task_000264, wrong output paths, domain logic errors)
with no shared harness lever — no harness fix; skip.

### Changes

- `processors/length_recovery.py` — new `LengthTruncationRecoveryProcessor`
  (Control). `on_after_model`: on `finish_reason=length` with no tool call,
  collapse runaway `content` to a head+tail excerpt and arm a corrective
  nudge; reset streak on any normal/tool-calling turn. `on_before_model`:
  append exactly one `user` message redirecting the model to stop narrating
  and issue a single concrete Bash command, escalating on consecutive
  truncations.
- `config.yaml` — register the processor (`_order=5`, early in the
  after-model chain) via absolute `file://` path, between
  `TaskTimeReminderProcessor` and `CompactionProcessor`.

### Evidence

- `task_000015_89886d8d` step 25: assistant `content` length = 286,609,
  `tool_calls=0` — the same "Actually, let me try a different approach ..."
  paragraph repeated hundreds of times to the token cap; followed by the
  "cut off by the token limit" nudge then another over-length turn → error.
- `task_000264_ab8c7253` step 37: `content` length = 328,921, `tool_calls=0`;
  two "cut off ... continue" nudges precede `agent.exit_reason=error`.
- `task_001032_1adaccb9` step 45: `content` length = 166,154, `tool_calls=0`;
  nudge-then-relapse → agent_error.
- Perfect correlation: the 7 tasks containing the "cut off by the token limit"
  nudge are exactly the 7 `status=agent_error` failures.

### Uncertainty

Breaking the loop resumes productive tool calls, but does not guarantee the
underlying task is then solved correctly (some of the 7 also had upstream
logic gaps, e.g. task_000264's SQL). Expected outcome is partial recovery —
crashes become normal runs, and a subset flip to pass. If R2 shows no drop in
agent_error count, the redirect nudge is not strong enough / fires too late —
revert and reconsider (e.g. hard-terminate the turn on repeat rather than
nudge). Watched signal: agent_error count and pass-rate on the 30 R0-passing
tasks.

## Round 2 — break identical-command loop

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-14T23:10:00Z
hypothesis_id: h_repeated_command_breaker_v1
levers: [control]
predicted_affected: [task_001818_b251e5ea, task_000010_644ab1c2, task_000118_3043e92d, task_001031_a8f0eb37, task_000396_e56917e2]
cited_candidates: [C-002]
gating_outcome: accepted
gating_attribution: score=30/50; +1/-2 gained=task_001818_b251e5ea lost=task_001090_c61c71f2,task_001515_eed714e6; score 0.6000 >= incumbent(mean) 0.6200 - tol 0.0400
expected_global_gain: "Close the new dominant R1 failing cluster — degenerate identical-command loops that burn the whole step/wall budget (1000-4300s) across system_administration, data_processing, scientific_computing, data_querying"
regression_risk: "Low — pure no-op unless the same normalized Bash command repeats 3+ times consecutively; soft nudge at streak 3, hard nudge at 4, execution suppression only at streak 5+. Passing runs adapt between commands so they never trip it."
cost_shift: "Strongly negative — eliminates thousands of seconds and dozens of wasted identical tool calls on runaway failing tasks."
rollback_trigger: "If R3 regresses any of the 31 R1-passing tasks, or the runaway-elapsed failing tasks do not drop in elapsed_s / step count, revert the processor."
-->

### Why

R1's LengthTruncationRecoveryProcessor worked as intended: the
status=agent_error (finish_reason=length crash) cluster dropped from 7 to 2,
and R1 scored 31/50. But a distinct failure shape now dominates the slow
failures: several tasks end each turn normally (a well-formed Bash tool call,
not a length truncation) yet issue the SAME command over and over without ever
reacting to the result. They grind to the ~33-step cap and time out with reward
0 (task_000264 4307s, task_001818 3144s, task_000118 1931s, task_001031 1433s,
task_000010 964s). The existing CustomEditToolProcessor emits a passive
EditDetection text warning on over-writes, but the model ignores it and keeps
looping. This is a cross-domain harness deficiency, not a domain-knowledge gap.

### Changes

- processors/repeat_command_breaker.py — new RepeatedCommandBreakerProcessor
  (Control, order=40, after EditDetection, before self-verify). on_before_tool:
  normalize each Bash command (whitespace-collapse) and count consecutive
  identical repeats; at streak 3+ arm a soft redirect nudge, at 4+ a hard one,
  and at 5+ suppress the execution via approved=False, synthetic_result so the
  loop physically cannot continue. on_before_model: append exactly one user
  redirect (contract-safe +1). Streak resets on any different command / task
  boundary.
- config.yaml — register the processor via absolute file:// path.

### Evidence

- task_001818 msgs 6-68: identical 3238-char main.rs heredoc written ~20x
  consecutively, each exit 0; EditDetection warns at msgs 21/23/37/39/53/56 and
  is ignored. Dup ratio 0.89 (26 of 28 commands identical).
- task_000010 msgs 2-15: identical socket port-probe issued 4x, ps aux grep 5x,
  cycling start/kill/check with no adaptation.
- task_001031: identical mpiexec -n 4 run 3x with no code change between.

### Uncertainty

Breaking the loop reclaims budget and forces a different action, but does not
guarantee the task is then solved (task_001818 still had an unaddressed E0308
mismatched-types compiler error; task_000264 has an upstream SQL logic gap).
Expected outcome is partial recovery: runaway grinds become bounded runs, a
subset flip to pass, elapsed_s/step counts drop sharply. If R3 shows no drop in
elapsed_s on the runaway cluster, thresholds fire too late — lower
suppress_threshold or revert.

## Round 3 — no-op: remaining failures are capability gaps, control lever exhausted

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-15T00:30:00Z
hypothesis_id: h_noop_capability_gaps_v1
levers: []
predicted_affected: []
gating_outcome: reverted
gating_attribution: score=27/50; +1/-4 gained=task_001515_eed714e6 lost=task_001498_df8254c9,task_001536_acfe6c35,task_001706_24462a09,task_001818_b251e5ea; score 0.5400 < incumbent(mean) 0.6200 - tol 0.0400 -> revert to R1
expected_global_gain: "None claimed — no harness lever plausibly flips a failing cluster this round; explicit no-op protects the 30 passing tasks from a speculative regression."
regression_risk: "None — config is byte-identical to R2 (which passed replay). R1+R2 processors still resolve at their absolute file:// paths."
cost_shift: "Zero — no config change."
rollback_trigger: "N/A (no-op). Next round should revisit only if a NEW, non-capability harness signal appears."
-->

### Why

R2 scored 30/50 (60%), flat vs R0 (30) and down 1 from R1 (31). I swept all
50 trajectories (frontmatter + bodies of every failing task and a sample of
passing ones). The 20 failures decompose into two groups, neither of which a
harness lever can flip this round:

1. Catastrophic runaway/crash tasks (task_000010 3119s, task_001031 4317s,
   task_001032 4538s, task_000015 5219s agent_error). These are the tasks the
   R1 length-recovery and R2 repeat-command-breaker were built for. Body reading
   shows the loop is now a symptom of an upstream capability gap, not the
   blocker itself: task_001031 = MPI send/recv deadlock the model cannot debug
   (it re-emits an identical heredoc write 14x and RepeatGuard fired 138 nudges +
   suppression, which the model simply ignored across 2 sessions); task_000010 =
   k8s port-forward/socat/zombie-process management + a path mismatch (wrote the
   operator to the wrong filename vs the required path); task_001032 = ustar
   header parsing the model gets wrong. The Variant-A retroactive check for
   another loop-breaker is NO — breaking the loop harder does not supply the
   missing capability, so it cannot flip these. (analyze skill: "a tighter loop
   detector requires the loop to be the actual blocker, not the consequence of
   the real blocker.")

2. Short multi-requirement failures (system_administration 0/5, plus scattered
   data_science / software_engineering). Every one opens with the SAME habit the
   passing cluster uses — "Let me break down the task: 1... 2... 3..." then
   survey with find/ls/cat — so the plan/survey Instruction lever is already
   saturated (Variant-B/C check: habit present in BOTH pass and fail clusters, so
   encoding it is dead weight). They also all receive the existing
   CustomSelfVerifyProcessor checklist (fired 2x, SUCCESS marker emitted 2x) and
   run extra ls/cat verification, yet still fail eval because their
   self-verification is superficial (existence, not semantic correctness against
   every sub-requirement). That is a model capability gap — per SOUL, not the
   harness's job; more instruction around a missing capability compounds failure
   (analyze skill: Capability-gap / Instruction is "almost always wrong").

### Changes

- `config.yaml` — byte-identical copy of R2 config (explicit no-op).
  `system_prompt.txt` sidecar copied unchanged so SiblingSystemPromptBuilder
  resolves next to the new config.

### Evidence

- task_001031_a8f0eb37: identical heredoc-write command (2364 chars) issued 14x;
  RepeatGuard nudges fired 138x, byte-identical suppression fired, model looped
  anyway across 2 oh_runs sessions. Root cause in raw_assistant content: "the
  send/recv pattern is blocking" (MPI deadlock).
- task_000010_644ab1c2: cycles pkill/ps/socat/mock_api among ~8 distinct
  commands (RepeatGuard fired 0x — not byte-identical), wrote operator to wrong
  filename vs required path. Genuine multi-part sysadmin task + capability gap.
- task_000015_89886d8d: status=agent_error, 73 steps, 5219s; length-recovery
  "stop narrating" nudge fired but the model kept length-truncating until the
  container errored — reward 0 either way.
- task_000028_7fe033ac, task_000140_01c78b42, task_001090_c61c71f2,
  task_001653_c4cafa73: all open with "Let me break down the task: 1... 2..."
  (habit present) and end with a confident "task complete / all requirements
  verified" + SUCCESS marker, yet fail eval on semantic correctness.
- Passing cluster (task_000024, task_000344, task_000818, task_001536) uses the
  identical opening habit — confirming the habit is not the differentiator;
  correctness is.
- R1->R2 regressions (task_001090, task_001515) had RepeatGuard fire 0x — they
  are run-to-run variance, not processor-induced; the R2 processor is not
  actively harming any passing task.

### Uncertainty

The honest risk of a no-op is under-spending on a live signal. I judged that a
hard step/wall-clock circuit breaker (the only remaining low-effort control
move) carries real regression risk against currently-PASSING slow tasks
(task_001701 passes at 1024s, task_001818 passes at 1484s) while flipping zero
failing tasks — a negative-Pareto trade the auto-revert gate would reject. If a
future round finds a NEW harness-shaped signal (e.g. a tool-return-shape gap, or
evidence that a specific slow-but-passing task would tolerate a bounded budget),
that is the place to spend. This round protects the 30 passing tasks and
declines to burn a round on an exhausted lever.

### needs_from_human

The dominant remaining failure driver on this task set is a model capability gap
(MPI concurrency debugging, ustar parsing, multi-service sysadmin orchestration,
rigorous self-verification of multi-requirement solutions) on a 9B model, not a
harness deficiency. Further pass-rate gains here likely require a stronger base
model or task-specific tooling that a general harness cannot supply without
memorising tasks. Flag for human: consider whether the eval set's expected
ceiling for Qwen3.5-9B is meaningfully above 60% given these gaps.

## Round 5 — budget-reclaiming loop guard (total-repeat, with suppression)

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-16T06:00:00Z
hypothesis_id: h_loop_budget_guard_total_repeat_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_000863_7acceb19, task_001032_1adaccb9, task_001652_86e1d185, task_001031_a8f0eb37]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=28/50; +3/-3 gained=task_000863_7acceb19,task_001536_acfe6c35,task_001652_86e1d185 lost=task_001090_c61c71f2,task_001701_95e3bbcb,task_001818_b251e5ea; score 0.5600 >= incumbent(mean) 0.5900 - tol 0.0400
expected_global_gain: "Flip a subset of the 8 R4 budget_exceeded failures (largest homogeneous cross-domain failing cluster) by converting whole-budget byte-identical command loops into bounded runs that retain budget to recover."
regression_risk: "Low — strict no-op unless the exact same normalized Bash command is issued 3+ times in one task; the 23 R4 passing tasks finish in 8-54 adaptive steps and never repeat one command that many times, so they never arm nudge/suppression."
cost_shift: "Strongly negative — eliminates 20-30 wasted identical tool executions and 250-3200s runaway wall-clock on looping tasks; passing tasks unaffected."
rollback_trigger: "If R6 regresses any currently-passing task where LoopGuard fired, or the budget_exceeded count does not drop, revert the processor."
-->

### Why

R4 measured the incumbent R1 config (the R2 identical-command breaker was
reverted at the R3 gate back to R1). R4 scored 27/50 — a low draw of a noisy
config (R1 spread 27-31). The 20 failures split cleanly:

1. Eight budget_exceeded at the 80-step cap — 4-5 dominated by a single
   byte-identical Bash command repeated 22-33 times out of ~33 total tool calls.
   With no loop guard active in the incumbent, these burn the entire step/wall
   budget on one useless command and never recover. Harness deficiency: the
   model does not react to the unchanging tool result (task_001652 literally
   narrates "I'm stuck in a loop" 20x while re-issuing the same command).
2. Fourteen done with reward=0 — natural finishes that fail verification
   (superficial self-verification / genuine capability gaps). No new harness
   lever this round; consistent with R3's capability-gap finding.

The R2 breaker was reverted as part of a whole-config revert (its own predicted
tasks were not the regression driver — R1->R2 losses had RepeatGuard fire 0x, run
variance). The mechanism is sound; the miss was that it tracked only consecutive
repeats and suppressed at streak 5 — blind to the interleaved-narration loop
(task_001652) and A/B cycling. R5 re-scopes it to key on total per-command issue
count, catching both shapes and biting sooner, under a new hypothesis id.

### Changes

- processors/loop_budget_guard.py — new LoopBudgetGuardProcessor (Control,
  order=40, after CustomEditToolProcessor, before CustomSelfVerifyProcessor).
  on_before_tool: count total issues of each whitespace-normalized Bash command
  this task; at 3 arm one escalating user redirect, at 5 suppress the execution
  (approved=False, synthetic result) so the loop physically cannot continue.
  on_before_model: append exactly one user redirect (contract-safe). Counters
  reset at task boundaries.
- config.yaml — R1 config plus the new processor registered via absolute file://
  path. system_prompt.txt sidecar copied for SiblingSystemPromptBuilder.

### Evidence

- task_000118_3043e92d: monitor-launch + ps-aux command issued 33x identically;
  every result shows the monitor NOT running — relaunching a crashing process.
- task_000863_7acceb19: identical Python heredoc file write 29x.
- task_001032_1adaccb9: identical C++ heredoc file write 31x.
- task_001652_86e1d185: identical failing strings|grep 22x, each exit 1 no
  output; recovered via tesseract only after ~50 wasted steps, then out of budget.
- Passing cluster (23 tasks) finish in 8-54 steps with distinct adaptive
  commands — never repeat one command 3+ times, so the guard is a no-op on them.

### Uncertainty

Breaking the loop reclaims budget and forces a different action but does not
supply a missing capability. Expected outcome is partial recovery: task_001652
and the pure relaunch/rewrite loops have real headroom to finish once budget is
not burned; task_001031 (MPI deadlock) may still fail on the underlying gap. If
R6 shows no drop in budget_exceeded count, suppression fires too late (lower
suppress_threshold) or the loops are downstream of capability gaps and the
control lever is exhausted (revert). The config is noisy (+-4 tasks); attribute
against R1's mean, not its best draw.

### needs_from_human

The 14 done+reward=0 failures remain a model capability gap (superficial
self-verification of multi-requirement solutions, MPI concurrency, ustar
parsing) on a 9B model — not a harness deficiency. Pass-rate ceiling above ~60%
likely needs a stronger base model.

## Round 6 — fix length-recovery trigger (finish_reason-agnostic)

<!-- journal:frontmatter
round: 6
timestamp: 2026-08-16T08:00:00Z
hypothesis_id: h_length_recovery_content_trigger_v2
levers: [control]
predicted_affected: [task_001032_1adaccb9, task_000118_3043e92d, task_000010_644ab1c2, task_001818_b251e5ea, task_001031_a8f0eb37, task_000958_4bb2b05d, task_001701_95e3bbcb, task_000015_89886d8d, task_000264_ab8c7253, task_001321_658ce4a8]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=30/50; +2/-0 gained=task_001090_c61c71f2,task_001701_95e3bbcb; score 0.6000 >= incumbent(mean) 0.5900 - tol 0.0400
expected_global_gain: "Re-arm the proven R1 max_tokens loop-breaker on the 10-task runaway cluster (largest homogeneous cross-domain failing cluster) by triggering on runaway content-length instead of finish_reason, which this backend never reports as 'length'."
regression_risk: "Effectively zero on the 28 passing tasks: their largest assistant turn is 12K chars, 3.3x below the 40K trigger, and none has a huge no-tool-call turn. Strict no-op below threshold or on any tool-calling turn; the finish_reason==length branch is preserved so v1 is a subset of v2."
cost_shift: "Strongly negative — collapses 100K-320K-char runaway generations (task_001032 burned 7962s across 3 sessions of repeated 160K blobs) to ~1.8K-char excerpts, slashing tokens and wall-clock on affected tasks. Passing tasks unaffected."
rollback_trigger: "If R7 regresses any currently-passing task, or the count of runaway no-tool-call turns / long-elapsed failures does not drop, revert to R5 config."
-->

### Why

R5 scored 28/50. The largest homogeneous failing cluster is 10 runaway
max_tokens repetition loops (task_001032 7962s, task_000118 5038s/exit=error,
task_000010 2444s, task_001818 3456s, task_001031 2288s, etc.). These are
exactly the failure the R1 LengthTruncationRecoveryProcessor was written to
break — the model emits the same 100K-320K-char narration paragraph with no
tool call, hits the token cap, receives the run loop's passive "cut off by the
token limit, please continue" nudge, and re-primes the identical runaway
generation, cycling until it crashes or grinds to the cap. The R1/R5 processor
gates strictly on `finish_reason == "length"`, but a per-task scan of the
episode JSONL shows this backend records `finish_reason=None` on ALL of these
truncated turns — so the processor has been a silent no-op on the very loops it
targets. This is a harness-trigger deficiency, not a capability gap: a clean
bimodal separation exists (all 28 passing tasks max out at 12K-char assistant
turns; the 10 runaway failures start at 161K chars), so a content-length
trigger closes the gap with no passing-task collateral.

### Changes

- `processors/length_recovery.py` — new v2 LengthTruncationRecoveryProcessor
  (Control, order=5). `on_after_model` now treats a turn as a runaway
  truncation when it has no tool call AND (`finish_reason=="length"` OR
  `len(content) >= content_char_threshold`), collapsing the blob and arming the
  escalating corrective "issue one Bash command" redirect. The
  finish_reason=="length" path is preserved (v1 behaviour is a strict subset).
- `config.yaml` — repoint the processor `_target_` from the R1 file to the new
  R6 file and add `content_char_threshold: 40000`. system_prompt.txt sidecar
  copied for SiblingSystemPromptBuilder.

### Evidence

- task_001032_1adaccb9 session 3d2fbaac: raw_assistant contentlen sequence
  96741, 94701, 92026, 89871, 87050... (13 huge no-tool-call blobs),
  finish_reason=None throughout; messages.json msgs 3-58 show the passive
  "cut off by the token limit... continue" nudge ~25x while the assistant
  re-emits "I've been stuck in a loop. I need to stop analyzing...". The R1
  processor's own nudge language never appears in context — it never fired.
- task_000118_3043e92d: 17 huge no-tool-call turns, max 256861 chars,
  finish_reason=None; 5038s, exit=error.
- task_000010_644ab1c2: 9 huge turns (161854, 135357, 131968...),
  finish_reason=None; 2444s.
- task_001818_b251e5ea: 13 huge turns, max 185757, finish_reason=None; 3456s.
- Bimodal split: all 28 passing tasks have max assistant content <= 12000 chars
  and zero huge no-tool-call turns; the 10 runaway failures begin at 161K. The
  40K trigger sits in the empty band.

### Uncertainty

Breaking the loop reclaims budget and forces a concrete action but does not
supply a missing capability. Expected outcome is partial recovery: the
loop-dominated tasks (task_001032 ustar, task_000118, task_000010, task_001818)
have real headroom once the runaway blob is collapsed on its first occurrence;
task_001031 (MPI deadlock) and task_000264 (recursive-SQL logic) have upstream
capability gaps and may still fail. If R7 shows no drop in the runaway-turn
count or long-elapsed failures, the loops are downstream of capability gaps and
the control lever is exhausted on this cluster — revert.

## Round 7 — no-op: R6 nudges fire but are ignored; loops are capability-gap symptoms

<!-- journal:frontmatter
round: 7
timestamp: 2026-08-16T10:00:00Z
hypothesis_id: h_noop_control_lever_exhausted_v2
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=30/50; score 0.6000 >= incumbent(mean) 0.6000 - tol 0.0400 (final-round scoring)
expected_global_gain: "0 flips. Protects the 30 R6-passing tasks (incl. task_001090, task_001701 which pass BECAUSE R6 length-recovery reclaims budget) by declining a negative-Pareto control move on a lever that trace evidence proves is exhausted on the runaway cluster."
regression_risk: "None — config + system_prompt.txt sidecar are byte-identical to R6 (accepted at 30/50). All processors resolve at their stable absolute file:// paths (R5 loop_budget_guard, R6 length_recovery)."
cost_shift: "Zero — no config change."
rollback_trigger: "N/A (no-op). Next round should only spend if a NEW harness-shaped signal appears (a tool-return-shape gap, or a passing slow task that would tolerate a bounded budget). Do NOT re-scope the loop nudge again — it is proven ineffective."
-->

### Why

R6 scored 30/50 (accepted, +2/-0 vs R5: gained task_001090, task_001701). This
round I read the trace-level processor-trigger logs (not just messages.json) for
the runaway cluster and found decisive evidence that the R6/R1 control lever is
exhausted on it. The remaining 20 failures split into (1) five runaway loops
(task_001032 5279s, task_001031 5200s/error, task_000010 4564s, task_000118
2217s/error, task_001818 2105s) and (2) ~15 fast natural finishes that fail one
semantic sub-test each. Neither group has a harness lever that flips it this
round.

The critical new finding: on the runaway cluster the R6 LengthTruncationRecovery
processor **is firing** — the trace shows it triggered 25x (after_model) + 24x
(before_model) on task_001032 alone — and the model **ignores every nudge and
keeps producing content-only truncated turns**. Nudging (R1/R6) is therefore
proven-ineffective in this state, and R3 already established re-scoping the nudge
does not help. The only other control moves are unavailable or counterproductive:
BeforeModelEvent has no max_tokens knob; `skip_model=True` emits
finish_reason="stop" with no tool call, which the runloop treats as a natural
end_turn and TERMINATES the episode (reward 0). Body reading confirms the loops
are DOWNSTREAM of capability gaps, not the blocker: on task_000010 the model has
already run `ls`/`cat` and SEES the file state and the SyntaxError, but cannot
diagnose the Python import-shadowing bug (operator.py shadows stdlib `operator`);
on task_001032 it wrote a full ustar parser but its header-parsing logic is
wrong; task_001031 is an MPI send/recv deadlock. Injecting "here is your current
state" would not help — the state is already in context. Variant-A retroactive
check = NO for any control intervention on this cluster. The fast-fail cluster is
heterogeneous capability gaps (lingering processes, adversarial-filter bypass,
tarball contents, encryption magic number, ETL centroid values, deadlock
detection, grid-optimization value, CSV parser) — each fails a different
task-specific test; no shared harness lever (consistent with R3/R5).

Adding another nudge/loop-guard variant would be negative-Pareto: zero expected
flips against regression risk to the 30 passing tasks. task_001090 and
task_001701 pass specifically BECAUSE R6 length-recovery reclaims budget
(task_001701 passes at 56 steps/1747s), so the R6 config is load-bearing and
worth preserving byte-for-byte.

This is NOT a re-proposal of R3's reverted `h_noop_capability_gaps_v1`: R3
claimed control was exhausted broadly on inference; R7 supplies trace-level proof
that the R6 nudge fires ~49x and is ignored, plus body proof that the model
already has the data and lacks the capability. New evidence, distinct hypothesis
id, no config change.

### Changes

- `config.yaml` — byte-identical copy of R6 config (explicit no-op).
- `system_prompt.txt` — sidecar copied unchanged so SiblingSystemPromptBuilder
  resolves next to the new config.

### Evidence

- task_001032_1adaccb9 trace: processor_trigger counts —
  LengthTruncationRecoveryProcessor after_model=25, before_model=24;
  LoopBudgetGuardProcessor before_tool=8. The model still emitted ~40 of 80
  steps as content-only truncated turns and hit budget_exceeded at step 80,
  right after finally compiling/running its (wrong) ustar extractor
  ("Skipping unsafe path: docs/doc1.md").
- task_000010_644ab1c2 msgs 6-39: model runs `ls`/`cat operator.py`, sees the
  contents and the "SyntaxError: Missing parentheses in call to 'exec'", narrates
  "The error message ... is strange because the CLI tool doesn't have any exec
  calls" and re-loops — it has the data, cannot diagnose import-shadowing. 13
  content-only truncated turns, 13 "cut off by the token limit" nudges.
- task_001031_a8f0eb37: 13 content-only truncated turns / 14 cutoff nudges;
  underlying MPI send/recv deadlock (per prior rounds).
- Load-bearing check: task_001090_c61c71f2 (reward 1, 33 steps, 133s) and
  task_001701_95e3bbcb (reward 1, 56 steps, 1747s) pass under R6 — the slow one
  depends on budget reclamation the R6 processor provides. Preserving R6 protects
  both.
- canonicalize on the R7 config: {"ok": true, "checked_templates": 0}.

### Uncertainty

The config is noisy (±4 tasks over R1's spread); R6's 30 is a mid draw, not a
guaranteed floor. The honest risk of a no-op is under-spending on a live signal,
but every candidate on the two live clusters fails its retroactive check (loops
are capability-gap symptoms; fast-fails are heterogeneous), so a positive
intervention this round would carry regression risk with zero expected flips.
If a future round finds a genuinely NEW harness-shaped signal, that is where to
spend — not another iteration on the exhausted loop-nudge lever.

### needs_from_human

The dominant remaining failure driver is a model capability gap on Qwen3.5-9B
(Python import-shadowing debugging, ustar header parsing, MPI concurrency,
multi-service sysadmin orchestration, rigorous semantic self-verification of
multi-requirement solutions), not a harness deficiency. Pass-rate above ~60% on
this task set likely requires a stronger base model or task-specific tooling a
general harness cannot supply without memorising tasks.
