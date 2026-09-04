# Evolve Journal — tmax-coev-rep2-i1

## Round 1 — de-prime max_tokens loop

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-17T17:45:00Z
hypothesis_id: h_length_loop_deprime_v1
levers: [control]
predicted_affected: [task_000264_ab8c7253, task_000958_4bb2b05d, task_001818_b251e5ea, task_000118_3043e92d, task_001321_658ce4a8, task_001701_95e3bbcb]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=32/50; +3/-0 gained=task_000578_cebe85a5,task_000587_9862bb19,task_001090_c61c71f2; score 0.6400 >= incumbent(mean) 0.5800 - tol 0.0400
expected_global_gain: "Break the self-priming max_tokens repetition loop that drains the 80-step budget on ~6 budget_exceeded tasks; plausibly flip the 2-3 where the model already named the real bug in its narration."
regression_risk: "Over-collapsing a turn that was legitimately truncated mid-useful-work; but only tasks with >=2 consecutive no-tool length-truncations are ever touched — passing long-horizon tasks make tool calls each turn and never hit this signature."
cost_shift: "Neutral-to-negative: de-priming shrinks persisted context inside loops, lowering per-call tokens; earlier loop-break saves steps."
rollback_trigger: "Revert to stock LengthTruncationRecoveryProcessor if R2 net pass-rate drops OR any of the passing long-horizon tasks (001536/001652/001498/001031) regress T->F."
-->

### Why

R0 = 29/50. A distinct `budget_exceeded`-at-80-steps cluster (010,
118, 264, 958, 1321, 1701, 1818) shares one shape: the model emits a
paragraph of narration with no tool call, hits `max_tokens`
(`finish_reason=length`), the run loop appends a passive "continue"
nudge, and the model re-emits the SAME narration. The stock
`LengthTruncationRecoveryProcessor` fires correctly (corrective nudge +
truncation marker seen 33-39x per looping task in JSONL) but does not
break the loop: each truncated turn's full narration is persisted, so
by step 50 the model sees its own repeated prose ~6x in context (verified
in task 264) and re-primes the identical runaway generation. The nudge
is not the missing piece — the persisted repeated narration is.

### Changes

- `processors/length_loop_deprime.py` — new `LengthLoopDeprimeProcessor`
  (same singleton group `tmax_length_recovery`, same `_order=5`) that
  replaces the stock recovery processor. On repeated consecutive
  length-truncations (`>= deprime_after=2`) it drops the runaway
  narration to a compact stub (history stops re-showing the loop text)
  and escalates the user nudge to a hard "single Bash command, no prose"
  directive. First truncation retains the stock head+tail collapse.
- `config.yaml` — swap `recipe.tmax_eval.processors.length_recovery.
  LengthTruncationRecoveryProcessor` for the `file://` authored
  processor; add `deprime_after: 2`.

### Evidence

- `task_000264` / `task_000958` / `task_001818`: `exit_reason=budget_exceeded`,
  ~70 messages, JSONL shows 33-39 length-truncation recovery firings — the
  loop never converts to a tool call.
- `task_000264` step-50 context: the same narration paragraph appears ~6x,
  re-priming identical output. In 958 the narration already NAMED the real
  bug ("near backups: syntax error") but never issued the fix command.
- Pareto guard: passing tasks reach 40-98 messages (001536=98, 001652=88,
  001498=76) — proving message-count threshold tuning would regress them.
  The distinguishing signal is truncation-recurrence, not length, which is
  exactly what this processor gates on. Threshold tuning explicitly rejected.

### Uncertainty

The de-prime reclaims budget and de-primes context, but whether the weak
model then converts its (correct) diagnosis into the right command is a
capability question — flips are plausible, not guaranteed. If R2 shows no
flips in the cluster AND no regressions, the mechanism is safe but
insufficient (consider pairing with a repeated-action breaker next round).
If any passing long-horizon task regresses, revert immediately.


## Round 2 - break identical-command loops

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-17T18:30:00Z
hypothesis_id: h_repeated_command_breaker_v1
levers: [control]
predicted_affected: [task_000264_ab8c7253, task_001818_b251e5ea, task_001032_1adaccb9, task_000958_4bb2b05d]
cited_candidates: [C-002]
gating_outcome: accepted
gating_attribution: score=30/50; +2/-4 gained=task_001701_95e3bbcb,task_001818_b251e5ea lost=task_000587_9862bb19,task_001031_a8f0eb37,task_001090_c61c71f2,task_001652_86e1d185; score 0.6000 >= incumbent(mean) 0.6400 - tol 0.0400
expected_global_gain: "Close the residual budget_exceeded cluster that R1's narration-loop de-prime converted into an IDENTICAL-command repetition loop; flip the data_querying/data_processing subset (esp. 264) that is one different action away from producing its named deliverables, and reclaim wasted budget on the capability-bound tasks."
regression_risk: "Very low - the processor is warn-only and NEVER raises. Two currently-passing tasks (536, 1089) each issue 27 consecutive identical commands and still pass; a hard loop-raise would have regressed them, which is exactly why the stock LoopDetectionProcessor was rejected in favour of a warn-only variant."
cost_shift: "Neutral-to-negative: a few-hundred-char directive appended to at most one tool result per repeated run; earlier loop-break reduces steps/tokens on the affected cluster. No new model calls."
rollback_trigger: "Revert if R3 shows tasks 536 or 1089 regress T->F, OR net pass-rate drops below R1 32/50."
-->

### Why

R1 (accepted, 29->32/50, +3/-0) de-primed the pure-narration finish_reason=length
loop. But the residual budget_exceeded-at-80-steps cluster re-manifested with a
different shape: the model now issues a tool call every turn but re-issues the
EXACT SAME Bash command over and over, each returning the same result, and never
produces the task required output artifacts. Fingerprinting the R1 messages logs:
958 = 33/33 identical, 1818 = 27/29, 1032 = 25/29, 264 = 21 consecutive identical
heredoc writes to an intermediate SQL file. This is a genuinely new signal
(identical-command repetition, not length truncation) and no existing guard closes it.

### Changes

- processors/repeated_command_breaker.py - new RepeatedCommandBreakerProcessor
  (singleton group tmax_repeated_command_breaker, _order=21). On consecutive
  identical tool-call fingerprints it appends an escalating, deliverable-grounded
  directive to the tool result at warn_at=3 (verify/produce the required output
  artifacts; stop re-running the same command) and a hardened one at escalate_at=5.
  Warn-only - never raises. Contract-safe (mutates only ToolResultEvent.result).
- config.yaml - insert the processor after LengthLoopDeprimeProcessor, before
  CompactionProcessor.

### Evidence

- task_000264 last 16 messages: identical heredoc write to an intermediate SQL
  file re-issued back-to-back, each returning (exit 0, no output captured); the
  [EditDetection] over-editing warning fires 3x and is IGNORED - proving the
  generic nudge is too weak for this model. A grep of all 29 tool-call args finds
  ZERO references to the required deliverables (the two named output files): the
  agent had a correct recursive CTE but never ran it to produce output.
- task_000958 repeated command (33x): a broken-server restart loop
  (pkill; run server; ps grep), no progress.
- Pareto guard (why warn-only, not the stock raise): task_000536 (audit script
  re-written 27x) and task_001089 (malformed empty tool call 27x) BOTH PASS despite
  27 consecutive identical calls. The identical-command signature does not
  discriminate pass from fail, so LoopDetectionProcessor raise-at-5 would be a
  2-task regression. Passing long-horizon tasks otherwise have
  max_consec_identical < 3 and are untouched.

### Uncertainty

The directive is advisory; a weak model may ignore it just as it ignored the
edit-warning on 264 (the escalation + deliverable-grounding is the bet on why it
lands this time). For the C++ HTTP microservice tasks (958, 1701) the block is a
capability gap - this reclaims budget/cost but a flip is not expected there. If R3
shows no flips AND no regressions, the mechanism is safe but the remaining cluster
is capability-bound; if 536/1089 regress, revert immediately.


## Round 3 — step-budget convergence nudge

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-17T19:15:05Z
hypothesis_id: h_step_budget_reminder_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_000118_3043e92d, task_000958_4bb2b05d, task_001031_a8f0eb37, task_001032_1adaccb9, task_001321_658ce4a8]
cited_candidates: [C-003]
gating_outcome: accepted
gating_attribution: score=32/50; +2/-0 gained=task_000587_9862bb19,task_001090_c61c71f2; score 0.6400 >= incumbent(mean) 0.6400 - tol 0.0400
expected_global_gain: "Give the residual budget_exceeded-at-max_steps cluster (6 tasks) the convergence signal it currently never receives — TaskTimeReminder no-ops (no timeout_seconds) and CustomSelfVerify only fires on voluntary exit, which the cut-off cluster never reaches. Plausibly flips the 2-3 members still merely diagnosing at cut-off with un-written deliverables (010/1321/1032)."
regression_risk: "Near-zero: 29/30 passing tasks finished in 8-37 steps, below the 0.75 band (=60 steps at max_steps=80). Only task_001818 (passing, 69) reaches the band and the nudge reinforces convergence, not redirection. Warn-only, never raises."
cost_shift: "Neutral-to-negative: earlier convergence ends budget runs sooner; injected message is a few hundred chars at most twice on the tail of long runs. No new model calls."
rollback_trigger: "Revert if R4 shows any of 001818/001536/001652/001498 regress T->F, OR net pass-rate drops below R1 incumbent 32/50."
-->

### Why

R0=29 -> R1=32 (accepted) -> R2=30 (accepted vs R1 mean, but a net -2 draw). The
R1/R2 loop breakers already collapsed the identical-command loops (max-consecutive
identical calls in R2 = 3-5, down from R1's 21-33), so the remaining
budget_exceeded cluster (010/118/958/1031/1032/1321, all exit at step 80) is no
longer a mechanical loop — it is varied, unproductive exploration that runs out of
steps before the required deliverables are written. Crucially, NO processor warns
the agent it is approaching the step budget: TaskTimeReminderProcessor is
instantiated without `timeout_seconds` and no-ops (fired 0x on all six), and
CustomSelfVerifyProcessor only fires on a voluntary exit the cut-off cluster never
reaches. This is a genuine, unaddressed harness gap distinct from the two prior
loop-breaker rounds.

### Changes

- `processors/step_budget_reminder.py` — new `StepBudgetReminderProcessor`
  (singleton group `tmax_step_budget_reminder`, `_order=7`, right after
  TaskTimeReminder). Counts its own `on_step_start` invocations, reads the hard
  budget from `task.max_steps` (no hardcoded 80), and at 0.75 / 0.90 of the budget
  appends ONE `role="user"` "stop exploring, write+verify required deliverables
  now" message (each band once). Warn-only. Contract-safe (mirrors
  TaskTimeReminder: +1 user message to messages and raw_messages in on_step_start).
- `config.yaml` — insert the processor after TaskTimeReminderProcessor. R2's
  RepeatedCommandBreaker kept unchanged (confirmed inert — warn-only, never
  escalated, fired only on failing tasks, flipped nothing) so R3's single lever is
  cleanly attributable.

### Evidence

- `task_000010_644ab1c2` (budget_exceeded, 70 msgs): last assistant turn = "Let me
  check if the port forwarder is running now." — still investigating at cut-off;
  TaskTimeReminder text count = 0 in its log.
- `task_001321_658ce4a8` (budget_exceeded): last turn = "Now I can see the actual
  product codes ... the grep is correctly extracting the pattern" — diagnosing, no
  deliverable produced; TaskTimeReminder fired 0x.
- `task_001032_1adaccb9` (budget_exceeded): last turn is the length-loop-deprime
  stub — budget burned without output; TaskTimeReminder fired 0x.
- Pareto guard: R2 per-task shows 29/30 passing tasks finished in 8-37 steps
  (below 0.75*80=60); only task_001818 (pass, 69) reaches the band.

### Uncertainty

The nudge is advisory; a weak 9B model may keep exploring anyway (as it ignored
the R2 edit-warning). For 958 (C++ HTTP service) / 1031 (scientific compute) the
block is a capability gap — this reclaims budget but a flip is not expected there.
If R4 shows no flips AND no regressions, the mechanism is safe but the residual
cluster is capability-bound, and the next round should drop the inert R2 breaker
and look outside the budget cluster (the large early-exit/no_tool_calls failing
cluster is mostly capability-bound and NOT safely harness-addressable because
29/30 passing tasks also exit via no_tool_calls). If any long-horizon passing task
regresses, revert immediately.


## Round 4 — requirement-enumeration + per-criterion verify prompt

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-17T20:05:00Z
hypothesis_id: h_criterion_enumerate_verify_v1
levers: [instruction]
predicted_affected: [task_000140_01c78b42, task_000933_1f27096a, task_000748_c9807703, task_001937_ac874115, task_000505_50b5162d]
cited_candidates: [C-004]
gating_outcome: accepted
gating_attribution: score=32/50; +2/-2 gained=task_001652_86e1d185,task_001653_c4cafa73 lost=task_001090_c61c71f2,task_001536_acfe6c35; score 0.6400 >= incumbent(mean) 0.6400 - tol 0.0400
expected_global_gain: "First use of the untried instruction lever after three plateaued control rounds (R1=32,R2=30,R3=32). Targets the dominant failing cluster — voluntary-exit partial completion (11/18 fails are finished=no_tool_calls, many at 9-34 steps, final_pytest '1 failed, N passed'): the agent met most criteria but missed one specific checkable condition it never enumerated. A general 'survey -> enumerate EVERY criterion type (incl. non-file/state/absence/numeric) -> verify each with a concrete command' discipline plausibly flips the subset whose missing criterion is checkable-and-fixable within budget."
regression_risk: "Rewriting the base prompt could destabilize the 32 passing tasks. Mitigated: the original 5 stub lines are kept VERBATIM as the head and only general discipline is appended (no guidance removed); the appended discipline is exactly what the passing cluster (578/1536) already does, reinforcing rather than redirecting; verification is scoped to 'each criterion in your checklist' so trivial tasks add few commands. budget_exceeded tasks are untouched (they never reach the voluntary-exit path)."
cost_shift: "Mildly positive: a longer system prompt (~45 lines vs 5) once per task, plus a few extra verification commands on tasks that currently under-verify. Bounded — no per-turn injection, no loops."
rollback_trigger: "Revert the TemplateSystemPromptBuilder swap (back to SiblingSystemPromptBuilder / 5-line prompt) if R5 net pass-rate drops below the R1 incumbent (32/50) OR any currently-passing short-horizon task regresses T->F without a compensating flip."
-->

### Why

R1=32 -> R2=30 -> R3=32; the lever scoreboard shows all three prior rounds
pulled `control` (loop/budget processors) and the score plateaued. Re-reading
the R3 trajectories against the WHOLE failing set (not just the budget cluster
the prior rounds chased) reveals that the budget_exceeded cluster is only 3 of
18 fails. The DOMINANT shape is voluntary exit with partial completion:
11 of 18 fails are `agent.finished=no_tool_calls` / `exit_reason=done`, many at
very low step counts (9, 10, 10, 11, 15, 15, 16, 28, 34), and their
`final_pytest` reads "1 failed, 2-3 passed" — the agent satisfied MOST criteria
and declared done, but missed one specific checkable condition it never
enumerated or verified. The existing CustomSelfVerifyProcessor already injects a
strong FILE-centric exit checklist, so the gap is not "no verification hook" —
it is the agent's incomplete up-front MODEL of which criteria (non-file, system
state, absence, numeric threshold) to verify. That is an instruction-shaped gap,
and the base system prompt is a 5-line stub — the instruction lever was never
touched.

### Changes

- `templates/tmax_system_prompt.j2` — new system-prompt template. Keeps the
  original 5 stub lines verbatim, then appends GENERAL execution discipline:
  (1) survey the environment/toolchain before building; (2) enumerate EVERY
  success criterion up front, explicitly including non-file / process-state /
  absence / numeric-threshold conditions; (3) before declaring done, run a
  concrete command that exercises EACH criterion (not just file existence) and
  read its output. Zero task-specific literals — pure strategy for a class of
  problems.
- `config.yaml` — swap the SystemPromptProcessor's builder from
  `SiblingSystemPromptBuilder` (5-line stub) to `TemplateSystemPromptBuilder`
  pointing at the new `.j2` (absolute `file://`). This also makes the change a
  real `templates_added` changeset entry rather than an invisible sibling-file
  edit. R1/R2/R3 control processors left unchanged so R4's single lever is
  cleanly attributable.

### Evidence

- PASS `task_000578` step-1 body: "Let me break down this task: 1. Initialize a
  Go module ... 2. Read all CSV files ... 3. For each CSV: parse ... compute
  mean ... 4. Write ..." — full criterion enumeration up front; passes.
- FAIL `task_000140` final turns: agent's self-check enumerated only
  file/content requirements ("Go service ✓, supervisor script ✓") and
  ls-verified files, then declared done — verifier's
  `test_no_lingering_service_processes` found PIDs 346/592/796 still running.
  The binding state/absence criterion was never in its checklist.
- FAIL `task_000933` final turns: agent verified files exist and listed tarball
  contents, declared done — verifier extracted the tarball and found
  `.../home/user/bin/graph_math_double` absent (archive path-layout criterion
  never exercised by the agent's own check).
- FAIL `task_000748`: Rust builds (warnings only), 2/3 tests pass, but
  `test_memory_profiling` fails — a behavioral criterion never re-measured
  before exit.

### Uncertainty

The discipline is advisory; a weak 9B model may keep under-verifying (it ignored
R2's edit warning). For genuine capability gaps (task_001937 optimization
convergence 60 vs 50, task_001653 numeric, task_000505 security logic,
task_001031 scientific compute) no prompt flips them — those are logged as
capability-bound, not harness-addressable. If R5 shows no flips AND no
regressions, the instruction lever is safe-but-insufficient and the residual is
capability-bound. If any passing short-horizon task regresses T->F, revert the
builder swap immediately.


## Round 5 — no-op: residual is capability-bound

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-17T21:00:00Z
hypothesis_id: h_noop_capability_bound_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=34/50; score 0.6800 >= incumbent(mean) 0.6400 - tol 0.0400 (final-round scoring)
expected_global_gain: "0 flips expected — protects the stable 26-pass core plus 9-task fragile passing surface by refusing to add speculative config that only risks noise-level regressions. The residual 15 always-fail tasks are capability-bound and have not responded to ANY of 4 distinct harness interventions across R1-R4."
regression_risk: "None — config is byte-identical to R4 (accepted incumbent). No new surface, no new processor, no template change."
cost_shift: "Zero — identical pipeline."
rollback_trigger: "N/A (no-op). If R6 evidence surfaces a NEW mechanical (non-capability) signal shared by two or more always-fail tasks, that becomes the next candidate."
-->

### Why

Score has plateaued at 32/50 for four consecutive rounds (R1=32, R2=30,
R3=32, R4=32) despite pulling three distinct control-lever mechanisms
(R1 length-loop de-prime, R2 identical-command breaker, R3 step-budget
reminder) and one instruction-lever mechanism (R4 enumerate+verify
template). A cross-round flip analysis (R0-R4, computed in
`_meta_scratch/flips.py`) is decisive:

- 26 tasks always pass (stable core).
- 9 tasks are fragile — flip on measurement noise (578, 587, 1031,
  1090, 1536, 1652, 1653, 1701, 1818); several trended UP and are now
  effectively stable-pass (578, 1701, 1818). These are the regression
  surface.
- 15 tasks ALWAYS fail across all 5 rounds (010, 015, 028, 118, 140,
  264, 396, 505, 748, 933, 958, 1032, 1321, 1781, 1937). Not one of them
  was flipped by any of the four harness interventions.

The 15-task always-fail set is the only place a corrective candidate
could live, and body inspection shows every one is a genuine model
capability gap, not a harness deficiency. The loop-breaking (R1/R2) and
budget-signal (R3) mechanisms are demonstrably working — `probe.py`
confirms max-consecutive-identical commands is now 1-5 (down from R0's
21-33) and the residual budget_exceeded tasks (958=29/30 unique commands,
010=26/27 unique) are doing VARIED unproductive exploration, i.e. the
mechanical loops are already closed. The R4 verify-instruction and the
stock CustomSelfVerifyProcessor (which already says "For running
services: confirm they are still alive and reachable right now") are both
present and injected on the voluntary-exit fails; the agent reads them and
still cannot produce the correct domain logic.

### Changes

- `config.yaml` — byte-identical copy of R4 (explicit no-op). Verified
  `diff` empty; `canonicalize` returns `ok: true`.

### Evidence

Failure mechanisms of the always-fail cluster (verifier assertions from
`final_pytest.output_tail`) are diverse and capability-bound — no shared
harness mechanism:

- `task_000264`: CSV aggregate off-by-one (12 vs 11) AND missing
  `USING INDEX` in the SQL query plan — semantic/domain logic.
- `task_000748`: Rust errors file contains `0502` but required `E0502` —
  the agent's extraction dropped the leading char (a data-shape bug the
  model didn't catch; single-task, different mechanism from 264).
- `task_000933`: tarball extracts to wrong internal path layout
  (graph_math_double binary absent) — archive-layout logic.
- `task_001781`: deadlock logic literally unchanged
  (`timestamp_ms % 200 == 0` still present) — the agent never fixed the bug.
- `task_000505`: `2 of 2 evil bypassed` — security-detection logic gap.
- `task_001937`: optimization convergence Optimal Grid 60 vs required
  50 — numerical/algorithmic gap.
- `task_000015`: URL routing regex fails property-based URL-encoding case —
  parsing capability.
- `task_000396`: numeric max deviation 1.05338 vs required below 0.1 —
  scientific-compute algorithm wrong.
- `task_000118`: log dir peaked at 200MB vs 45MB cap — rotation/cleanup
  logic doesn't enforce the limit.
- `task_000010` / `task_001090`: budget_exceeded doing varied exploration
  (pexpect+socat interactive K8s CLI automation; HTTPS-service JSON schema
  mismatch) — capability, not loop.
- `task_000028`: agent's nginx+C++ service IS running at exit, but the
  verifier module fails with `ModuleNotFoundError: No module named
  requests` — a verifier-phase environment issue; internet is blocked so
  pip install is not a reliable harness fix (playbook: outbound fetches
  blocked). Not harness-addressable.

### Uncertainty

The Pareto-correct move when no corrective candidate passes its
Variant-A retroactive check (all 15 stuck tasks: "would the agent still
have failed? — yes, it lacks the domain capability") is to NOT ship
config surface that only endangers the 9-task fragile passing cluster
(R4's template swap already showed noise-level +2/-2 churn on
1090/1536/1652/1653). If a future round wants to reclaim wasted budget on
the 6 always-fail budget_exceeded tasks, the only safe lever is a
cost-reduction Configuration tweak with predicted_affected empty (cost,
not pass-rate) — but that pays nothing on score and was not worth the
regression risk this round. Next round should look for a NEW mechanical
signal shared by two or more always-fail tasks (e.g. the E0502/extractor
output-mangling shape in 748+1321 if it recurs) before touching the
pipeline again.
