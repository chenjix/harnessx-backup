# Tmax coev rep12-i4 — evolve journal

## Round 1 — break identical-Bash loops

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_bash_loop_breaker_v1
levers: [control]
predicted_affected: [task_000032_3fb303f6, task_000910_16cc0daf, task_001044_45c70cf1, task_001465_aa3ed3f8, task_000506_c13429e7, task_001028_5bc8bc70, task_000938_6d7bdc5c, task_000796_828a72cf, task_000761_072f9e93]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=10/50; +1/-1 gained=task_000677_1de47eed lost=task_000560_7dc63881; score 0.2000 >= incumbent(mean) 0.2000 - tol 0.0400
expected_global_gain: "Recover wasted step-budget on the largest failing cluster (9/11 budget_exceeded tasks stuck in identical-Bash loops); frees ~25-70 steps per task to try a different approach."
regression_risk: "A legitimate task that must poll one identical command >=6 times (waiting on a service) could be interrupted; mitigated by generous break_threshold=6 and a redirect that says verify-and-finish rather than hard-fail. Internal _tb2_self_verify calls are excluded from tracking, protecting the passing task that repeats it 22x."
cost_shift: "Net decrease — intercepting dead loops truncates 20-75 wasted step-generations per affected task, including the 1600s/1900s runaways."
rollback_trigger: "If R2 pass_rate is flat-or-down AND any previously-passing task regressed to F with a BashLoopBreaker BLOCKED message in its final steps, revert."
-->

### Why

R0 scored 10/50 (0.20). The single dominant failure cluster is
`exit_reason=budget_exceeded` at the step cap (`steps=80`): 11 tasks, only 1
passed. On 9 of those 11, the model enters a degenerate tool-level repetition
loop — it re-issues the byte-for-byte same `Bash` command 10-33 times in a row,
gets the identical result each time, explicitly narrates "I'm stuck in a loop /
I need to stop repeating this command," and then repeats it again until the
budget is exhausted. The loops set in early (3rd-7th Bash call), so 25-75 steps
are wasted on a command already proven useless. This is a harness deficiency:
the model cannot self-correct a loop it recognises, and there is no mechanical
guard in the pipeline for repeated-identical-tool-call loops (the existing
`LengthTruncationRecoveryProcessor` only handles `finish_reason=length` / no
tool call, a different shape).

### Changes

- `processors/bash_loop_breaker.py` — new `BashLoopBreakerProcessor`
  (`MultiHookProcessor`): Bash-only fingerprinting; warns on the 3rd identical
  command (appends nudge to the tool result), intercepts the 6th (does not
  execute; injects a corrective redirect via `approved=False` +
  `synthetic_result`). Internal tools (e.g. `_tb2_self_verify`) are never
  fingerprinted, so they cannot trip the detector.
- `config.yaml` — register the processor after
  `LengthTruncationRecoveryProcessor` with `warn_threshold=3`,
  `break_threshold=6`. Rest of the pipeline copied byte-for-byte from R0.
- `system_prompt.txt` — copied byte-for-byte beside the new config (required by
  `SiblingSystemPromptBuilder`).

### Evidence

- `task_000910_16cc0daf`: 33x identical `ffmpeg ... -f rawvideo ...`; tool output
  each time is the interactive `File 'pixel_format=rgb24' already exists.
  Overwrite? [y/N] Not overwriting - exiting`; assistant: "I need to stop
  repeating the same command" → repeats.
- `task_000032_3fb303f6`: 16x identical Bash returning `(exit 0, no output
  captured)`; assistant: "Let me try a different approach" → repeats verbatim.
- `task_001044_45c70cf1`: 27x identical `echo "1 10 ..." | perf_oracle` →
  `Trend: m=0 b=1` each time.
- `task_001465_aa3ed3f8`: 26x identical `curl -s http://localhost:5000/embed`.
- Passing-set safety: all 10 R0 passing tasks had max consecutive identical-Bash
  run = 1 (measured); the one passing task with heavy repetition
  (`task_002138_2e85672e`, 22x) repeats `_tb2_self_verify`, which the detector
  ignores by design.

### Uncertainty

Breaking the loop returns budget but does not guarantee each task flips — some
(e.g. the video-frame + crypto + Go-server task) also need domain capability the
model may still lack; those are model-capability gaps, not harness gaps, and
this round does not try to patch them. The clean win is removing the specific
harness-level waste (burning the whole budget on a proven-useless command). If
R2 shows no pass-rate movement AND no cost reduction on the cluster, the loop
was masking a deeper capability wall and the lever should move elsewhere.

## Round 2 — deploy round-trip verify workflow prompt

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_roundtrip_verify_prompt_v1
levers: [instruction]
predicted_affected: [task_000763_7713d5ae, task_000567_d082f802, task_000142_47a40a0c, task_000032_3fb303f6]
cited_candidates: [C-002]
gating_outcome: accepted
gating_attribution: score=10/50; +2/-2 gained=task_000560_7dc63881,task_001185_636b067c lost=task_000677_1de47eed,task_002138_2e85672e; score 0.2000 >= incumbent(mean) 0.2000 - tol 0.0400
expected_global_gain: "Largest failing cluster is done-but-wrong-content (28 tasks; 16 clear value mismatches). Deploy the general survey/plan/round-trip-verify/preserve-partial-credit workflow the R1 config header claimed but never actually shipped (R1's sibling system_prompt.txt was the bare 5-line default). Even a modest hit rate flips pass_rate off the flat 0.20."
regression_risk: "LOW — prompt is strategy-only and additive, cannot remove capability. Minor: extra verify steps could add budget pressure near the step cap; mitigated by the paired 'abandon doomed subgoal / bank completed deliverables' rule which REDUCES wasted steps on the budget_exceeded cluster."
cost_shift: "Neutral-to-down: +a few verify steps on short tasks, -many wasted steps on the 80-step budget_exceeded runaways."
rollback_trigger: "Revert to the 5-line prompt if R3 pass_rate < 0.20 OR a previously-passing task regresses with the agent visibly over-verifying into the step cap."
-->

### Why

R1 (BashLoopBreaker, control) left pass_rate flat at 0.20 (10/50). Post-hoc:
the loop-breaker fired on only 4/11 budget_exceeded tasks, and on those the
model IGNORED the BLOCKED redirect and re-emitted the byte-identical command
5-33 more times (task_000032: 33 consecutive BLOCKs, never once attempted the
required report.txt). Mechanical redirects are being ignored — the control
lever has hit a capability wall on the loop cluster. The dominant failure shape
is now `exit_reason=done` with a verifier AssertionError on a computed VALUE
(28 done-but-fail; 16 clear value mismatches). In these the agent confidently
declares success; where it "verified" at all, it never round-tripped its answer
against the constraint the task actually states (e.g. task_000763 ran the
forward token algo on its guessed PIN 2778 but never checked that 2778 actually
reproduces the given token 1A3B — ground truth was 2394).

Critically, the R1 config's own header comment claims a full
survey/plan/verify workflow prompt, but the `system_prompt.txt` shipped beside
it is the bare 5-line default. The playbook's #1 lever (explicit plan +
verify-deliverables discipline) was never actually deployed.

### Changes

- `system_prompt.txt` (sibling of config, loaded by SiblingSystemPromptBuilder):
  replaced the 5-line default with a general solver workflow centered on
  (a) round-trip verification — when you derive a value from a GIVEN target,
  re-run the forward process on your value and confirm it reproduces the
  target; (b) verbatim diff against any exact stated string/path/target;
  (c) preserve-partial-credit — abandon a doomed subgoal and bank completed
  deliverables rather than burn the whole budget re-issuing a failing command.
  Strategy-only: no task ids, answers, constants, or per-task code.
- `config.yaml`: processor pipeline copied byte-for-byte from R1 (BashLoopBreaker
  retained). Only the sibling system prompt changed. file:// refs to R1/R0
  processor assets are read-only and unchanged.

### Evidence

See `_meta_scratch/candidates.md` C-002 for body-quoted verifier assertions on
task_000763 (PIN 2778 vs 2394), task_000567 (PC1_Sum 6.9499 vs 7.7709), and
task_000142 (symlink -> file_A.dat vs financial_records_2021.dat).

### Uncertainty

Instruction cannot conjure computation the model can't do; the round-trip rule
only helps where the model CAN detect its own error by re-checking against the
stated constraint (strongest on task_000763 and task_000142). task_000567's
seed/determinism mismatch may still need capability the model lacks. Honest
expectation: flips a subset of the value-mismatch cluster, not all. If R3 shows
zero movement, the done-but-wrong cluster is a pure capability wall and the
lever should move off Instruction entirely for this benchmark.

## Round 3 — fire verifier-dep guard proactively

<!-- journal:frontmatter
round: 3
timestamp: 2026-06-03T00:00:00Z
hypothesis_id: h_verifier_dep_guard_proactive_v1
levers: [control]
predicted_affected: [task_000796_828a72cf, task_000910_16cc0daf, task_002108_a8cfbf2a]
cited_candidates: [C-003]
gating_outcome: accepted
gating_attribution: score=10/50; +1/-1 gained=task_000013_b393db71 lost=task_001185_636b067c; score 0.2000 >= incumbent(mean) 0.2000 - tol 0.0400
expected_global_gain: "Remove a guaranteed-0 mechanical wall on the HTTP-service subset of the budget_exceeded cluster: 3 tasks whose verifier aborts at pytest COLLECTION time with `ModuleNotFoundError: No module named 'requests'` because the exit-intent-only dep guard never fires on budget_exceeded (steps=80) runs. Generalizes to any long-running task whose hidden verifier imports requests/yaml."
regression_risk: "Very low. The ensure command is idempotent (import-check first, pip only on failure, whole thing `|| true`), a pure no-op where requests is already present (the passing set). Prepending it to a mid-run tool-call turn adds one tool result to context but does NOT replace the model's own call (the run loop executes every call in a turn). Fires at most once per task."
cost_shift: "Negligible +1 Bash round-trip (a few hundred tokens of banner) per task, once. No change to runaway tails."
rollback_trigger: "Revert if R4 pass_rate < 0.20 OR a previously-passing task regresses with a 'VERIFIER DEP CHECK' banner implicated in its final steps (prepended call disrupting the model's flow)."
-->

### Why

R1 (control/loop-breaker) and R2 (instruction/verify-prompt) both left pass_rate
flat at 0.20 (10/50). Reading the R2 trajectories' `final_pytest.output_tail`
surfaced a distinct, purely-mechanical failure the prior rounds never touched:
three separate HTTP-service tasks (task_000796 security, task_000910 sysadmin,
task_002108 file_operations) fail at verifier **collection** time —
`ImportError while importing test module '/tmp/test_final_state.py' ...
ModuleNotFoundError: No module named 'requests' ... Interrupted: 1 error during
collection`. The agent's server work is never even scored; pytest can't collect
the test module. The R0/R2 pipeline already carries a `VerifierDepGuardProcessor`
built exactly for this, but it fires ONLY at clean exit-intent — and all three
tasks end in `exit_reason=budget_exceeded` at `steps=80`, so the guard never
fires (the "VERIFIER DEP CHECK" banner appears 0 times in each transcript). The
tasks that most need the guard are precisely the ones that never reach clean
exit. This is a Control deficiency: the guard's trigger is wrong, not its
command.

### Changes

- `processors/verifier_dep_guard_proactive.py` — new
  `ProactiveVerifierDepGuardProcessor` (`MultiHookProcessor`): fires the
  dep-ensure exactly once per task, whichever comes first — (a) PROACTIVE:
  prepends the ensure `Bash` call onto the first mid-run tool-call turn at/after
  `proactive_step=4` (the run loop executes every tool call in a turn, so the
  model's own work proceeds); (b) EXIT-INTENT fallback, preserving the original
  guard's behaviour for short clean tasks. Idempotent import-check-then-pip,
  fully `|| true`-guarded.
- `config.yaml` — replaced the exit-intent-only `VerifierDepGuardProcessor` in
  the tail slot with `ProactiveVerifierDepGuardProcessor(proactive_step=4)`.
  Rest of the pipeline + sibling `system_prompt.txt` copied byte-for-byte from
  R2 (BashLoopBreaker + R2 verify-workflow prompt retained).

### Evidence

- task_000796_828a72cf.result.json: collection ImportError, `import requests`
  test line 5; exit_reason=budget_exceeded, steps=80.
- task_000910_16cc0daf.result.json: identical collection ImportError, `import
  requests` line 5; budget_exceeded, steps=80.
- task_002108_a8cfbf2a.result.json: identical collection ImportError, `import
  requests` line 4; budget_exceeded, steps=80.
- Guard-never-fired: `grep -c "VERIFIER DEP CHECK"` = 0 in all three
  `messages.json` (exit-intent path unreached — run ended budget_exceeded).
- pip-reaches-PyPI: task_001013_7f3bf12e installed `flask-3.1.3` + full dep
  chain (blinker/click/itsdangerous/jinja2/markupsafe) from PyPI at full speed,
  so `pip install requests` succeeds in-container.

### Uncertainty

Installing `requests` reliably converts a certain-fail *collection* error into a
real test run; whether each of the three then passes depends on the underlying
server correctness, which the collection abort made unobservable (tests never
executed). So this is a partial-yes retroactive check: guaranteed to remove the
mechanical 0, uncertain how many of the 3 flip. Because the install is
idempotent the downside on the passing set is nil. If R4 shows the three still
failing but now with real per-test assertions (not collection errors), the
remaining gap is server-capability, not harness — and the lever moves off the
dep guard. If a passing task regresses due to the prepended call, lower
`proactive_step` reasoning was wrong and revert.

## Round 4 — hard-terminate degenerate assistant-turn loops

<!-- journal:frontmatter
round: 4
timestamp: 2026-06-04T00:00:00Z
hypothesis_id: h_degenerate_turn_terminator_v1
levers: [control]
predicted_affected: [task_000438_fee5a792, task_000032_3fb303f6, task_000938_6d7bdc5c, task_001044_45c70cf1, task_000761_072f9e93, task_001028_5bc8bc70, task_002138_2e85672e, task_002146_0bc2994c, task_000348_31fb8c8a, task_000908_170e5e4e]
cited_candidates: [C-004]
gating_outcome: accepted
expected_global_gain: "Eliminates the exit_reason=error crash cluster (4 tasks: 000032, 000438, 000938, 001044) by converting the identical-turn runaway into a clean done that guarantees verifier scoring and protects the post-flight replay gate (which hard-fails on exit_reason=error). Recovers 20-70 wasted step-generations across the full 16-task identical-assistant-turn-loop cluster and lets exit-intent processors (verifier-dep guard) fire on tasks that previously died at the step cap. Generalizes to any future task that falls into byte-identical assistant repetition (Bash OR no-tool-call), a shape the R1 BashLoopBreaker structurally cannot cover."
regression_risk: "Near-zero on pass-rate. All 10 R3 passing tasks have a max consecutive identical-assistant run of at most 2; terminate_threshold=6 (warn at 4) cannot be tripped by any observed passing trajectory. Force-stop fires only after the run is provably dead (6 identical turns after the R2 prompt AND R1 soft block were both ignored). Small theoretical risk: a legitimate task that must poll one identical action many times could be stopped, mitigated by the high threshold and the fact that the R2 self-verify tool (which repeats legitimately in one passing task) does not produce identical assistant-text runs."
cost_shift: "Net decrease. Truncates 20-70 wasted step-generations (full model calls, some at the 4096-token cap) across 16 tasks including the 900-1900s runaways. The synthetic stop turn replaces a model call rather than adding one; no new model calls."
rollback_trigger: "Revert if R5 pass_rate below 0.20 OR any previously-passing task regresses with a [LoopTerminator] banner implicated in its final steps (threshold too low / disrupted a legitimate repeat)."
-->

### Why

R0-R3 are flat at 0.20 (10/50). Re-reading the R3 transcripts surfaced the true
dominant failure shape that all prior rounds only partially touched: a
DEGENERATE ASSISTANT-TURN LOOP. Measuring the max run of byte-identical
consecutive assistant messages: 16 distinct FAILING tasks have a run of 4 or
more (ranging 4 to 41); every PASSING task's max run is 2 or fewer, a
razor-sharp separator. Four of the 16 (000032, 000438, 000938, 001044) end in
exit_reason=error (agent_error crash driven by the repetition), the rest in
budget_exceeded.

The R1 BashLoopBreaker fingerprints only the Bash tool input, so it is blind
to the no-tool-call half of the loop (e.g. task_000761 repeats "The user is
asking me to verify my solution..." with NO tool call, alternating with the
self-verify ACK, msgs 2-11). Where it does fire, its soft "BLOCKED" redirect is
ignored verbatim: task_000032 shows 20 consecutive BLOCKs with the model
re-emitting the identical assistant turn each time. The R2 system prompt already
instructs the model to stop looping; it ignores that too. Instruction plus soft
mechanical redirect are proven-saturated over three rounds. The missing
capability is termination, plus coverage of the no-tool-call loop shape.

### Changes

- `processors/degenerate_turn_terminator.py` — new
  `DegenerateTurnTerminatorProcessor` (`MultiHookProcessor`, `on_before_model`):
  fingerprints the text of the last assistant turn in the assembled context;
  warns at `warn_threshold=4` (one contract-safe user message), and at
  `terminate_threshold=6` sets `skip_model=True` + `synthetic_output`, which the
  run loop turns into a `finish_reason="stop"` response with no tool calls, its
  own clean-exit condition (exit_reason=done). Strategy-only, no task literals.
- `config.yaml` — registered the new processor immediately after the retained
  R1 BashLoopBreaker (which still trims the Bash-input half early) and before
  CompactionProcessor. Rest of the R3 pipeline plus sibling `system_prompt.txt`
  copied byte-for-byte. R3's ProactiveVerifierDepGuard is retained: R3
  trajectories confirm it works (796/910/2108 no longer hit collection
  ImportError; they now fail with real ConnectionError, a server-capability
  gap, not a harness gap).

### Evidence

See `_meta_scratch/candidates.md` C-004 for body-quoted identical-turn loops on
task_000032 (20x "stuck in a loop" + BLOCK), task_000761 (self-verify no-tool
loop msgs 2-11 + build.rs loop msgs 13-21), and the full 16-task run-length
table (all FAIL, run 4-41) vs the passing set (all 2 or fewer).

### Uncertainty

This is a PARTIAL-yes retroactive check. On the 4 exit_reason=error tasks the
forced clean done is a strict robustness improvement (removes the crash,
guarantees verifier scoring, protects the replay gate). On the budget_exceeded
loopers it does NOT by itself add a missing deliverable; those remain
capability-bound, so the honest gain there is cost/step recovery and a clean
exit that lets the verifier-dep guard's exit-intent path run. Net expectation:
robustness plus a large cost win, with pass-rate upside concentrated on the
crash cluster. If R5 shows the crash tasks still failing with real per-test
assertions (not agent_error) and no cost reduction on the loop cluster, the
remaining wall is pure model capability and the lever should move off loop
control entirely for this benchmark.


## Round 5 — fuzzy near-identical turn terminator

<!-- journal:frontmatter
round: 5
timestamp: 2026-06-05T00:00:00Z
hypothesis_id: h_fuzzy_loop_terminator_v1
levers: [control]
predicted_affected: [task_000032_3fb303f6, task_001201_1340f4e2, task_001028_5bc8bc70, task_001465_aa3ed3f8, task_001044_45c70cf1]
cited_candidates: [C-005]
gating_outcome: accepted
gating_attribution: score=10/50; +0/-0; score 0.2000 >= incumbent(mean) 0.2000 - tol 0.0400
expected_global_gain: "Eliminate the exit_reason=error crash cluster (task_000032, task_001201) by converting agent_error into a clean done that guarantees verifier scoring and protects the post-flight replay gate, and reclaim 20-50 wasted step-generations / 600-1600s wall-clock across the drifting-loop cluster (>=5 tasks) so exit-intent processors (verifier-dep guard) fire. Generalizes to any task falling into a NEAR-identical (not byte-identical) assistant-turn loop — the shape R1 (Bash-input) and R4 (byte-identical) structurally cannot cover."
regression_risk: "Near-zero on pass-rate. Every R4 passing task has a max consecutive similarity>=0.85 assistant-turn run of at most 2; terminate_threshold=5 (warn at 3) cannot be tripped by any observed passing trajectory. Residual: a legitimate task that must poll a near-identical action >=5 times could be stopped early — mitigated by the high threshold, min_tokens=5 (short acks ignored), and the two-turn warn window before termination."
cost_shift: "Net decrease. Truncates 20-50 wasted step-generations (several at the 4096-token cap) across the loop cluster including the 828s/1555s/964s runaways. The synthetic stop turn replaces a model call rather than adding one; no new model calls. Warn path adds at most one small user message per looping task."
rollback_trigger: "Revert if R6 pass_rate < 0.20 OR any previously-passing task regresses with a [LoopTerminator] banner implicated in its final steps (threshold too low / disrupted a legitimate near-identical repeat)."
-->

### Why

R0-R4 are all flat at pass_rate 0.20 (10/50). The dominant remaining
harness-level waste is a degenerate assistant-turn loop; the R4
DegenerateTurnTerminatorProcessor was built to convert it into a clean exit but
measurement on the R4 trajectories shows it NEVER FIRED on its target tasks
(grep [LoopTerminator] = 0 in both exit_reason=error crashes and in the
budget_exceeded loopers). The cause: R4's fingerprint requires byte-identical
turns, but the real loops DRIFT — each regenerated "I've been stuck in a loop
... take a fundamentally different approach" turn differs by a few words or a
different 4096-token truncation point. The max byte-identical consecutive
assistant run tops out at 4, below R4's terminate_threshold of 6. A similarity
(Jaccard on normalized token set) counter at 0.85 separates loopers from the
passing set even more sharply: FAILING loopers reach runs of 4/5/6/10/17 while
the entire R4 passing set has a max run of 2. The done-but-wrong-value cluster
that makes up most of the remaining failures is a model-capability wall (wrong
computed numbers/hashes/MSE across ~28 tasks) and is explicitly NOT targeted
this round — no harness fix for those; they need model capability.

### Changes

- processors/fuzzy_loop_terminator.py — new FuzzyLoopTerminatorProcessor
  (MultiHookProcessor, on_before_model): counts consecutive assistant turns
  whose normalized token-set Jaccard similarity is at least 0.85 (byte-identical
  scores 1.0, so it subsumes the R4 exact matcher); warns at 3 (one
  contract-safe user message), force-terminates at 5 via skip_model +
  synthetic_output which the run loop turns into finish_reason=stop ->
  exit_reason=done. Strategy-only, no task literals.
- config.yaml — REPLACED the R4 exact-match DegenerateTurnTerminatorProcessor
  with the fuzzy terminator (warn 3 / terminate 5 / similarity 0.85). Rest of
  the R4 pipeline (R1 BashLoopBreaker, R3 ProactiveVerifierDepGuard, etc.) and
  sibling system_prompt.txt copied byte-for-byte.

### Evidence

See _meta_scratch/candidates.md C-005 for the measured byte-run vs
similarity-run tables. Key: task_000032 byte_run=4 / sim_run=4 (crashed
exit_reason=error, 828s); task_001201 byte_run=4 / sim_run=10 (crashed
exit_reason=error); task_001028 byte_run=4 / sim_run=17; task_001465 sim_run=6;
task_001044 sim_run=5 (all budget_exceeded, steps=80). Passing set: max
sim_run=2 (task_000899), nine of ten equal 1. [LoopTerminator] banner count = 0
in all target messages.json (R4 exact matcher never fired).

### Uncertainty

Partial-yes retroactive check. On the two exit_reason=error tasks the forced
clean stop is a strict robustness win (removes the crash, protects the replay
gate, banks partial work, saves wall-clock). On the budget loopers it does not
by itself add a missing deliverable — most fail on a wrong computed value, a
capability wall — so the honest gain there is cost/step recovery plus a clean
exit that lets the verifier-dep guard exit-intent path run. If R6 shows the
crash tasks still failing with real per-test assertions (not agent_error) AND no
cost reduction on the loop cluster, loop control is fully saturated for this
benchmark and the lever must move off it entirely (the remaining wall is pure
model capability).

## Round 6 — no-op: loop lever saturated, remainder is a capability wall

<!-- journal:frontmatter
round: 6
timestamp: 2026-06-06T00:00:00Z
hypothesis_id: h_noop_loop_saturated_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=10/50; +0/-0; score 0.2000 >= incumbent(mean) 0.2000 - tol 0.0400
expected_global_gain: "0 flips. Explicit no-op. The only harness-shaped lever left (loop control) has provably collapsed its safety margin on the R5 run, and the dominant remaining failing cluster is a model-capability wall that SOUL.md forbids patching with domain knowledge. Preserving the currently-stable R5 pipeline (which fires on no passing task) is the correct Pareto move."
regression_risk: "None — byte-identical config + sibling system_prompt.txt copied from R5. No processor currently fires on any of the 10 passing tasks (verified), so nothing is at risk of a knob-tune regression this round."
cost_shift: "Zero — identical pipeline."
rollback_trigger: "N/A (no-op). Next round: if a NEW harness-shaped signal appears (a recurring blocked-but-recoverable tool shape, a mis-parametrized processor firing on passers, or a done-but-wrong sub-cluster whose fix is mechanical rather than domain-knowledge), pursue it. Do NOT push loop control further."
-->

### Why

R0-R5 are all flat at pass_rate 0.20 (10/50). This round is an evidence-backed
explicit NO-OP. Two independent findings drove it:

1. **The loop-control lever (4 control rounds: R1/R3/R4/R5) is saturated AND now
   regression-unsafe.** Measured on the R5 run, the max consecutive run of
   near-identical assistant turns (Jaccard>=0.85 token-set, both fixed-anchor
   and sliding-previous variants) is only **3** on every looper
   (task_001044, task_001465, task_000032, task_001201, task_000438) — down from
   R4's 4-17 because this run's loops drift more per turn. Critically, a PASSING
   task (task_000061) also reaches run=3, identical to the loopers. The
   separator between loopers and passers that R4/R5 relied on (looper 4-17 vs
   passer <=2) has collapsed: there is now NO terminate_threshold that catches
   the loopers without also firing on a passing task. Confirmed inert: the
   [LoopTerminator] banner fired 0 times on all target tasks (grep=0), and the R3
   ProactiveVerifierDepGuard "VERIFIER DEP" banner also fired 0 times on the 3
   HTTP tasks. I also traced the R5 FuzzyLoopTerminator's anchor bug (it compares
   each turn to the run's FIRST turn, so drift decays the score) — but the
   sliding-previous fix yields the same max run (3) on this data, so even the bug
   fix cannot reach a safe firing threshold. The lever is dead for this benchmark.

2. **The dominant remaining failing cluster (~28 exit=done tasks) is a model-
   capability wall.** Body/verifier reads across every sub-shape confirm this:
   wrong computed numbers (task_000164 y_pred 3.44 vs 3.195; task_000567 PCA
   PC1_Sum 6.9499 vs 7.7709; task_000763 PIN 2778 vs 2394), wrong validation
   logic producing empty deliverables (task_000028 wrote 0 valid payloads vs 2,
   and its own self-verify narrated "empty is correct"; task_000740 empty
   alerts.log; task_000669 empty corpus), and wrong edge-case handling
   (task_000142 symlink target file_A.dat vs financial_records_2021.dat, 5/6
   subtests pass). No harness mechanism produces the correct value; SOUL.md is
   explicit that these are model-capability gaps and must NOT be patched by
   embedding domain knowledge into the prompt. The R2 verify/plan/round-trip
   system prompt is already deployed, general, and strong.

The current pipeline is stable (no processor fires on any passing task), so
there is no mis-parametrized knob to correct either. With no evidence-backed
harness intervention available and the one candidate lever collapsed, the
disciplined choice is to preserve the stable pipeline rather than push a
saturated, now-regression-prone mechanism or inject task-specific knowledge.

### Changes

- `config.yaml` — byte-for-byte copy of R5 (canonicalize ok, checked_templates=0).
- `system_prompt.txt` — byte-for-byte copy of R5 (sibling, required by
  SiblingSystemPromptBuilder).

### Evidence

- Loop max-run measurement (R5 messages.json): loopers task_001044/task_001465/
  task_000032/task_001201/task_000438 all max near-identical run = 3 (anchor and
  sliding); PASSING task_000061 also run = 3 — separator collapsed.
- Banner grep: [LoopTerminator]=0 on all loopers; VERIFIER DEP=0 on all 3 HTTP
  tasks; no processor banner on any of the 10 passing tasks.
- Capability-wall verifier tails: task_000164 y_pred 3.4418 vs 3.195079;
  task_000567 PC1_Sum 6.9499 vs 7.7709; task_000028 valid_payloads len 0 vs 2
  (self-verify: "empty is correct"); task_000142 symlink file_A.dat vs
  financial_records_2021.dat.

### Uncertainty

Risk of a no-op is leaving a real signal on the table. Mitigated: I swept all 40
failures by exit_reason and read representatives of every done-but-wrong
sub-shape; each is capability-bound, and the one harness lever is provably
collapsed. If a future run surfaces a genuinely NEW harness-shaped pattern
(a recurring blocked-but-recoverable tool return, a processor mis-firing on a
passer, or a mechanically-fixable done-but-wrong sub-cluster), that is the next
lever — but it is NOT loop control, which is retired for this benchmark.

## Round 7 — no-op: verify-workflow fires yet capability wall holds

<!-- journal:frontmatter
round: 7
timestamp: 2026-06-07T00:00:00Z
hypothesis_id: h_noop_verify_fires_capwall_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=12/50; +2/-0 gained=task_001201_1340f4e2,task_002138_2e85672e; score 0.2400 >= incumbent(mean) 0.2160 - tol 0.0400
expected_global_gain: "0 flips. Explicit no-op. Fresh R6-run evidence this round (distinct from R6's finding) shows the deployed R2 verify/round-trip system prompt now DEMONSTRABLY FIRES AND EXECUTES CORRECTLY on the value-mismatch cluster — the agent runs the forward-check, confirms it reproduces the given target, then still lands the wrong ground-truth value. That closes the instruction lever as saturated with direct execution evidence (not just 'deployed'). The loop/control lever remains regression-safe and inert on all 10 passers. No new class-level harness deficiency exists, so preserving the stable R5/R6 pipeline is the correct Pareto move."
regression_risk: "None — byte-identical config + sibling system_prompt.txt copied from R6 (md5 044420c4291e005fa03fa83dd25d6b82 / c3d21a500424db9387e4b58505b87cfe). Verified no loop/terminator banner fires on any of the 10 passing tasks this run; the ProactiveVerifierDepGuard banner fires on 8/10 passers but is idempotent (no-op) and does not disturb them."
cost_shift: "Zero — identical pipeline."
rollback_trigger: "N/A (no-op). Next round: pursue a lever ONLY if a genuinely NEW harness-shaped signal appears — a recurring blocked-but-recoverable tool return, a processor mis-firing on a passer, or a mechanically-fixable done-but-wrong sub-cluster (e.g. a missing-deliverable pattern the agent COULD produce but exits before writing). Do NOT re-push loop control (retired) or the verify/plan instruction (saturated with execution evidence)."
-->

### Why

R0-R6 are all flat at pass_rate 0.20 (10/50), R5 config measured 3x at exactly
10/50. This round is an evidence-backed explicit NO-OP, distinct from R6.

The R6 run shifted shape vs prior rounds: **0 exit_reason=error crashes** this
run (the R4/R5 terminators + loop breakers converted the former crashes into
clean exits), 6 budget_exceeded (steps=80), 34 exit=done/no_tool_calls. I swept
all 40 failures by verifier `output_tail`.

The decisive new finding: the deployed R2 verify/round-trip system prompt is not
merely present, it is being **executed correctly** on the value-mismatch cluster
and still fails on ground truth. On task_000763 the agent computed PIN=2778,
explicitly ran the forward check `bash token_algo.sh 2778` → got the given target
`1A3B`, confirmed the round-trip in its self-verify turn, and declared success —
yet the verifier wants 2394. The forward-check the prompt asks for PASSES on the
agent's wrong answer. No harness mechanism can distinguish this; the instruction
lever has hit its ceiling with direct execution evidence, not just deployment.

The rest of the failing set is the same capability wall: wrong computed numbers
(task_000164 y_pred 3.4418 vs 3.195079; task_000567 PC1_Sum 6.9499 vs 7.7709;
task_000925 best_ridge_alpha 1.0 vs 10.0; task_000785 ks_statistic 0.2 vs 0.3;
task_000905 MSE 159841 vs <=1.0; task_000730 Iteration 1733 vs 1716), wrong
logic / empty deliverables (task_000028 valid_payloads 0 vs 2; task_000669 empty
evil corpus; task_001465 2/2 evil bypassed), wrong edge-case (task_000142 symlink
file_A.dat vs financial_records_2021.dat, 5/6 subtests pass), server crashes
during verification (task_000885 n_components(50)>n_features(14)), and two
non-agent verifier faults that are unfixable from the harness (task_002071 the
injected test has a Python SyntaxError `f-string expression part cannot include a
backslash` at collection; task_000998 the agent's own wrong-content operator.py
shadows the stdlib operator module and crashes the verifier bootstrap — a
capability error in file content, not a harness gap). SOUL.md forbids patching
any of these with domain knowledge.

I evaluated one candidate NEW mechanism — a pre-exit named-deliverable existence
guard — and rejected it: the short done-fails (steps 12-27) overwhelmingly have
the file PRESENT with a wrong VALUE (existence guard is a no-op on them), and the
three genuinely-missing-file cases (task_000677 bug_report.txt, task_001201,
task_002033) are loop-bound long tasks where the agent already KNOWS the file is
missing (it narrates it) but cannot produce the content — a nudge cannot conjure
capability. The guard would fire mostly as a no-op and add regression surface on
passers for zero expected flips. Not Pareto-justified.

### Changes

- `config.yaml` — byte-for-byte copy of R6 (canonicalize ok, checked_templates=0).
- `system_prompt.txt` — byte-for-byte copy of R6 (sibling, required by
  SiblingSystemPromptBuilder).

### Evidence

- task_000763 messages.json: agent self-verify turn "Verification: bash
  /home/user/token_algo.sh 2778 outputs 1A3B ✓" — round-trip PASSES on wrong
  answer; verifier wants 2394. Direct proof the verify prompt executes and is
  insufficient.
- Value-mismatch tails: task_000164 3.4418 vs 3.195079; task_000567 6.9499 vs
  7.7709; task_000925 alpha 1.0 vs 10.0; task_000785 ks 0.2 vs 0.3.
- Non-agent verifier faults: task_002071 collection SyntaxError (backslash in
  f-string); task_000998 stdlib-shadowing operator.py crashes pytest bootstrap.
- Regression safety: grep of all 10 passing-task messages.json → 0 loop/
  terminator banners; ProactiveVerifierDepGuard banner on 8/10 passers is
  idempotent no-op.

### Uncertainty

Risk of a second consecutive no-op is leaving a real signal on the table. I
mitigated by sweeping every failure's verifier tail this run (not reusing R6's
read) and by actively designing + rejecting one concrete new mechanism against
the current data. If a future run surfaces a mechanically-fixable done-but-wrong
sub-cluster (a deliverable the agent CAN produce but exits before writing, or a
processor mis-firing on a passer), that is the next lever — but neither loop
control nor the verify/plan instruction, both now confirmed saturated.


## Round 8 — no-op: fresh full sweep confirms capability wall + verifier-infra faults

<!-- journal:frontmatter
round: 8
timestamp: 2026-06-08T00:00:00Z
hypothesis_id: h_noop_capwall_verifier_infra_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=11/50; +1/-2 gained=task_000013_b393db71 lost=task_001185_636b067c,task_002138_2e85672e; score 0.2200 >= incumbent(mean) 0.2167 - tol 0.0400
expected_global_gain: "0 flips. Explicit no-op. A fresh, independent full sweep of all 38 R7 failures (not reusing R6/R7 reads) reclassified one previously-mislabeled failure (task_000998) as a VERIFIER-INFRASTRUCTURE fault rather than an agent-content capability error - but it is single-task AND structurally unfixable from the agent phase (the crash happens in the post-exit verifier, which the harness cannot touch). No new ACTIONABLE harness-shaped lever exists: the loop/control lever is saturated + regression-unsafe (R6), the verify/plan instruction lever is saturated with execution evidence (R7), and the dominant ~28-task cluster is a genuine model-capability wall (wrong computed values / wrong logic / empty deliverables) that SOUL.md forbids patching with domain knowledge. Preserving the regression-safe R7 pipeline is the correct Pareto move."
regression_risk: "None - config.yaml + sibling system_prompt.txt copied byte-for-byte from R7 (md5 044420c4291e005fa03fa83dd25d6b82 / c3d21a500424db9387e4b58505b87cfe). Verified 0 loop/terminator/BLOCKED banners fire on any of the 12 passing tasks this run; the pipeline is inert on the passing set."
cost_shift: "Zero - identical pipeline."
rollback_trigger: "N/A (no-op). Next round: pursue a lever ONLY if a genuinely NEW harness-shaped, AGENT-PHASE-FIXABLE signal appears (a processor mis-firing on a passer, a recurring blocked-but-recoverable tool return, or a mechanically-fixable done-but-wrong sub-cluster the agent COULD produce but exits before writing). Do NOT re-push loop control (retired) or the verify/plan instruction (saturated). Verifier-infra faults (stdlib-shadow, injected-test SyntaxError, python-vs-python3, verifier timeout) are OUT OF SCOPE - logged in NEEDS_FROM_HUMAN.md for the benchmark maintainers."
-->

### Why

R7 measured 12/50 (0.24), up from the long 10/50 (0.20) plateau (same R5/R6/R7
config; the +2 is within the repeat spread 10-12, not a config effect). This
round is an evidence-backed explicit NO-OP grounded in a FRESH independent sweep
of every one of the 38 R7 failures by final_pytest.output_tail (I did not reuse
R6/R7's reads).

Breakdown of the 38 failures this run:
1. ~28 done-but-wrong-value / wrong-logic - the confirmed model-capability
   wall. Representative verifier tails: task_000567 PC1_Sum 6.9499 vs 7.7709;
   task_000164 0.2467 < 0.001 fails; task_000785 ks 0.1 vs <0.0001;
   task_000925 1.0 == 10.0; task_000142 symlink file_A.dat vs
   financial_records_2021.dat; task_000740 CSV rows off-by-content;
   task_000713 primer sequence wrong; task_000013 fuzz-equivalence dict mismatch.
   No harness mechanism produces the correct value; SOUL.md forbids embedding it.
2. ~5 loop/budget-bound (task_000032, task_000438, task_000796, task_000910,
   task_001028, task_001465, task_002146) - the loop lever is retired
   (R4/R5/R6): the separator between loopers and passers collapsed on the R5 run
   (a passing task also reaches near-identical run=3), so no terminate_threshold
   is safe. task_000032 exits done at step 43 with 2/3 tests passing and only
   report.txt does-not-exist failing - but its body shows it looping on an
   uncrackable hash with NO answer to report; a pre-exit file guard cannot
   conjure the content. Capability-bound, not a missing-write the agent could do.
3. ~4 VERIFIER-INFRASTRUCTURE faults (NEW characterization this round) -
   unfixable from the agent phase, logged in NEEDS_FROM_HUMAN.md:
   - task_000998: task MANDATES output at /home/user/operator.py; the verifier's
     pytest puts /home/user on sys.path ahead of stdlib, so CPython's bootstrap
     from-operator-import-eq resolves to the agent file -> circular-import crash,
     Interrupted: 1 error during collection. The agent's work is never scored.
     The agent cannot both satisfy the task and avoid poisoning the verifier path;
     this is a benchmark filename-collision bug, not an agent-content error (R7
     mislabeled it "capability error in file content"). Single task; below the
     two-task idiosyncratic threshold; and no agent-phase processor can reorder the
     post-exit verifier's sys.path.
   - task_002071: injected /tmp/test_final_state.py line 54 has a
     SyntaxError (f-string expression part cannot include a backslash) - the test
     itself is invalid on py3.10; no agent action passes it.
   - task_000709: verifier shells out to python (only python3 present) ->
     FileNotFoundError python.
   - task_001074: verifier command timed out after 180s.

I evaluated exactly one candidate NEW agent-phase mechanism - a pre-exit
named-deliverable-exists / stdlib-shadow-rename guard - and rejected it on two
grounds: (a) the stdlib-shadow case is single-task, structurally verifier-phase,
and the task requires the exact colliding filename (renaming would fail the task);
(b) the short done-fails overwhelmingly have the file PRESENT with a wrong VALUE,
so an existence guard is a no-op on them and adds regression surface on passers
for zero expected flips. Not Pareto-justified.

The current pipeline is regression-safe (0 banners on all 12 passers), so there
is no mis-parametrized knob to correct either. With no evidence-backed
agent-phase harness intervention available, the disciplined choice is to preserve
the stable R7 pipeline.

### Changes

- config.yaml - byte-for-byte copy of R7 (canonicalize ok, checked_templates=0,
  md5 044420c4291e005fa03fa83dd25d6b82).
- system_prompt.txt - byte-for-byte copy of R7 (sibling, required by
  SiblingSystemPromptBuilder; md5 c3d21a500424db9387e4b58505b87cfe).
- _meta_scratch/NEEDS_FROM_HUMAN.md - the 4 verifier-infrastructure faults
  above, for benchmark maintainers.

### Evidence

- Full-sweep script _meta_scratch/sweep.py output: 28 value/logic mismatches,
  ~5 loop/budget, 4 verifier-infra.
- task_000998 verifier tail: File /home/user/operator.py line 12 import json ...
  ImportError: cannot import name namedtuple from partially initialized module
  collections (circular import); task body confirms the task requires
  "Your Python script (/home/user/operator.py)".
- Regression safety: grep of all 12 passing-task messages.json -> LoopTerminator=0
  and BLOCKED=0 on every one.
- History: R5/R6/R7 all the same config (md5 044420...), scored 10/10/10/12/12
  across repeats - the +2 is repeat noise, not a config gain.

### Uncertainty

Risk of a third consecutive no-op is leaving a real signal on the table. Mitigated
by a fresh independent sweep of every failure this round (not reusing prior reads)
and by designing + rejecting one concrete new agent-phase mechanism against the
current data. The one genuinely-new finding (task_000998 is a verifier-infra fault,
not agent capability) is real but out of harness scope and single-task. If a future
run surfaces a mechanically-fixable, AGENT-PHASE done-but-wrong sub-cluster (two-plus
tasks, deliverable the agent CAN produce but exits before writing) or a processor
mis-firing on a passer, that is the next lever - not loop control, not verify/plan
instruction, both confirmed saturated, and not verifier-infra, which needs a
maintainer fix.

## Round 9 — server-persistence detach discipline (instruction)

<!-- journal:frontmatter
round: 9
timestamp: 2026-06-09T00:00:00Z
hypothesis_id: h_server_persistence_prompt_v1
levers: [instruction]
predicted_affected: [task_002108_a8cfbf2a, task_002138_2e85672e, task_000910_16cc0daf]
cited_candidates: [C-006]
gating_outcome: accepted
gating_attribution: score=10/50; score 0.2000 >= incumbent(mean) 0.2143 - tol 0.0400 (final-round scoring)
expected_global_gain: "The server/service cluster is the single largest harness-adjacent failing cluster this run (8/8 failing across 5 domains). Add a general long-running-service discipline to the sibling prompt: launch fully detached (own session/process group via setsid + all fds redirected to a log), self-verify reachability from a SEPARATE Bash call after launch, and never end the session with a teardown. Targets the sub-shape where the server code is correct but the process dies at the agent->verifier phase boundary or is killed by the agent's own final command. Generalizes to any future task that must leave a listener alive at check time."
regression_risk: "LOW. Additive, strategy-only edit to sibling system_prompt.txt; config.yaml byte-identical to R8 (md5 044420c4291e005fa03fa83dd25d6b82), no processor/tool/template authored, no task literals. The new section is explicitly conditional on 'if the task requires a process to be running when checked', so non-server tasks are untouched. Small budget cost of an extra reachability-check Bash call on server tasks (which already fail). No processor fires on any of the 11 passers this run (LoopTerminator=0, BLOCKED=0; only the idempotent VERIFIER DEP no-op banner)."
cost_shift: "Neutral-to-slightly-up on the server cluster only (a couple extra reachability Bash calls); negligible elsewhere. Retained R2/R8 don't-loop / bank-partial-credit rules continue to cap the runaway tail."
rollback_trigger: "Revert to the R8 prompt (md5 c3d21a500424db9387e4b58505b87cfe) if R10 pass_rate < R9 baseline OR a previously-passing task regresses with over-eager server relaunching visibly consuming its budget."
-->

### Why

R8 measured 11/50 (0.22), on the same long ~10-12/50 plateau. After three
consecutive evidence-backed no-ops (R6/R7/R8) I ran a fresh independent sweep of
all 39 failures by final_pytest.output_tail and re-read the trajectory bodies of
the server cluster rather than trusting the prior "server-capability gap" label.

The failure distribution is dominated by a genuine model-capability wall (~13
value mismatches, ~12 could-not-compute-so-missing-file), which SOUL.md forbids
patching with domain knowledge and which loop-control (retired R6) and the R2/R7
round-trip-verify instruction (saturated with execution evidence R7) already
cover. But the SERVER/SERVICE cluster (8/8 failing) contains a distinct,
harness-shaped, agent-phase-fixable sub-shape the prior rounds mislabeled: the
process is alive at the end of the agent session yet unreachable in the separate
post-exit verifier phase, and in one case the agent's own final command tears
the service down. The TB2 playbook names this exact structural failure
("Background process dies after agent exits — nohup/& persists only if the
final Bash call does not kill them"), and one verifier's error text literally
asks "Did you start it in the background?" — proving persistence is a scored
dimension. The R8 prompt already carries a weak one-liner ("background with
nohup/&; do not kill it in your final command") that task_002138 directly
violates and task_002108 shows to be insufficient, so this is a different shape
at the instruction lever with NEW evidence, not a re-push of the saturated
round-trip rule.

### Changes

- system_prompt.txt (sibling, loaded by SiblingSystemPromptBuilder): added a
  "LONG-RUNNING SERVICES" section — launch fully detached (own session/process
  group, all fds redirected to a log; a launch that blocks the shell was not
  backgrounded correctly), then in a SEPARATE Bash call confirm the process is
  alive AND reachable on the named port/socket/path (query the real endpoint,
  not just ps), and NEVER make the final action a teardown (pkill/kill/
  systemctl stop/cleanup that removes the running process or its socket).
  Strategy-only, general, no task ids / constants / code-to-copy. All prior
  R2/R8 workflow + verify + don't-loop rules retained verbatim.
- config.yaml — byte-for-byte copy of R8 (md5 044420c4291e005fa03fa83dd25d6b82;
  canonicalize ok, checked_templates=0; dry_fire ok, likely_bugs=0). Only the
  sibling prompt changed.

### Evidence

- task_002108_a8cfbf2a: agent launched a nohup backgrounded frame_server with
  output redirected to a log; its own final ps shows the process ALIVE
  (725 ... frame_server) yet the verifier tail reads "The server on
  127.0.0.1:8000 is not reachable. Did you start it in the background?" —
  running-at-session-end, dead-at-verify.
- task_002138_2e85672e: the agent's VERY LAST Bash command is a pkill of its own
  socat TCP listener — it kills the service as the final action; verifier then
  gets Connection refused on 127.0.0.1:9000.
- task_000910_16cc0daf: backgrounds monitor_daemon with a plain & (no setsid);
  verifier gets IncompleteRead / Connection broken on the HTTP endpoint.
- Regression safety: grep of all 11 passing-task messages.json -> LoopTerminator=0,
  BLOCKED=0 on every one; only the idempotent VERIFIER DEP banner (2x) appears.

### Uncertainty

Partial-yes retroactive check. The rule cannot fix server tasks whose CODE is
broken (task_000796 key-decrypt, task_001013 401-auth logic, task_001037 server
crashes with returncode 1) — those are capability-bound and the gain is
concentrated on the persistence/teardown sub-shape (strongest on task_002108 and
task_002138). If R10 shows the server cluster still failing with real code-level
assertions (auth/logic/crash) and no reachability gain, the persistence sub-shape
was already handled by the weak R8 line and the instruction lever is fully
saturated for this benchmark — move off it. Risk of leaving signal on the table
is mitigated: I swept every failure fresh and confirmed the dominant clusters are
the known capability wall, choosing the one remaining harness-shaped, low-risk
lever over a fourth no-op.
