# Evolve journal — tmax-coev-rep10-i2

## Round 1 — functional verify gate

<!-- journal:frontmatter
round: 1
timestamp: 2026-04-27T00:00:00Z
hypothesis_id: h_functional_verify_v1
levers: [instruction, control]
predicted_affected: [task_000059_e4b842f5, task_000118_3043e92d, task_000115_40adb445, task_000264_ab8c7253, task_000396_e56917e2, task_001937_ac874115]
cited_candidates: [C-001, C-002]
gating_outcome: accepted
gating_attribution: score=23/50; +5/-3 gained=task_000090_d0fa254a,task_000505_50b5162d,task_000533_01bba2c2,task_001095_31b3cce4 lost=task_000191_578636e9,task_000714_2b74124a,task_000764_a0052743; score 0.4600 >= incumbent(mean) 0.4200 - tol 0.0400
expected_global_gain: "Flip a chunk of the large done+'1 failed' near-miss cluster (23/29 R0 failures exited done after only a static self-check) by forcing one genuine end-to-end run before exit"
regression_risk: "Bounded extra verify cycles (hard cap max_nudges=2) could add a few Bash calls on already-correct tasks; read-only heuristic could misclassify a legit inspection-only final turn, costing one extra nudge — never an infinite loop"
cost_shift: "+bounded tokens/task (<=2 extra verify cycles + a few functional-test Bash calls); net positive if it flips >=2 near-miss tasks"
rollback_trigger: "If R2 pass_rate is flat/down AND median steps/cost rise materially, revert to R0's single-shot self-verify"
-->

### Why

R0 scored 21/50 (42%). The dominant, systemic failure shape: **23 of 29
failures exited `exit_reason=done`** — the agent believed it was finished.
Only 4 were `budget_exceeded`, 2 `error`. Nearly every failing pytest tail
reads "1 failed, N passed": the agent got most of the task right but shipped
a semantic/functional bug (wrong numeric constant, a daemon that never
actually keeps the invariant, a query using the wrong index) that a real
end-to-end run would have caught. The existing single-shot
`CustomSelfVerifyProcessor` fires exactly once (`_tb2_self_verify` shows 2
refs = 1 call + 1 ack in ~all failing done-tasks); the agent answers it with
a **shallow static re-check** (ls / grep / cat) and immediately re-exits.

### Changes

- `system_prompt.txt` (C-001) — rewrote the sidecar prompt to make
  verification *functional, not cosmetic*: survey → restate every concrete
  checkable requirement (exact paths, constants, thresholds, formats,
  runtime behavior) → implement to those values → before stopping, RUN the
  solution end-to-end against the real scenario and compare observed behavior
  value-by-value, recomputing required constants independently. General
  strategy only; no task literals.
- `processors/functional_verify_gate.py` + `config.yaml` (C-002) — replaced
  the single-shot `CustomSelfVerifyProcessor` with a stateful, functional-
  aware `FunctionalVerifyGateProcessor` (same `_singleton_group` slot,
  `_hook_='*'`, `max_nudges=2`). It nudges on exit intent, tracks whether the
  agent runs a *substantive* command (executes/exercises the solution) vs.
  purely read-only inspection (ls/cat/grep/head/find/...), and re-nudges once
  more (bounded) when an exit attempt is preceded only by inspection.

### Evidence

- `task_000059_e4b842f5` final turn: "verified" by `ls -lh final_mac.txt &&
  cat final_mac.txt` + `cat verify_mac.py` — never independently recomputed
  the MAC; verifier failed `test_verify_mac_py_python3_compatible`.
- `task_000118_3043e92d` final turn: "verified" by `ls -lh
  deployment_monitor.py` and `grep -n THRESHOLD_BYTES/SIGSTOP` — never ran
  `run_deployment.sh` to confirm the monitor keeps disk under quota; verifier
  failed `test_deployment_monitor`.
- Aggregate: `exit_reason=done` on 23/29 failures; `_tb2_self_verify`==2 refs
  in ~all of them; post-ack turn contains only read-only Bash then re-exit.

### Uncertainty

Two changes ship together (instruction + control), so attribution will be
ambiguous — but they are independent (either helps alone) and both target the
same cluster via complementary mechanisms (knowledge vs. enforcement). The
main risk is that some near-misses are genuine capability gaps the model
can't fix even after running its own test; those will stay F. Watch for cost
inflation without pass-rate gain — that's the rollback signal. Structural
failures (e.g. task_000958's verifier missing `requests`) are out of scope.

## Round 2 — warn-only loop breaker

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-28T00:00:00Z
hypothesis_id: h_loop_break_warnonly_v1
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000118_3043e92d, task_000191_578636e9, task_000257_3cd35e11, task_000010_644ab1c2, task_000300_7d6b511c, task_000396_e56917e2, task_000442_502566d6, task_001653_c4cafa73]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=23/50; +3/-3 gained=task_000191_578636e9,task_000396_e56917e2,task_000748_c9807703 lost=task_000090_d0fa254a,task_000533_01bba2c2,task_001883_6e356e21; score 0.4600 >= incumbent(mean) 0.4600 - tol 0.0400
expected_global_gain: "Flip a subset of the dominant budget_exceeded cluster (11/27 R1 failures exited at the 80-step cap, +1 error) whose shared root cause is a byte-identical no-progress command loop; an early warn frees the remaining budget for a different approach"
regression_risk: "3 currently-passing tasks (878/890/505) also repeat identical commands deliberately; mitigated by making the guard WARN-ONLY (threshold=999, never raises) so no pass can become loop_detected - they only see an ignorable advisory line in a tool result"
cost_shift: "Near-neutral to negative: a few warning tokens on affected tasks, but converting 80-step budget_exceeded runs into earlier completions should reduce median steps/cost on the cluster"
rollback_trigger: "If R3 pass_rate is flat/down while budget_exceeded count is unchanged, OR any of 878/890/505 flips T to F, revert the LoopDetectionProcessor"
-->

### Why

R1 landed at 23/50 (accepted). The largest remaining failure cluster is
exit_reason=budget_exceeded at the hard 80-step cap: 11 of 27 R1 failures,
plus 1 error. Extracting the Bash command stream from the messages log shows
the dominant shape is a byte-identical command repeated consecutively with no
change of approach - the agent re-issues the exact same failing command instead
of diagnosing why it fails, exhausting the whole step budget. Max
consecutive-identical run: task_000015=21 (same 21-line OCR heredoc), 1653=25
(same etl.c write), 191=12 (same go build + go test), 118=11 (same
pkill/restart), 257=9. The pipeline had no mechanism to notice "you have run
this exact command N times with the same result - change approach". A
well-designed LoopDetectionProcessor already exists in harnessx but was absent
from the config.

### Changes

- config.yaml (C-001) - added
  harnessx.processors.control.loop_detection.LoopDetectionProcessor in
  WARN-ONLY mode: warn_threshold=3 (advisory appended to tool result after 3
  byte-identical consecutive Bash calls), threshold=999 (hard-raise disabled -
  never terminates a task), name_warn_threshold=999 (Strategy 2 disabled
  because every call here is Bash and name-only would fire on nearly every
  task). window_size=12, compaction_drop_threshold=5 (defaults).
- Carried system_prompt.txt (R1 functional-verify rewrite) and the R1
  FunctionalVerifyGateProcessor + R0 StepBudgetDeadlineProcessor forward
  unchanged.

### Evidence

- task_000015_89886d8d: the same pytesseract python heredoc appears 21x
  verbatim; exit_reason=budget_exceeded at 80 steps.
- task_001653_c4cafa73: the same etl.c heredoc written 37x, 25 in one unbroken
  consecutive run; exit_reason=error.
- task_000118_3043e92d: the same pkill/sleep/restart command repeated 11x
  consecutively; budget_exceeded.
- task_000191_578636e9: the same go build + go test command repeated 12x
  consecutively; budget_exceeded (was a R0 pass, R1 regressed it).
- Aggregate: at max-consecutive-run >= 3, 12 failing tasks would receive the
  warn vs only 3 passing (878 run=11, 890 run=9, 505 run=3) - and those 3 only
  get a harmless advisory, never a termination.

### Uncertainty

The warn is soft - the agent may ignore it and keep looping, in which case the
task still fails (no worse than now). The gain depends on the model reacting to
"try something fundamentally different". Risk that a task legitimately polling a
slow process gets a distracting advisory; mitigated by requiring byte-identical
consecutive calls (polling usually varies the target/sleep or interleaves
checks). If R3 shows no budget_exceeded reduction and cost is flat, this lever
is the wrong shape for this cluster and should be reverted.


## Round 3 — decompose + loop-escape strategy

<!-- journal:frontmatter
round: 3
timestamp: 2026-04-29T00:00:00Z
hypothesis_id: h_decompose_loop_escape_v1
levers: [instruction]
predicted_affected: [task_000028_7fe033ac, task_000300_7d6b511c, task_000010_644ab1c2, task_000585_049544a3]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=26/50; +4/-1 gained=task_000090_d0fa254a,task_000257_3cd35e11,task_000533_01bba2c2,task_001883_6e356e21 lost=task_002073_a89d5ed6; score 0.5200 >= incumbent(mean) 0.4600 - tol 0.0400
expected_global_gain: "Flip a subset of the 7 budget_exceeded thrash tasks by converting reactive same-mechanism retries into early decomposition plus a genuine strategy switch, committing easy deliverables before a hard sub-goal exhausts the step budget; generalizes across domains (k8s operator, cmake build, ffprobe pipeline, service daemon) because the shared root cause is mechanism-fixation, not domain knowledge"
regression_risk: "Longer prompt; a simple passing task could over-engineer via the decompose guidance. Mitigated by gating that guidance on multi-step / multiple-files / service-plus-client tasks. R1 survey/restate/functional-verify content preserved byte-for-byte, so the done-cluster near-miss behavior is unchanged"
cost_shift: "Near-neutral to slightly negative on the thrash cluster (abandon a failing mechanism after 2 tries instead of ~16); plus small prompt tokens/task; net positive if it flips 2+ budget tasks"
rollback_trigger: "If R4 pass_rate is flat/down AND budget_exceeded count is unchanged, revert the R3 prompt additions to the R2 sidecar prompt"
-->

### Why

R1 (functional-verify) and R2 (warn-only loop breaker) both landed flat at
23/50. The dominant remaining harness-addressable shape is the budget_exceeded
cluster (7/27 R2 failures, down from R1's 11 after the loop-breaker cut the
byte-identical loops, but the semantic thrash remains). Reading the bodies:
the SAME agents that hit budget_exceeded repeatedly say "I'm stuck in a loop,
let me try a different approach" (16x in task_000028, 17x in task_000300) yet
keep issuing cosmetic variants of the same failing command. The R2
LoopDetection warn (Control) only re-asserts the awareness the agent already
has; what it lacks is the recovery strategy. Separately, hard sub-goals
swallow the whole budget before the easy deliverables exist (task_000010
never wrote /home/user/operator.py despite 33 distinct steps debugging the
port-forward). Both shapes are strategy gaps, addressable at the instruction
lever.

### Changes

- system_prompt.txt (C-001) - added two general strategies to the carried-
  forward R1 prompt: (1) a decompose-first step for multi-step tasks - write an
  ordered sub-goal plan, get a minimal end-to-end version with every deliverable
  on disk before perfecting one stage; (2) a loop-escape protocol - after 2
  failures, state the root cause and switch to a STRUCTURALLY different
  mechanism (not a tweaked flag), and treat "I'm stuck in a loop" as the signal
  to abandon the current mechanism entirely. No task literals; general strategy.
- config.yaml - no processor changes; carries R1 FunctionalVerifyGate, R0
  StepBudgetDeadline, and R2 LoopDetection (warn-only) forward unchanged. Only
  the sidecar prompt changed (SiblingSystemPromptBuilder reads it).

### Evidence

- task_000028_7fe033ac: exit_reason=budget_exceeded (80 steps); assistant
  turns 4-6 repeat "I'm stuck in a loop. Let me try a different approach to get
  the frame count from ffprobe." while re-running near-identical ffprobe
  commands; 16 loop/stuck mentions across the run.
- task_000300_7d6b511c: budget_exceeded; "I keep getting stuck in a loop
  running the same cmake command ... I need to stop the loop and take a
  fundamentally different approach"; 17 loop/stuck mentions.
- task_000010_644ab1c2: 33 distinct Bash commands debugging the mock-API
  port-forward; final_pytest "Operator script /home/user/operator.py does not
  exist" - the required deliverable was never committed while a hard sub-goal
  ate the budget.
- Aggregate: max consecutive byte-identical calls dropped to 1-5 (028=5,
  300=4) so R2's exact-match warn (warn_threshold=3) rarely fires; loops are
  now argument-varying - a Configuration tune of the exact-match guard cannot
  catch them, and name-only Strategy 2 would fire on nearly every Bash call.

### Uncertainty

Instruction landed once (R1) then plateaued, so a prompt-only round is a
calculated bet - the wager is that the specific untried strategy (mechanism-
switch plus decomposition, the playbook's "biggest single lever" for multi-step
tasks) closes tasks the functional-verify content could not. Risk: the model
may keep thrashing despite the protocol (soft guidance, no enforcement) - then
those tasks stay F, no worse than now. If R4 shows no budget_exceeded reduction
and cost is flat, instruction is exhausted for this cluster; next round should
move to a Control mechanism that actively injects a re-plan directive when
argument-varying loops are detected, or accept these as capability gaps.

## Round 4 — windowed no-progress loop breaker

<!-- journal:frontmatter
round: 4
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_windowed_repeat_break_v1
levers: [control]
predicted_affected: [task_000958_4bb2b05d, task_000118_3043e92d, task_002073_a89d5ed6]
cited_candidates: [C-001]
gating_outcome: reverted
gating_attribution: score=22/50; +0/-4 lost=task_000191_578636e9,task_000257_3cd35e11,task_000320_38324774,task_000533_01bba2c2; score 0.4400 < incumbent(mean) 0.5200 - tol 0.0400 -> revert to R3
expected_global_gain: "Redirect the R3 budget_exceeded cluster (7/24) off interleaved no-progress spin toward the upstream bug, freeing wasted steps on 000958/000118 and the empty-loop steps on 002073"
regression_risk: "A passing task (task_000865_d9a96bd4) legitimately repeats a command 8x within an 8-window; warn-only (never raises) means it can only ever see a harmless advisory, not a loop_detected flip"
cost_shift: "Near-neutral; a few advisory tokens on affected tasks, offset by shorter runs if any 80-step spin completes earlier"
rollback_trigger: "If R5 pass_rate flat/down AND budget_exceeded count unchanged, OR task_000865/task_001197 flips T->F, revert WindowedRepeatBreakerProcessor"
-->

### Why

R3 landed at 26/50. Of the 24 remaining failures, 7 are `budget_exceeded`
at the 80-step cap. Reading the command streams for that cluster, the dominant
harness-addressable shape is an *interleaved* (non-consecutive) repeat that the
R2 `LoopDetectionProcessor` structurally cannot catch: it counts only the
strictly-CONSECUTIVE run at the tail of its window, so one stray inspection or
empty command between repeats resets the count and `warn_threshold=3` never
trips. R3's own Uncertainty note explicitly recommended moving to a Control
mechanism that fires on argument/interleave-varying loops — this round ships
exactly that.

### Changes

- `processors/windowed_repeat_breaker.py` — new `WindowedRepeatBreakerProcessor`
  (`MultiHookProcessor`, warn-only, order=21 just after LoopDetection). Two
  orthogonal signals: (1) windowed exact-repeat (same command at least 5 times
  within the last 8 tool calls) triggers an advisory to stop re-running and
  read/fix the source; (2) empty/no-op run (at least 3 blank commands in a row)
  triggers an advisory to run a real command or finish. Never raises; appends
  advisory to the tool result only (`event.messages` never mutated). Ignores the
  FunctionalVerifyGate keepalive tool; cooldown=4 prevents advisory spam.
- `config.yaml` — register the processor after `LoopDetectionProcessor`
  (window_size=8, repeat_threshold=5, empty_threshold=3, cooldown=4).

### Evidence

- `task_000958_4bb2b05d` messages.json: service restart+curl cycle re-run 16x
  (max 8-of-8 in an 8-window; first tripwire around step 9) — never edits the
  C++ source producing the failing curl result. `exit_reason=budget_exceeded`.
- `task_000118_3043e92d` messages.json: monitor start+test cycle re-run 19x
  (max 8-of-8; tripwire around step 6), same failing result each time.
- `task_002073_a89d5ed6` messages.json: last ~20 calls alternate an empty
  command and the same `echo "=== Script Verification ==="` check (13+ repeats)
  — a degenerate verify/empty oscillation to budget_exceeded.
- All three have consecutive-run of 3 or fewer, so the R2 consecutive warn
  never fires.

### Uncertainty

Warn-only: the gain is conditional on the model reacting to the advisory (the
R2/R3 soft-guidance bet). A warn cannot fail a task, so no regression path exists
beyond a few advisory tokens on the legit-repeat passing task (task_000865). If
R5 shows no budget_exceeded reduction, the interleaved-loop hypothesis is
exhausted for this cluster and the remaining budget failures should be treated
as capability gaps (wrong-implementation bugs, e.g. task_002073's evil-log
handling) rather than loop-shape problems.

## Round 6 — no-op: remaining failures are capability gaps / variance

<!-- journal:frontmatter
round: 6
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_noop_levers_exhausted_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=21/50; +5/-5 gained=task_000320_38324774,task_000426_b9e2ee44,task_000714_2b74124a,task_000764_a0052743 lost=task_000090_d0fa254a,task_000191_578636e9,task_000505_50b5162d,task_000748_c9807703; score 0.4200 >= incumbent(mean) 0.4450 - tol 0.0400
expected_global_gain: "0 flips expected; protects the fragile R3 incumbent (26/21/21) from a variance-amplifying control change like the reverted R4"
regression_risk: "None from config (byte-identical R3 copy). Only risk is opportunity cost of not shipping - judged lower than the R4-style regression risk of shipping an ungrounded change"
cost_shift: "Zero - no config change"
rollback_trigger: "N/A (no change). Next round should revisit only if a NEW harness-addressable shape appears, not the exhausted loop/verify/deadline shapes"
-->

### Why

R5 re-measured the R3 config at 21/50 (R3 repeats 26,21,21 - mean 22.7,
fragile). Full sweep of the R5 round (50 tasks): 21 pass, 29 fail. Breakdown:
17 `done`+near-miss ("1 failed, N passed"), 11 `budget_exceeded`, 1 `error`.
Per-task history R0-R5 shows ~20 tasks NEVER passed in any round
(010,015,028,059,115,140,264,300,345,426,442,585,661,933,958,1017,1093,1653,
1781,1937), and the swing between R3=26 and R5=21 on the IDENTICAL config hash
(0140f0bb) is pure model sampling variance on a fragile band
(320,396,1883,533,257 flip with no config cause).

Every harness lever targeting the two live clusters has been tried and has
plateaued or been reverted, and each new corrective candidate I could construct
fails its retroactive check because the shape is a symptom of a capability gap,
not the blocker:

- Near-miss `done` cluster (17): the R1 FunctionalVerifyGate fires exactly once
  (verify_refs==2) in every sampled near-miss; the agent DOES run a substantive
  command after the nudge, then exits. Failures are wrong VALUES/FORMATS, not
  skipped verification: task_000320 short git hash `a55a9aa` vs required 40-char;
  task_000396 max deviation 0.577 vs less-than 0.1; task_000764 t_statistic
  -0.53 vs 1.19; task_001653 wrong centroid; task_000345 NaN vs 0.1537. Model
  implementation bugs - no harness mechanism computes the correct value.
- `budget_exceeded` cluster (11): only 4/11 have a MISSING deliverable
  (010,118,533,1883 - the exact case StepBudgetDeadlineProcessor targets, and it
  fires); the other 7 wrote WRONG outputs (capability gaps). Loops are now
  argument-varying (R2 consecutive-warn barely fires), and the reverted R4
  windowed-repeat breaker already showed a control change here amplifies
  variance (-4 non-target tasks: 191/257/320/533).
- The one CLEAN zero-regression signal found - consecutive byte-identical
  READ-ONLY commands (only 4 tasks reach 2 or more: 1937=7x, 585=3x, 010=3x,
  533=2x; ZERO passing tasks, incl. 505/865 whose 28x/24x identical runs are
  process-polling NOT read-only) - fails the corrective retroactive check: on all
  4 the loop is downstream of the real blocker (1937 fails on numeric deviation
  regardless; terminating just fails faster). Per the analyze skill, a tighter
  loop detector requires the loop to be the actual blocker, not the consequence
  of it. Net global gain ~0, adds config surface - dropped.

### Changes

- `config.yaml` - byte-identical copy of R3 incumbent (md5 6cc7bf8a...).
  Explicit no-op. Canonicalize returned ok=true.

### Evidence

- Per-task R0-R5 matrix: ~20 tasks 000000 (never passed); R3=26 vs R5=21 on the
  SAME config hash 0140f0bb -> variance, not a config regression to chase.
- FunctionalVerifyGate: verify_refs==2 on all 10 sampled near-misses -> gate
  fires once, agent runs one substantive check, exits with a wrong value.
- task_000320 final_pytest: got `a55a9aa` vs Expected `a55a9aa9902c35a...` -
  correct method (git bisect), wrong output precision -> capability gap.
- Read-only consecutive-loop scan: {1937:7, 585:3, 010:3, 533:2}, all fail,
  0 passing tasks affected; passing 505/865 max read-only run == 1.
- Missing-deliverable scan on budget cluster: 4/11 missing, 7/11 wrong-output.

### Capability-gap memo (no harness fix - skip)

- task_000320: requires emitting the FULL 40-char commit hash, not
  `git rev-parse --short`; model chose short form - capability gap.
- task_000396 / task_000764 / task_000345 / task_001653: require a correct
  numeric simulation/statistic result; model produced wrong values - capability
  gaps; no harness mechanism computes the right number.
- task_001937 / task_000028 / task_000300 / task_000426 / task_000442 /
  task_000585 / task_000958: budget_exceeded with wrong-but-present outputs;
  argument-varying debug thrash on a hard implementation - capability gaps;
  loop/deadline/verify levers already applied and exhausted.
- Fragile tasks (090,191,320,396,505,533,748,1095,1883,2073): flip with no
  config cause between identical-config rounds -> model sampling variance; not
  harness-addressable.

### Uncertainty

The bet is that protecting the incumbent beats shipping an ungrounded change
against a fragile baseline. Risk: if a future round finds a genuinely NEW
harness-addressable shape - e.g. confirmed evidence that CompactionProcessor
evicts StepBudget/loop reminders before step 80 (the compacted logs hinted at
this: budget tasks showed 0-1 reminder msgs in the final 70-msg log despite
reaching step 80) - that would justify a targeted Control candidate marking
deadline reminders non-evictable. That evidence was suggestive but not
confirmable from compacted logs this round, so it was not shipped.

## Round 7 — restore dropped R3 enriched prompt

<!-- journal:frontmatter
round: 7
timestamp: 2026-06-03T00:00:00Z
hypothesis_id: h_restore_r3_prompt_v1
levers: [instruction]
predicted_affected: [task_000090_d0fa254a, task_000257_3cd35e11, task_000533_01bba2c2, task_001883_6e356e21]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=24/50; score 0.4800 >= incumbent(mean) 0.4520 - tol 0.0400 (final-round scoring)
expected_global_gain: "Restore the accepted R3 incumbent's true behavior (best 26/50): the R6 no-op copied config.yaml byte-for-byte but did NOT co-locate the 51-line R3 enriched system_prompt.txt, so the r6-traj (21/50) silently ran with the R0 5-line DEFAULT prompt, losing all validated survey/restate/decompose/functional-verify content the incumbent depends on"
regression_risk: "Near-zero: this restores the byte-identical accepted R3 artifact (md5 ba441c35) to the location SiblingSystemPromptBuilder reads (next to config.yaml). No new code, no processor surface. Worst case the r6-traj 21/50 was pure variance and the restore is a neutral no-op on a validated incumbent"
cost_shift: "Small positive prompt tokens/task (+~46 system-prompt lines), offset by fewer thrash steps on multi-step tasks (the decompose + loop-escape content was accepted partly for cutting budget_exceeded thrash). Net near-neutral to slightly negative"
rollback_trigger: "If R8 measures at/below the default-prompt baseline (~21) AND median steps/cost rise, the enriched prompt is not carrying weight on this model — revert to the default 5-line prompt"
-->

### Why

Diagnostic finding, not a new failure cluster. R6 was an explicit no-op that
copied the R3 incumbent config.yaml byte-for-byte (md5 6cc7bf8a). But the
active prompt builder is `SiblingSystemPromptBuilder`, which resolves
`system_prompt.txt` from the directory of the active config YAML. The R6
output dir contains only the 5-line DEFAULT prompt (md5 914fb4a8, byte-
identical to the R0 baseline) — the 51-line R3 enriched prompt (md5 ba441c35)
that R1 and R3 were ACCEPTED for was never co-located. So the r6-traj measured
at 21/50 actually ran the R0 default prompt, silently reverting the two
accepted Instruction rounds. R3's best draw was 26/50; part of the 26→21 gap
is plausibly this dropped prompt, not just sampling variance. Everything else
(loop/verify/deadline levers) was confirmed exhausted in R4/R6, and the only
new `error` exit this round (task_000257) is a capability-gap RLE puzzle where
LoopDetection warns at 13 consecutive identical calls but the model keeps
looping — not harness-addressable (Bash-only env, no new tools per playbook).

### Changes

- `system_prompt.txt` (C-001) — restore the accepted R3 enriched sidecar
  prompt (survey -> restate requirements -> decompose -> functional-verify ->
  loop-escape; md5 ba441c35) into the R7 output dir so
  SiblingSystemPromptBuilder resolves it next to config.yaml.
- `config.yaml` (C-001) — byte-identical copy of the R3/R6 incumbent
  (md5 6cc7bf8a). No processor changes. The change vs the r6-traj is purely
  restoring the co-located prompt the no-op dropped.

### Evidence

- R3/system_prompt.txt md5 = ba441c35 (51 lines, enriched); R6/system_prompt.txt
  md5 = 914fb4a8 (5 lines, == R0 default). The enriched content is absent from
  the round the r6-traj was measured on.
- config.yaml (R3 == R6, md5 6cc7bf8a) declares
  `recipe.tmax_eval.prompt_builder.SiblingSystemPromptBuilder`; the builder
  reads `system_prompt.txt` beside the active config, so the file must be
  co-located to take effect — it was not in R6.
- R3 journal gate credited gained = task_000090, task_000257, task_000533,
  task_001883 for the enriched-prompt instruction change; these are the
  cluster the restore protects.
- r6-traj summary.json: 21/50 vs R3 best 26/50 on the same config hash.
- task_000257 messages.json: `[LoopDetection] ... issued 13 times in a row`
  fires, agent still loops, exit_reason=error — capability gap, not a harness
  gap (Bash-only, per tb2-playbook).

### Uncertainty

The wager is that the r6-traj 21/50 partly reflects the missing prompt rather
than pure variance. If R8 lands at/below 21 with the enriched prompt restored,
the prompt gives this model nothing and the incumbent should be simplified to
the default. Because R3's own repeats already spanned 26/21/21/21, a single R8
draw near the low end is not decisive — read it against the repeat spread, not
as a point estimate. No regression path beyond a few extra prompt tokens, since
the artifact is the exact accepted R3 content restored to its intended location.
