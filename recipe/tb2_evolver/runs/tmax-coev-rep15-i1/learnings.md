## Round 1 — break truncation spiral (force-act)

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-30T04:18:22Z
hypothesis_id: h_length_recovery_forceact_v1
levers: [control, instruction]
predicted_affected: [task_000010_644ab1c2, task_000118_3043e92d, task_000264_ab8c7253, task_001031_a8f0eb37, task_001321_658ce4a8]
cited_candidates: [C-001, C-002]
gating_outcome: accepted
gating_attribution: score=29/50; +3/-4 gained=task_000028_7fe033ac,task_001090_c61c71f2,task_001515_eed714e6 lost=task_000536_9c16e8ef,task_000587_9862bb19,task_000740_59416444,task_001701_95e3bbcb; score 0.5800 >= incumbent(mean) 0.6000 - tol 0.0400
expected_global_gain: "Flips some/all of the 5-task budget_exceeded cluster (10% of sample) by breaking the reasoning-only max_tokens spiral and returning wasted steps to productive work; generalizes to any verbose-model truncation spiral."
regression_risk: "Low — same singleton group replaces the R0 recovery processor; new triggers (content-length, forced Bash snapshot) only fire on runs already spiralling (3 consecutive truncations), at most once per streak. Prompt additions are general strategy, no task literals."
cost_shift: "Net negative on the affected cluster (fewer wasted re-priming turns); +1 bounded Bash round-trip per spiral is negligible. Neutral on healthy runs."
rollback_trigger: "If R2 pass_rate is flat/down AND the budget_exceeded count does not shrink, or if any previously-passing task regresses to budget_exceeded/error, revert to the R0 text-only recovery processor."
-->

### Why

R0 scored 30/50. Five tasks all hit `exit_reason=budget_exceeded` at the
full 80-step cap (task_000010, task_000118, task_000264, task_001031,
task_001321). The shared root cause is a *reasoning-only truncation spiral*:
the model generates verbose narration that exceeds the per-turn `max_tokens`
(4096) cap with NO tool call, the run loop appends a passive "cut off by the
token limit. Please continue from where you left off." nudge, and the same
runaway generation re-primes turn after turn. task_000118 shows the assistant
emitting the IDENTICAL sentence ~11 times with zero tool calls; 20 of its
user turns are the passive continue nudge. The R0 config carried a text-only
`LengthTruncationRecoveryProcessor` that (a) triggered only on
`finish_reason=="length"` (which the backend did not report reliably — the
raw passive nudge survived into history, proving the processor never fired)
and (b) escalated with TEXT the model narrates straight past. task_000010
additionally exposed the "correct-logic wrong-path" hard-fail: it wrote
`/home/user/k8s_operator.py` while the verifier required
`/home/user/operator.py`.

### Changes

- `processors/length_recovery_forceact.py` — new `MultiHookProcessor`
  (singleton group `tmax_length_recovery`, so it REPLACES the R0 processor):
  adds a secondary content-length trigger and, after N consecutive
  truncations, mechanically injects a real generic Bash workspace-snapshot
  tool call so model-external ground truth lands in context and breaks the
  spiral. (C-001)
- `config.yaml` — swap the R0
  `recipe.tmax_eval.processors.length_recovery.LengthTruncationRecoveryProcessor`
  entry for the new `file://` processor with
  `repeat_threshold=2, force_action_threshold=3, content_char_threshold=40000`.
- `system_prompt.txt` — two general strategy rules: verify every required
  output artifact exists at its EXACT path before finishing; after 2+
  identical failures, stop retrying variants and switch approach; keep turns
  short. (C-002)

### Evidence

- `task_000118_3043e92d` msgs 2,4,6,9,11,13,15,17,19,21,23: assistant repeats
  verbatim "The user is right - I've been stuck in a loop..." with zero
  tool_calls; each followed by the raw passive "cut off by the token limit"
  nudge. First real tool call only at msg 25. 20 passive continue turns, only
  14 tool calls across 80 steps.
- `task_000264_ab8c7253` last assistant: "The query is still timing out. I've
  been stuck in a loop trying the same query." 18 passive continue turns.
- `task_000010_644ab1c2` final_pytest: "Operator script /home/user/operator.py
  does not exist. You must create it." (wrote `k8s_operator.py` instead).
- `task_001031_a8f0eb37` / `task_001321_658ce4a8`: 33 tool calls each, cycling
  near-identical retries (mpi4py API / output-capture variants) with no pivot.

### Uncertainty

The forced-snapshot mechanism is proven to fire in this harness
(`tb2_self_verify` uses the same injected-tool-call path), but whether fresh
state actually lets these particular Qwen3.5-9B runs finish within remaining
budget is the open question — some tasks (recursive-CTE perf, MPI) may also
have a genuine capability gap the harness cannot close. If the cluster does
not shrink at all in R2, the spiral is not the only blocker and the next round
should look upstream (per-turn max_tokens sizing, or task-class capability).

## Round 2 — final-state process/resource hygiene

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T12:00:00Z
hypothesis_id: h_final_state_hygiene_v1
levers: [control]
predicted_affected: [task_000140_01c78b42, task_001090_c61c71f2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flip the TB2 final-container-state failure cluster: tasks whose verifier greps for lingering/misnamed processes or bounded resource footprints (task_000140 lingering vm_service; task_001090 wrong /proc/comm), a verifier family the file-content-only self-verify checklist never probes."
regression_risk: "Low — appends text to the one-shot exit self-verify ack; injects no messages, changes no control flow, fires once per task. Non-service tasks read a few hundred extra tokens once. No passing task depends on leaving processes running unless the task asked for it (clause branches stopped-vs-running)."
cost_shift: "+~350 tokens on the single exit turn per task; +1-3 Bash verify calls near the end on service/resource tasks only. Negligible aggregate, no per-step growth."
rollback_trigger: "If R3 shows task_000140 still failing test_no_lingering_service_processes, or any previously-passing service task regresses (e.g. agent kills a process it was meant to leave running), revert the processor."
-->

### Why

Assigned focus task_000140_01c78b42 fails the verifier test
`test_no_lingering_service_processes`: `Lingering vm_service processes found:
['338', '591', '790']`. The task asks the agent to fix a Go service, write a
supervisor script, and build a CI/CD pipeline that starts the service, hits it,
then **gracefully stops it** — so the required END state is "no vm_service
running". The agent implemented everything correctly and ran the pipeline
(step 14) to test it, but the pipeline's `kill -TERM $PID` on the single
recorded PID is racy and misses copies spawned by earlier/repeated pipeline
runs (the verifier re-runs the pipeline). The stock CustomSelfVerifyProcessor
fired at step 20, but the agent's follow-up only re-checked output-file
existence/content and never ran `pgrep`/`ps` to confirm no service lingered.

The same root cause — the agent not reconciling the final *process* state
against the task's required end-state — recurs at task_001090_c61c71f2, whose
verifier fails `Process name is 'bash', expected 'monitor'` (a wrapper bash left
in front of the real binary instead of `exec`-ing it). Two adjacent
resource-footprint tasks (task_000118 log-dir size, task_000748 leaked bytes)
sit on the same "final container state" verifier axis, so the resource clause is
additive cover. The stock self-verify checklist covers output files and
"is the service still alive" but says nothing about cleanup, process identity,
or bounded resource state — a real harness gap on the TB2 final-state verifier
family.

### Changes

- `processors/final_state_hygiene.py` — new `FinalStateHygieneProcessor`
  (MultiHookProcessor). On `on_after_tool`, when the stock `_tb2_self_verify`
  synthetic result appears, appends a task-agnostic final-state process/resource
  hygiene checklist (reconcile running processes vs required stopped/running
  end-state incl. process identity/port; reconcile bounded resource footprints).
  Fires at most once per task; injects no messages; contract-clean.
- `config.yaml` — registered the processor via absolute `file://…::FinalStateHygieneProcessor`
  immediately after `CustomSelfVerifyProcessor` (order 95 so the synthetic
  result exists before we append).

### Evidence

- `task_000140_01c78b42` result.json final_pytest: `FAILED
  test_no_lingering_service_processes - AssertionError: Lingering vm_service
  processes found: ['338', '591', '790']`.
- `task_000140` msg 0 (prompt): "gracefully stop the Go service … sending a
  SIGTERM kill signal" — required end-state is service stopped.
- `task_000140` step 14 tool call: `bash /home/user/test_pipeline.sh` (spawns
  `vm_service &`); step 20 `_tb2_self_verify` fired; msg 22 re-checked only files
  (`ls`, `cat vm_setup.log`), never `pgrep vm_service`; msg 26 declared done.
- `task_001090_c61c71f2` result.json tail: `assert comm == "monitor" … 'bash' ==
  'monitor'` — final process-identity mismatch, same reconcile-end-state gap.

### Uncertainty

Assumes the model acts on the appended checklist when it reads the exit ack
rather than skimming past it — same instruction-adherence risk as any prompt
guidance, but delivered exactly at the exit decision point via the existing
gate rather than as always-on prompt text. If R3 shows task_000140 still
failing at the lingering-process test, the model is ignoring the exit reminder
and the next escalation is a Control hook that actively runs `pgrep` for
agent-spawned background processes at exit and blocks the exit until they are
resolved. Watch for regressions where the agent over-eagerly kills a process a
task wanted left running — the clause branches on stopped-vs-running to avoid
this, but confirm on any service task that flips F.

## Round 2 — correctness self-verify (value audit)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T05:10:00Z
hypothesis_id: h_correctness_self_verify_v1
levers: [control]
predicted_affected: [task_000396_e56917e2, task_000505_50b5162d, task_001653_c4cafa73, task_000140_01c78b42, task_000015_89886d8d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips part of the ~15-task exit_reason=done/reward=0 'silent wrong-answer' cluster (largest R0 failing cluster untouched by R1's budget_exceeded fix) by turning the passive one-shot self-verify into an active correctness audit + forced independent cross-check. Generalizes to any task with a stated success criterion."
regression_risk: "Low — same singleton group tb2_self_verify replaces the stock verify (no net processor added). Barriers fire only on exit-intent turns; phase-2 nudge fires at most once and only when the model re-narrated with zero new tool calls, then always yields to exit, so a genuinely-finished run is never trapped."
cost_shift: "+1 to +3 Bash round-trips on runs that would otherwise rubber-stamp and exit; negligible on runs already doing real verification (they trip the 'new tool call since phase 1' guard and skip phase 2). Net positive vs a wasted 0-reward task."
rollback_trigger: "If R3 pass_rate is flat/down AND the done/reward=0 cluster does not shrink, OR any previously-passing task regresses to done/reward=0 via being pushed to 'fix' a correct value, revert to the stock CustomSelfVerifyProcessor."
-->

### Why

R0's largest failing cluster is exit_reason=done + finished=no_tool_calls +
reward=0 (~15 tasks): the agent believed it finished, but the verifier's
VALUE/behaviour assertion (a threshold, an exact match, a clean final state)
failed. The stock `_tb2_self_verify` fired in every one of these cases, yet
the agent rubber-stamped it — re-listing files and declaring "format matches"
— because the stock checklist is existence/format-oriented and the model
narrates straight past the weak "values are correct" item. The assigned
task_000396 is the archetype: the task says the integration "diverges and
fails to match the analytical reference", the verifier wants deviation < 0.1,
the agent computed 0.576768, self-verified only that files exist, and exited —
never reconciling a plainly-non-matching value against the stated goal.

### Changes

- `processors/correctness_self_verify.py` — new `MultiHookProcessor`
  `CorrectnessSelfVerifyProcessor` (singleton group `tb2_self_verify`, so it
  REPLACES the stock `CustomSelfVerifyProcessor`). Two exit-intent barriers:
  (1) an adversarial correctness audit that forces reconciling the result
  against the task's stated success criterion, independently re-deriving any
  reported number, and testing on an edge/adversarial input; (2) a terminal
  anti-rubber-stamp nudge that fires only if the model tries to exit again
  with zero new tool calls since barrier 1, insisting on one concrete
  cross-check command before exit. Bounded (phase 2 at most once, then free
  exit). (C-001)
- `config.yaml` — drop the stock
  `benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor` entry, add
  the new `file://` processor at the same pipeline position / singleton group.

### Evidence

- `task_000396_e56917e2` msgs 42-53: max deviation `0.576768` written; verify
  fired msg 53; agent re-checked only file existence + `0.25` bug line; exited.
  final_pytest: `assert 0.576768 < 0.1`.
- `task_000505_50b5162d` msgs 15-19: verify fired; agent only re-ran `ls`;
  final_pytest: "2 of 2 evil bypassed" (detector tested only on `/bin/ls`).
- `task_001653_c4cafa73` msgs 11-15: verify fired; agent confirmed "format
  matches exactly"; VALUES wrong (`Centroid: 36.36..` vs `42.00..`).
- `task_000140_01c78b42` msgs 20-26: verify fired; files confirmed; final_pytest
  "Lingering vm_service processes found ['338','591','790']".
- `task_000015_89886d8d` msgs 66-73: verify fired; agent's own passing test did
  not exercise the golden corpus; final_pytest `accuracy 0.3389 < 0.98`.

### Uncertainty

Some cluster members (task_001653, task_000015) are partly capability-bound:
the "recompute a second way / test the real corpus" barrier gives a genuine
second chance but cannot conjure correct math the model can't do. The
mechanism is proven to fire (same injected-tool-call path as the stock
verify), and the phase-2 guard bounds cost, but whether these particular
Qwen3.5-9B runs actually act on the audit rather than re-narrating a second
time is the open question. If the cluster does not shrink at all in R3, the
blocker is capability, not verification discipline, and the next round should
look upstream (per-task-class reasoning support), not tighten the barrier.

## Round 1 — rigorous completion guard (c7)

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-30T05:02:00Z
hypothesis_id: h_rigorous_completion_guard_v1
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips/derisks the 'declared done after a shallow check' failure shape — agent finishes after script-ran / sample-matched / file-plausibly-exists without re-deriving the EXACT output contract (keys, exact path, format, accuracy behaviour) from the authoritative source. Generalizes to any task self-advertising a hidden/accuracy-graded eval (OCR-schema, data-migration, service-contract)."
regression_risk: "Low. Guard arms ONLY when task text matches generic rigor-cue vocabulary, so trivial already-passing tasks get no extra turn; bounded at max_fires=2 so it cannot cause an exit-block/budget spiral; additive, +1 user message per fire (contract-verified), ordered right after the existing one-shot CustomSelfVerifyProcessor."
cost_shift: "+0 on non-armed tasks; +1-2 short verification round-trips (plus agent re-checking) on armed tasks. Net-positive when it converts a 0-reward run into a pass or averts wasted retries."
rollback_trigger: "If R2 pass_rate is flat/down AND neither task_000015 nor task_000010 flips AND any previously-passing rigor-advertised task regresses to budget_exceeded/error (exit-block spiral), revert the RigorousCompletionGuard entry."
-->

### Why

Assigned cluster: task_000010_644ab1c2 (budget_exceeded, wrong exact path
`k8s_operator.py` vs required `operator.py`) and task_000015_89886d8d
(exit_reason=done, final accuracy 0.3389 < 0.98). Distinct proximate causes,
one shared harness-actionable root: the agent finishes after a SHALLOW check
without re-deriving the exact output contract from the authoritative source and
without stress-testing beyond the provided sample. In task_000015 the OCR of
the schema image clearly carried the true target keys (`category`, `order`,
`traffic_source`) but the agent named its output keys after the sample's own
query params (`department`, `sort_order`) and verified only that its script ran
on the 3-line sample. In task_000010 the agent wrote a plausible-but-wrong path
and never re-confirmed the exact required filename. The truncation-spiral half
of task_000010 is owned by the sibling proposal (h_length_recovery_forceact_v1)
— this candidate ships the complementary completion-discipline mechanism, not a
duplicate length-recovery lever.

### Changes

- `processors/rigorous_completion_guard.py` — new `MultiHookProcessor`
  `RigorousCompletionGuard` (singleton group `tb2_rigor_completion_guard`,
  `_order=91`, right after `CustomSelfVerifyProcessor`). Captures
  `task_description` at `on_task_start`; arms only when a generic rigor-cue
  regex matches ("hidden", "massive", "edge-case", "accuracy", "threshold",
  "robust", "property-based", "golden", "test suite will run", …). On an
  exit-intent turn (finish_reason∈{end_turn,stop}, no tool calls) it injects a
  bounded (`max_fires=2`) targeted reminder: re-derive the exact contract from
  the source (not from samples), stress-test edge cases, confirm exact paths.
  Uses the proven keepalive-tool-call exit-intercept path (approved=False +
  synthetic_result), net +1 user message per fire. (C-001)
- `config.yaml` — append the new `file://` processor with `max_fires=2`;
  everything else byte-identical to R0 (length-recovery left untouched so this
  proposal stays orthogonal to the sibling).

### Evidence

- `task_000015_89886d8d` msg 37 (Bash writing migrate.py): ROUTES table maps
  `dept→department`, `sort→sort_order` with comments deriving keys from the
  query-param names; msgs 40 & 66 show the only correctness check is running on
  the 3-line `sample_urls.txt`; msg 73 final: "the script works correctly with
  the sample URLs." OCR msgs 4/16/24 show `category`/`order`/`traffic_source`
  were in context and ignored.
- `task_000010_644ab1c2` result.json final_pytest: "Operator script
  /home/user/operator.py does not exist" (agent wrote `k8s_operator.py`);
  exit_reason=budget_exceeded at 80 steps.

### Uncertainty

task_000015 is partly a model OCR/reasoning capability limit — the correct
keys were in context and the agent still guessed. The guard raises the odds it
trusts the source over the sample but does not guarantee the flip. For
task_000010 the guard only bites if the run reaches an exit turn before
budget_exceeded, i.e. the sibling's length-recovery has to hold first — so 010
is the secondary target here. If R2 shows no flip on either and no cost spike,
the residual blocker is capability, and the next round should look upstream
(per-turn max_tokens sizing / OCR tooling) rather than at completion discipline.

## Round 1 (c5) — break identical-command loops

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_repeated_command_loop_breaker_v1
levers: [control]
predicted_affected: [task_000264_ab8c7253, task_001031_a8f0eb37, task_001321_658ce4a8]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the identical-Bash-command repetition-loop budget_exceeded class; early redirect gives recovery headroom, raise reclaims budget on unrecoverable loops"
regression_risk: "A legitimate task that must re-run the exact same substantive command 5+ times consecutively; mitigated by ignoring empty/trivial calls (protects the observed 22x-empty-call passing task_001089), warn-before-raise, and clean loop_detected exit"
cost_shift: "Net cheaper — looping tasks terminate early instead of burning all 80 steps / 1000s+; ~0 added cost on non-looping tasks"
rollback_trigger: "If any of the 3 predicted tasks still budget_exceeded with the breaker present, OR any previously-passing task regresses to loop_detected with the breaker firing (esp. task_001089_220cc46b), revert"
-->

### Why

Assigned focus task_000264_ab8c7253 failed budget_exceeded at 80 steps / 1410s.
It is a SQLite recursive-CTE task; the model wrote a recursive query that always
times out (15s), then re-narrated "The user is right - I've been stuck in a
loop, let me try a fundamentally different approach" verbatim on ~10 consecutive
turns while re-issuing the byte-identical failing command. The whole step +
wall-clock budget drained with zero progress; the required top_managers.csv was
left 0 bytes. Same mechanical pathology as two other budget_exceeded tasks this
round: task_001031 (mpi4py snippet, 33 consecutive identical commands) and
task_001321 (extractor invocation, 19 consecutive identical). It is a loop
pathology, not a missing capability — the agent runs varied diagnostics when
it briefly breaks out.

### Changes

- processors/repeated_command_breaker.py — new RepeatedCommandLoopBreaker
  MultiHookProcessor. Fingerprints only substantive Bash commands (non-empty,
  min length); counts consecutive identical commands; at warn_threshold appends
  a decisive "stop re-issuing the exact command" redirect to the tool result;
  at raise_threshold raises LoopDetectedError → clean exit_reason=loop_detected
  with best-output recovery. Empty-arg / trivial / non-Bash calls are neutral
  interludes (neither count nor reset), which protects the passing empty-call
  recovery pattern. Content-agnostic.
- config.yaml — register RepeatedCommandLoopBreaker (order 21, after
  LengthTruncationRecoveryProcessor, before compaction).

### Evidence

- task_000264 messages.json: command fingerprint for the sqlite recursive-CTE
  query issued 8 times, 3 consecutive at tail; each returns "Error: command
  timed out after 15s"; identical narration ~10 times. final_pytest:
  top_managers.csv empty → test_csv_output AssertionError [] == [...].
- task_001031 last 4 assistant turns: identical python3 mpi4py snippet;
  simulated max consecutive-identical run = 33.
- task_001321 last turns: identical extractor invocation; max
  consecutive-identical run = 19.
- Regression guard verified: task_001089_220cc46b (reward=1) makes 22
  consecutive empty-argument calls; simulator confirms the breaker does NOT fire
  because empty/trivial calls are ignored. No passing task trips the raise
  threshold.

### Uncertainty

The raise reclaims budget but does not by itself flip a still-broken task; the
flip path is the early warn redirect giving the agent budget headroom to change
approach. If the underlying query/logic gap is unrecoverable for the model, the
task will exit loop_detected (still reward=0) but faster — a cost win, not a
pass win. Worst case on a false positive is one redirect message plus an early
clean termination on a task that would have failed anyway.

## Round 2 — graded-artifact clean re-run guard (c2)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T06:20:00Z
hypothesis_id: h_graded_artifact_rerun_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_000140_01c78b42]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the 'graded-artifact re-run mismatch' sub-cluster of the large done/reward=0 family: tasks shipping an executable deliverable (daemon/monitor/service/pipeline) that an external grader restarts from scratch, where the agent's ad-hoc self-test used a non-matching invocation ordering/timing and a latent startup/timing/leftover bug survived. Generalizes to any task graded on the artifact's behaviour on a clean re-execution."
regression_risk: "Low — new singleton group graded_artifact_rerun_reminder, _order=92 after service_deps (91) and CustomSelfVerify (90) so exit-intent hooks serialize. Fires at most once per task, only when the agent's own Bash activity shows an executable deliverable was authored AND task text uses run/execute/start/test-the-artifact vocabulary; injects one user message via the proven keepalive-tool-call path (contract-clean), never off task ids. Always yields to a genuine exit on the next turn, so a finished run is never trapped. Non-matching tasks (pure data-transform / one-shot answer) never arm."
cost_shift: "+1 reminder message + typically +1-3 Bash re-run/verify round-trips on armed tasks only; ~0 on the ~30 tasks that do not arm. Net-positive when it converts a 0-reward run into a pass."
rollback_trigger: "If R3 shows task_000118 still failing the peak-size assertion AND task_000140 still failing test_no_lingering_service_processes, OR any previously-passing artifact/service task regresses to done/reward=0 or budget_exceeded via the reminder pushing a needless re-run, revert the graded_artifact_rerun_reminder entry."
-->

### Why

Assigned focus task_000118_3043e92d fails `exit_reason=done`,
`finished=no_tool_calls`, `reward=0` — NOT a truncation spiral (only 23/80
steps). The task asks for a disk-usage monitor daemon at
`/home/user/deployment_monitor.py` that pauses/truncates worker logs. The
agent wrote a correct-looking monitor and tested it, but its final self-test
(msg 33) started the monitor and then `run_deployment.sh` in the SAME shell,
where Python import-lag happened to let the monitor's first loop catch the
workers. The GRADER instead does `Popen(monitor); sleep(0.5); Popen(deploy)` —
giving the monitor a clean 0.5 s head start, so its "exit when no worker_sim.py
found" loop fires on iteration 1 (before any worker exists), the daemon dies
immediately, never truncates, and peak log-dir size hits the full 200 MB
(final_pytest: `Peak log directory size was 209715200 bytes ... exceeds ...
45000000`). Root cause the harness can act on: the agent tested its graded
deliverable under a self-chosen invocation whose startup ordering/timing
differed from how the grader restarts it, and never re-ran it from a clean,
fresh-process baseline. The same shape recurs at task_000140_01c78b42, whose
pipeline leaves lingering `vm_service` processes on the grader's re-run and
whose agent's follow-up only re-listed output files instead of re-running the
pipeline clean and checking `pgrep`.

### Changes

- `processors/graded_artifact_rerun_reminder.py` — new
  `GradedArtifactReRunReminderProcessor` (MultiHookProcessor, singleton group
  `graded_artifact_rerun_reminder`, `_order=92`). On an exit-intent turn, when
  the agent's Bash activity shows an executable deliverable was authored AND the
  task text describes it being run/executed by an automated check, it injects a
  one-shot reminder (via the proven keepalive-tool-call exit-intercept path) to
  reset to a clean baseline (kill spawned background processes, reset mutable
  working state) and re-run the artifact exactly as the grader will — fresh
  process, worst-case startup ordering — then confirm the stated end-state
  metric, not just file existence. Bounded to one fire; contract-clean;
  content-agnostic. (C-001)
- `config.yaml` — registered the new `file://…::GradedArtifactReRunReminderProcessor`
  immediately after `ServiceDepsReminderProcessor`; everything else
  byte-identical to R1/c2.

### Evidence

- `task_000118_3043e92d` msg 33 (agent self-test): `python3
  /home/user/deployment_monitor.py & ; sleep 1 ; /home/user/run_deployment.sh`
  in one shell; msg 34 showed 12 KB total (passed by timing luck). result.json
  final_pytest: `AssertionError: Peak log directory size was 209715200 bytes,
  which exceeds the threshold of 45000000 bytes.` The monitor's `main()` loop
  `break`s when `get_worker_processes()` is empty — true on iteration 1 under
  the grader's `Popen+sleep(0.5)` order.
- `task_000140_01c78b42` result.json final_pytest: `Lingering vm_service
  processes found: ['338','591','790']`; msgs 20-26 show the agent's post-verify
  follow-up only re-checked files (`ls`, `cat vm_setup.log`), never re-ran the
  pipeline clean + `pgrep vm_service`.

### Uncertainty

Assumes the model acts on the clean-re-run reminder rather than skimming past it
and re-declaring done — the same instruction-adherence risk as any exit nudge,
but delivered exactly at the exit decision via the existing gate. For task_000118
it also assumes the agent, once it sees the 200 MB clean-re-run result, can fix
the startup guard (a small, in-budget edit — it used 23/80 steps). If R3 shows
task_000118 still failing the peak-size assertion, the model is not acting on the
reminder and the next escalation is a Control hook that actively runs the
artifact once in a clean namespace at exit and surfaces the end-state metric
before allowing exit. Watch for regressions where the reminder pushes a needless
re-run on an already-correct artifact/service task — the arm requires BOTH an
authored-executable Bash signal and re-run-graded task vocabulary to limit this.

## Round 2 — background-service lifecycle hygiene

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T06:30:00Z
hypothesis_id: h_bg_service_hygiene_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_000396_e56917e2, task_001536_acfe6c35]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips/de-risks the background-service churn slice of the budget_exceeded cluster (task_000010 archetype) and trims wasted wall-clock on any port-forward/mock-API/socket-server task by breaking the hung-command + stale-port-rebind loop early. Generalizes to any task emitting the two generic OS/tooling failure strings."
regression_risk: "Low - fires at most once per task and only after 2+ churn signatures; a single legitimate transient timeout never trips it. Adds one processor (new singleton group, replaces nothing), injects one user message, no tool round-trip, no control-flow change. Worst false-positive cost: one ~250-token advisory on a task that hit two unrelated timeouts."
cost_shift: "Net negative on the affected cluster (churning tasks stop burning 120s hangs and restart loops early). +~250 tokens once on any task crossing the threshold; zero on healthy runs (no signatures so never arms)."
rollback_trigger: "If R3 shows task_000010 still budget_exceeded with unchanged 120s-timeout / address-in-use churn counts, OR any previously-passing service task regresses to done/reward=0 or budget_exceeded with this processor firing, revert BackgroundServiceHygieneProcessor."
-->

### Why

Assigned focus task_000010_644ab1c2 still fails in R1: budget_exceeded at the
80-step cap, elapsed_s=1113, manifests never applied. The task requires
standing up a durable port-forward from :9090 to a mock API on :8080 and
driving an interactive CLI through it. Root cause is a background-service
lifecycle churn the model repeatedly misdiagnoses: it starts a single-shot /
non-durable proxy, the interactive CLI then hangs against it and the Bash tool
returns "command timed out after 120s" (3 such hangs, ~360s of the 1113s
budget), and restart attempts fail with a TCPServer(("127.0.0.1", 8080))
"address already in use" traceback because a stale mock_api PID still holds the
port - which the model reads as "the service is running, good" and moves past.
The same tool-layer signature class ("command timed out after") also appears on
task_000396 and task_001536, so the trigger is a recurring cross-task shape,
not a one-task regex. The separate wrong-operator-filename defect
(k8s_operator.py vs operator.py) is owned by the sibling completion-discipline
candidate; this round targets the budget-burn root that prevented 010 from ever
reaching a clean exit.

### Changes

- processors/background_service_hygiene.py - new BackgroundServiceHygieneProcessor
  (MultiHookProcessor, singleton group bg_service_hygiene, order 92).
  Counts generic churn signatures in tool results ("command timed out after N",
  "address already in use" / "Errno 98" / "EADDRINUSE"); once the count reaches
  signal_threshold (default 2), injects a single one-shot user message on the
  next model call: free stale ports, make the listener durable/multi-connection,
  probe with a bounded timeout instead of re-running a hung command. Churn-
  gated, not task-id-keyed; fires at most once per task. (C-001)
- config.yaml - registered the processor via absolute
  file://...::BackgroundServiceHygieneProcessor after ServiceDepsReminderProcessor;
  everything else byte-identical to R1/c2.

### Evidence

- task_000010_644ab1c2 result.json: exit_reason=budget_exceeded,
  elapsed_s=1113.2, 80 steps; final_pytest test_api_success_log shows only
  the ConfigMap was ever applied (deploy-v2.yaml never got through the flaky
  proxy).
- task_000010 messages.json msg 18/28/69: TCPServer(("127.0.0.1", 8080))
  Traceback (stale listener holds :8080); msg 19/29 assistant misreads it as
  "the mock API is still running".
- task_000010 msg 32, 46: "Error: command timed out after 120s" (interactive
  CLI hung against the non-durable forward).
- task_000010 msg 65: socat[801] E bind(...) Address already in use on :9090.
- Cross-task: task_000396_e56917e2 and task_001536_acfe6c35 each have one
  "command timed out after" tool result - same foreground-hang signature.

### Uncertainty

Assumes the model acts on the injected lifecycle guidance rather than skimming
past it mid-churn - same instruction-adherence risk as any nudge, but delivered
exactly at the churn signature rather than as always-on prompt text. If R3
shows 010 still budget_exceeded with unchanged timeout/bind-collision counts,
the residual blocker is model capability (it cannot author a durable proxy) and
the next escalation is a Control hook that actively runs a stale-port sweep,
not more guidance. Watch for regressions on any service task that flips F with
this processor firing.

## Round 2 — OCR robustness ladder (c1)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T06:20:00Z
hypothesis_id: h_ocr_robustness_ladder_v1
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000505_50b5162d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the OCR-from-image failure cluster (task_000015 accuracy 0.0000; task_000505 detector matched nothing) by supplying the standard OCR-recovery ladder — upscale first, set --dpi, sweep --psm, binarize — plus the 'derive the output contract from the authoritative source image, not the provided sample' discipline, delivered right after the agent's first tesseract call. Generalizes to any unseen task that must read text from a low-quality image."
regression_risk: "Low — the processor never arms unless the agent itself runs an OCR tool (tesseract/pytesseract), so the non-image majority of tasks are byte-for-byte unaffected. On an armed task it only APPENDS one user message (never rewrites tool_calls, so it cannot clobber a real or foreign-processor tool call) and fires at most once. Passing OCR tasks (task_000338, task_001652) already used good OCR habits consistent with the reminder."
cost_shift: "+~400 tokens once per OCR task (a small minority of the suite); ~0 on all non-OCR tasks. May also save the ~11 near-identical cosmetic-tweak OCR retries task_000015 currently burns before giving up. Net positive vs a wasted 0-reward run."
rollback_trigger: "If R3 shows task_000015 still failing test_migrate_script_accuracy AND task_000505 still failing test_adversarial_corpus AND no OCR task flips, the blocker is model OCR/reasoning capability the ladder cannot conjure — revert the processor and treat OCR as a capability gap (skip)."
-->

### Why

Assigned focus task_000015_89886d8d fails `test_migrate_script_accuracy`
(`Accuracy metric 0.0000 is below the 0.98 threshold`). The task's schema
mapping lives in a low-DPI synthetic PNG (`/app/routing_schema.png`, 800x400).
tesseract emits `Warning: Invalid resolution 0 dpi. Using 70 instead` and
returns garbled text. The agent then retries ONLY cosmetic PIL tweaks
(contrast/brightness/sharpen/autocontrast/threshold) ~11 times — it never
upscales, never sets `--dpi`, never sweeps `--psm`, the three highest-impact
OCR levers for clean synthetic renders. Getting the same garble, it gives up on
the source and invents output schema keys (`department`, `sort_order`,
`ui_theme`, `traffic_source`) from the *sample's* query-param names instead of
the right-hand side of the mapping arrows visible even in the garble, so its
output matches the golden schema on zero records. The same OCR-quality root
cause recurs at task_000505_50b5162d (transcribe an SSH public key from
`/app/evidence.png`; a misread key ⇒ `2 of 2 evil bypassed`). This is a
strategy gap, not a capability gap: tesseract is present and works — the model
just doesn't know how to drive it on low-quality images and gives up.

### Changes

- `processors/ocr_robustness_reminder.py` — new `OcrRobustnessReminderProcessor`
  (MultiHookProcessor, singleton group `ocr_robustness_reminder`, `_order=92`).
  Keyed off the agent's own Bash OCR activity (tesseract/pytesseract/etc.),
  never off task ids. The first time OCR is observed, it queues and (on the next
  `on_before_model`) appends a single task-agnostic user message: the OCR-
  recovery ladder (upscale first → `--dpi 300` → sweep `--psm` → binarize) plus
  "derive the output contract from the authoritative source image, not the
  provided sample". Fires at most once; only appends a message (never rewrites
  tool_calls); contract-clean. (C-001)
- `config.yaml` — registered the processor via absolute `file://…::OcrRobustnessReminderProcessor`
  after `ServiceDepsReminderProcessor`. Everything else byte-identical to R1/c2.

### Evidence

- `task_000015_89886d8d` step 2 tool result: `Warning: Invalid resolution 0 dpi.
  Using 70 instead ... atalogyitem <item _id> ... category ... order ... sort
  order` (garbled). Steps 3–24: only contrast/sharpen/threshold tweaks, no
  upscale/`--dpi`/`--psm`. Step 37: writes ROUTES with keys derived from sample
  query params; step 40 output uses `department`/`sort_order`/`ui_theme`/
  `traffic_source`. final_pytest: `Accuracy metric 0.0000 ... below 0.98`.
- `task_000505_50b5162d` first user msg: "Extract the hidden SSH public key from
  the image file `/app/evidence.png`"; `grep tesseract` confirms OCR path;
  final_pytest `test_adversarial_corpus`: `2 of 2 evil bypassed: ls_evil,
  cat_evil` (detector built on the transcribed key matched nothing).
- Passing OCR tasks task_000338_27d6a1be (reward 1) and task_001652_86e1d185
  (reward 1) used ImageMagick `convert` preprocessing — the reminder is
  consistent with, not counter to, the habits that already worked.

### Uncertainty

The ladder raises the odds of a clean transcription but cannot guarantee the
model executes it well or reasons correctly about the mapping direction —
task_000015 also has a reasoning component (right-hand-side keys). If R3 shows
no OCR flip and no cost spike, the residual blocker is model OCR/reasoning
capability, not strategy, and the next round should treat it as a capability
gap and skip rather than tighten the reminder. Note task_000536_9c16e8ef also
uses OCR but its failure is a CSV quoting-format mismatch, not OCR quality — it
is deliberately excluded from predicted_affected.

## Round 2 — active final-process-state audit (c3)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T06:20:00Z
hypothesis_id: h_final_process_state_audit_v1
levers: [control]
predicted_affected: [task_000140_01c78b42, task_001090_c61c71f2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the TB2 final-process-state verifier cluster (task_000140 lingering vm_service; task_001090 wrong final process identity) by injecting a REAL ps/ss/jobs snapshot at exit so the ground-truth lingering-process list lands in context, then reconciling it against the task's required stopped/running end-state. Generalizes to any service task whose verifier greps for lingering/misnamed service processes."
regression_risk: "Low — fires at most once per task, only after a backgrounded-service Bash signal, runs LAST (order 96) so it defers to self-verify (90) and svc-deps (91), and NEVER kills anything itself (KEEP branch explicitly confirms a wanted service stays up). Worst false-positive cost is one read-only snapshot + one reminder, then a free exit. No message on non-service tasks, no control-flow change."
cost_shift: "+1 real read-only Bash snapshot + 1 short user message on service tasks that reach exit un-audited; +0 on non-service tasks; +1-3 short pattern-kill/re-snapshot calls only on tasks that genuinely had lingering processes. Net positive vs a wasted 0-reward task; negligible aggregate."
rollback_trigger: "If R3 shows task_000140 still failing test_no_lingering_service_processes (agent ignores the injected snapshot), OR any previously-passing service task regresses (agent over-kills a service it was meant to leave running — should be impossible since the processor never kills, but watch KEEP-branch tasks), revert the FinalProcessStateAuditProcessor entry."
-->

### Why

Assigned focus task_000140_01c78b42 fails the verifier test
`test_no_lingering_service_processes`: `Lingering vm_service processes found:
['349','609','810']` (MULTIPLE PIDs). The task asks the agent to fix a Go
service, write a supervisor script, and a CI/CD pipeline that starts the
service, hits it, then **gracefully stops it** (SIGTERM on the PID in
`service.pid`) — required END state is "no vm_service running". The agent
implemented everything and ran the pipeline once (step 14), but its
`test_pipeline.sh` records only `$!` and issues `kill -TERM $PID` on the single
recorded PID; the verifier re-runs the pipeline, so each run spawns a fresh
`vm_service` and the single-PID kill is structurally racy — copies survive. The
root harness gap: the agent never reconciled the final *process* state against
the required end-state. The stock `_tb2_self_verify` fired (msg 20) but the
agent's follow-up (msg 22) re-checked only files (`ls -lh …`) and never ran
`pgrep`/`ps`. Same root cause recurs at task_001090_c61c71f2 (final process
identity `bash` vs `monitor`) — the file/format self-verify checklist never
probes process/socket state, a real TB2 final-state verifier-family gap.

This round is deliberately DISTINCT from the R2 sibling
`h_final_state_hygiene_v1`, which appends a passive TEXT reminder to the
self-verify ack. That relies on instruction adherence the model demonstrably
lacks here (it skimmed past the existing self-verify item). This candidate is
the escalation the R2 journal itself flagged as "next" — a Control hook that
actively RUNS the process snapshot at exit — so the actual lingering-PID list
lands in context as a tool result the model cannot skim past.

### Changes

- `processors/final_process_state_audit.py` — new `FinalProcessStateAuditProcessor`
  (MultiHookProcessor, singleton group `tb2_final_proc_audit`, `_order=96`).
  Tracks backgrounded-service Bash signals (`&`/`nohup`/`listen`/`.pid`/`$!`/
  bound port). At exit-intent (finish_reason∈{end_turn,stop}, no tool calls),
  once per task, injects one REAL `Bash` snapshot (`ps`/`ss`/`netstat`/`jobs`,
  read-only) so live process/socket state lands in context, then appends one
  reconcile-against-required-end-state instruction (STOP branch → pattern-kill
  all matching instances + re-confirm; KEEP branch → confirm the wanted service
  stays up). Never kills anything itself. Task-agnostic; contract-clean. (C-001)
- `config.yaml` — registered the processor via absolute
  `file://…::FinalProcessStateAuditProcessor` after ServiceDepsReminderProcessor
  (order 96, last among exit-intent hooks).

### Evidence

- `task_000140_01c78b42` result.json final_pytest: `FAILED
  test_no_lingering_service_processes — AssertionError: Lingering vm_service
  processes found: ['349','609']` then `['349','609','810']` (verifier re-runs).
- `task_000140` msg 0: "gracefully stop the Go service … sending a SIGTERM kill
  signal" — required end-state stopped.
- `task_000140` msg 10 (test_pipeline.sh): `./vm_service &` … `kill -TERM $PID`
  on the single recorded PID — racy single-PID kill.
- `task_000140` msg 20 `_tb2_self_verify` fired; msg 22 re-checked only files
  (`ls -lh …`); msg 28 declared done — no `pgrep`/`ps` anywhere.
- `task_001090_c61c71f2` result.json tail: `assert comm == "monitor" … 'bash' ==
  'monitor'` — final process-identity mismatch, same reconcile-end-state gap.

### Uncertainty

Assumes the model acts on the injected snapshot + reconcile message rather than
skimming past a second time. The snapshot mechanism is stronger than a text
reminder because the ground-truth lingering-PID list is now a concrete tool
result, but instruction adherence on the reconcile step is still the open
question. If R3 shows task_000140 still failing at the lingering-process test,
the next escalation is a Control hook that at exit actively `pkill`s
agent-spawned background service processes on tasks whose text asks for a
stopped end-state (blocking exit until zero remain). Watch KEEP-branch service
tasks for any regression, though the processor never kills so over-kill should
be impossible.


## Round 3 — identical-command loop breaker (c4)

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-30T07:30:00Z
hypothesis_id: h_repeated_command_loop_breaker_v2
levers: [control]
predicted_affected: [task_000264_ab8c7253, task_001031_a8f0eb37, task_001321_658ce4a8]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
retry_rationale: "The R1/c5 sibling h_repeated_command_loop_breaker_v1 was a parallel proposal that never merged into the accepted R1/c2 -> R2 config lineage (outcome pending, not reverted), so this mechanism has never actually run against the benchmark. task_000264 is STILL failing budget_exceeded in the r1 trajectories with the exact 23x-identical-command loop the c5 processor was designed for, and the current config has no guard for tool-calling loops (only finish_reason=length). Re-scoped raise_threshold 5->6 for more recovery headroom before reclaiming budget."
expected_global_gain: "Closes the identical-Bash-command budget_exceeded cluster (task_000264 recursive-CTE 23x-identical, task_001031 mpi4py 33x-identical, task_001321 extractor 19x-identical). warn redirect gives recovery headroom to actually write required outputs; raise reclaims wasted steps on unrecoverable loops. Generalizes to any verbatim-command loop, content-agnostic."
regression_risk: "Low. Fingerprints only substantive Bash commands (non-empty, above min length); empty/trivial/non-Bash calls are neutral interludes that neither count nor reset, specifically protecting the passing many-empty-call recovery task_001089_220cc46b. warn precedes raise by 3 turns. New singleton group tmax_repeated_command_breaker; additive; contract-clean."
cost_shift: "Net negative — looping tasks exit early instead of burning full 80-step budget; ~0 added cost on non-looping tasks (one appended sentence per warn, only inside an active verbatim loop)."
rollback_trigger: "If R4 shows task_000264 still budget_exceeded (loop unbroken) AND none of the 3 predicted tasks flip, OR any previously-passing task regresses to loop_detected via the breaker firing (esp. task_001089_220cc46b), revert the RepeatedCommandLoopBreaker entry."
-->

### Why

Assigned focus task_000264_ab8c7253 fails exit_reason=budget_exceeded at the
full 80-step cap, reward=0, with ALL THREE required output files missing
(top_managers.csv, query_plan.txt, and the manager_id index). The trajectory
tail is a textbook identical-command loop: from msg 23 through msg 69 the model
issues the BYTE-IDENTICAL Bash command
`sqlite3 /home/user/company.db "EXPLAIN QUERY PLAN ... JOIN hierarchy h ..."`
23 consecutive times. `hierarchy` is a CTE name referenced from a standalone
statement, so it can never resolve — every call returns the identical
`Error: in prepare, no such table: hierarchy (1)`. On each of those 23 turns the
assistant narrates the verbatim sentence "I've been stuck in a loop ... let me
try a completely different approach" and then re-issues the SAME command. The
current config's only loop guard, LengthTruncationRecoveryProcessor, fires only
on finish_reason==length with NO tool call, so this tool-calling verbatim loop
is completely unguarded and the whole step/wall-clock budget drains with zero
progress. The same mechanical pathology recurs on task_001031 (mpi4py snippet,
33x consecutive identical) and task_001321 (extractor invocation, 19x
consecutive identical) — a loop pathology, not a missing capability.

### Changes

- `processors/repeated_command_breaker.py` — new `RepeatedCommandLoopBreaker`
  MultiHookProcessor (singleton group `tmax_repeated_command_breaker`,
  `_order=21`). Fingerprints only substantive Bash commands (non-empty, above
  min_command_chars); empty/trivial/non-Bash calls are neutral interludes
  that neither increment nor reset the consecutive-identical counter. At
  warn_threshold (3) appends a decisive redirect to the tool result telling the
  model to reconsider the root cause and change the command materially or
  finish; at raise_threshold (6) raises LoopDetectedError, converted by the run
  loop into a clean exit_reason=loop_detected with best-output recovery.
  Content-agnostic. (C-001)
- `config.yaml` — registered the new RepeatedCommandLoopBreaker
  (warn_threshold=3, raise_threshold=6, min_command_chars=12, tool_name=Bash)
  right after LengthTruncationRecoveryProcessor; everything else byte-identical
  to R1/c2 (service_deps_reminder path unchanged).

### Evidence

- `task_000264_ab8c7253` messages.json: msg 23 and msg 35 tool_call arguments
  are byte-identical (`EXPLAIN QUERY PLAN ... JOIN hierarchy ...`); tool results
  24..69 are all identical `Error: in prepare, no such table: hierarchy (1)
  (exit 1)`; assistant narration "I've been stuck in a loop ... try a completely
  different approach" repeats ~23 times. result.json:
  exit_reason=budget_exceeded, 80 steps; final_pytest "Output file missing at
  /home/user/top_managers.csv" + "Query plan file missing" + "No index found on
  manager_id".
- `task_001031_a8f0eb37` / `task_001321_658ce4a8` (per R1/c5 diagnosis): last
  turns cycle a byte-identical command; max consecutive-identical runs 33 and 19
  respectively; both budget_exceeded.
- Regression guard: task_001089_220cc46b (reward=1) makes many consecutive
  empty-argument calls; the breaker ignores empty/trivial calls so it never
  counts them and never fires.

### Uncertainty

The warn redirect flips a task only if the model, once told to change the
command, can actually produce the correct query/output within remaining budget.
If the underlying logic gap is unrecoverable, raise still ends the task cleanly
as loop_detected (still reward=0) but sooner — a cost win, not a pass win. If R4
shows task_000264 still budget_exceeded with the breaker present, the loop is
not the only blocker (genuine capability gap on recursive-CTE) and the next
round should look upstream. Watch task_001089_220cc46b for any false-positive
loop_detected regression.

## Round 2 — metric-reconciliation exit guard (c5)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T07:10:00Z
hypothesis_id: h_metric_reconcile_exit_guard_v1
levers: [control]
predicted_affected: [task_000396_e56917e2, task_001653_c4cafa73, task_000748_c9807703]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the 'silent wrong-metric exit' slice of the large done/reward=0 cluster: tasks that state a quantitative acceptance criterion (value must match a reference / be within a range / equal an expected count / clear a threshold) and require the agent to write that computed value to an exact output file, where the stock existence/format self-verify lets the agent rubber-stamp a plainly-failing number. Generalizes to any unseen task with a numeric success criterion + metric-file deliverable."
regression_risk: "Low — new singleton group metric_reconcile_reminder (_order=93), additive, replaces nothing, no control-flow change. Arms only when BOTH the task text carries criterion vocabulary AND the agent's own Bash activity wrote a computed value into an output/metric file; fires at most once; injects one user message via the proven contract-clean keepalive-tool-call exit path; always yields to a genuine exit on the next turn so a correctly-finished run is never trapped. Worst false positive: one ~300-token advisory on a criterion-task that already had a correct value (the message explicitly says finishing is correct if the bound is satisfied). Non-criterion / pure-transform tasks never arm."
cost_shift: "+1 reminder message + typically +1-3 Bash reconcile/recompute round-trips on armed tasks only; ~0 on tasks that do not arm. Net-positive when it converts a 0-reward run into a pass."
rollback_trigger: "If R3 shows task_000396 still failing assert max_dev<0.1 AND task_001653/task_000748 still failing their numeric assertions AND no criterion-task flips, the residual blocker is model capability (cannot compute/align the metric) not verification discipline — revert. Also revert if any previously-passing criterion-task regresses to done/reward=0 or budget_exceeded with this processor firing."
-->

### Why

Assigned focus task_000396_e56917e2 fails exit_reason=done, finished=no_tool_calls,
reward=0. The task (fix an RK45 C integrator so the simulation "matches the
analytical reference", then write the max absolute deviation to
/home/user/validation.log) has a quantitative acceptance criterion: the verifier
asserts `0.0 < max_dev < 0.1`. The agent fixed the Makefile (-lm) and the obvious
dt_new exponent-sign bug the task hinted at, computed a max deviation of 0.6201,
wrote it to validation.log, and — although its own narration at msgs 43/45
explicitly flagged the result ("the maximum deviation is quite high (0.62) ...
suggests the simulation might still have issues"; "the amplitude is growing
significantly ... This is not correct for a harmonic oscillator") — its post
self-verify follow-up (msgs 51-62) only re-checked file existence, the Makefile,
and the dt_new line, then exited. The harness-actionable root cause: at exit the
agent had a computed metric in context AND the task stated a bound for it, but the
stock CustomSelfVerifyProcessor checklist is existence/format oriented, so the
agent rubber-stamped a value its own analysis said was wrong. The identical shape
recurs at task_001653_c4cafa73 (writes Centroid/Distance to etl_report.txt,
confirms only that the FORMAT matches, values wrong) and task_000748_c9807703
(writes leak_bytes=214, narrates that the value "changed from 138 to 214 ...
valgrind's output can vary", i.e. notes it is uncertain/unstable, then exits).

### Changes

- `processors/metric_reconcile_reminder.py` — new `MetricReconcileReminderProcessor`
  (MultiHookProcessor, singleton group `metric_reconcile_reminder`, `_order=93`).
  On on_task_start it arms only if the task text matches generic
  numeric-acceptance-criterion vocabulary (match/diverge/within/deviation/
  tolerance/threshold/accuracy/expected value/exact/reference/max error/
  must equal/leaked bytes/…). On on_before_tool it flags when the agent's Bash
  activity writes a computed value into an output/metric file. On an exit-intent
  turn (finish_reason∈{end_turn,stop}, no tool calls), when both conditions hold
  and it has not yet fired, it injects one one-shot user message (via the proven
  keepalive-tool-call exit-intercept path) telling the agent to reconcile the
  written value against the task's stated bound, and — critically — NOT to exit
  if its own earlier analysis already flagged the result as wrong, but to trace a
  root cause and recompute. Bounded to one fire; contract-clean; content-agnostic
  (arms off vocabulary + agent activity, never task ids). (C-001)
- `config.yaml` — registered the new `file://…::MetricReconcileReminderProcessor`
  immediately after `ServiceDepsReminderProcessor`; repointed the service-deps
  `file://` path to this round's output dir. Everything else byte-identical to
  R1/c2. Additive — the stock CustomSelfVerifyProcessor is preserved (this is
  NOT the sibling correctness-self-verify candidate that replaces it; this hook
  is narrower and runs after it).

### Evidence

- `task_000396_e56917e2` result.json final_pytest: `assert 0.6201280000000001 <
  0.1`; msg 0 task text "the integration diverges and fails to match the
  analytical reference"; msgs 43/45 agent narrates the result looks wrong; msgs
  51-62 self-verify follow-up re-checks only files/Makefile/dt_new, then exits.
- `task_001653_c4cafa73` late narrations: declares done confirming "the output
  file ... created with the correct format: Centroid: 37.3120 ... Distance:
  17.6896"; verifier fails on the VALUES not the format.
- `task_000748_c9807703` late narration: "The leak_bytes value changed from 138
  to 214 because valgrind's output can vary slightly between runs" — notes the
  metric is uncertain, exits anyway; verifier compares leak_bytes.txt to an
  expected count.

### Uncertainty

Assumes the model acts on the reconcile nudge rather than skimming past it and
re-declaring done — the same instruction-adherence risk as any exit nudge, but
delivered exactly at the exit decision via the existing gate and conditioned on
the run having actually written a metric. The nudge cannot conjure correct math
the model can't do (task_000396 has a residual second RK45/grid-alignment bug;
task_001653 needs correct ETL arithmetic), so if R3 shows no criterion-task
flip and no cost spike, the residual blocker is capability and the next
escalation would be a Control hook that actively re-derives/compares the metric
at exit rather than nudging. Watch for regressions on any criterion-task that
flips F with this processor firing.

## Round 2 — self-test integrity (anti-circular-fixture) (c6)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T07:10:00Z
hypothesis_id: h_self_test_integrity_v1
levers: [control]
predicted_affected: [task_000505_50b5162d, task_000015_89886d8d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the 'circular self-test / self-fabricated-fixture' slice of the large exit_reason=done/reward=0 cluster: tasks that extract a value from a lossy/authoritative source (OCR of an image, strings/decode of a binary) then 'verify' the deliverable only against a fixture built by echoing that same extracted value, so the self-test passes regardless of extraction correctness while the grader tests against the REAL provided inputs. Generalizes to any unseen task whose deliverable depends on an extracted value the grader exercises against real artifacts the agent never touched."
regression_risk: "Low. New singleton group self_test_integrity_reminder, _order=93 (after CustomSelfVerify=90 and ServiceDeps=91) so the exit-intent hooks serialize; replaces nothing; injects one user message via the proven contract-clean keepalive-tool-call path; fires at most once per task and ONLY when BOTH an extraction signal (tesseract/ocr/strings|grep/base64 -d/hexdump/...) AND a self-fabricated-fixture signal (echo/printf > file, cat > file <<EOF, tee) have been observed. Tasks without both signals are byte-for-byte unaffected. Worst false positive: one ~300-token advisory on a task that both extracted text and echoed to a scratch file. Always yields to a genuine exit on the next turn."
cost_shift: "+~300 tokens once + typically +1-3 Bash re-verify round-trips on the small armed subset; ~0 on the non-extraction majority. Net positive vs a wasted 0-reward run."
rollback_trigger: "If R3 shows task_000505 still failing test_adversarial_corpus AND task_000015 still failing test_migrate_script_accuracy with this processor firing, the residual blocker is extraction/reasoning capability (the model can't read the value correctly even when told to re-check), not verification integrity — revert SelfTestIntegrityReminderProcessor."
-->

### Why

Assigned focus task_000505_50b5162d fails `exit_reason=done`, `finished=no_tool_calls`,
`reward=0` at only 9/80 steps. The task: OCR an SSH public key out of
`/app/evidence.png`, then write `detect_trojan.sh` that flags any ELF binary
containing that key. tesseract returned a GARBLED key
(`ssh-ed25519 AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8` —
`IZDII` for `lZDI1`, spaces around `+`, a stray `|`, truncated tail). The agent
hardcoded that garble into the script and then "verified" it by `echo`-ing the SAME
garbled key into `/tmp/malicious_test.txt` and confirming the detector matched it —
a circular self-test that passes no matter how wrong the OCR was — plus a `/bin/ls`
check (trivially clean). It never listed `/app` for the real trojaned binaries and
never re-checked the OCR. The grader runs the detector against the real evil corpus
(whose binaries carry the CORRECT key), so the garbled search string matches nothing:
`2 of 2 evil bypassed: ls_evil, cat_evil`.

The same verification-integrity gap recurs at task_000015_89886d8d: a routing schema
is OCR'd from a PNG, and the agent verifies its migrate script ONLY by running it on
the 3-line provided `sample_urls.txt` (a self-selected trivial fixture), never on the
golden corpus the grader scores — accuracy 0.0000. Both share one harness-actionable
root: the agent's verification fixture is fabricated from / narrower than the
authoritative inputs, so a wrong extraction survives the self-test. This is distinct
from the pending correctness-self-verify bet (re-derive a NUMBER a second way) and the
pending OCR-ladder bet (drive OCR better) — here the agent DID verify and DID run OCR;
the specific pathology is the self-fulfilling fixture.

### Changes

- `processors/self_test_integrity_reminder.py` — new `SelfTestIntegrityReminderProcessor`
  (MultiHookProcessor, singleton group `self_test_integrity_reminder`, `_order=93`).
  Keyed off the agent's own Bash activity, never task ids. Arms only when BOTH an
  extraction signal AND a self-fabricated-fixture signal are observed. On the next
  exit-intent turn it injects one task-agnostic user message (via the proven
  keepalive-tool-call exit-intercept path) telling the agent that a fixture built from
  its own extracted value cannot detect an extraction error, and to (1) re-verify the
  extracted value against the authoritative source (re-run extraction with different
  settings; reconcile l/1, O/0, I/1 look-alikes and whitespace) and (2) exercise the
  deliverable against the REAL provided inputs, not only self-built fixtures. Bounded
  to one fire; contract-clean; content-agnostic. (C-001)
- `config.yaml` — registered the processor via absolute
  `file://…/R2/c6/processors/self_test_integrity_reminder.py::SelfTestIntegrityReminderProcessor`
  immediately after `ServiceDepsReminderProcessor` (order 93 so exit-intent hooks
  serialize); everything else byte-identical to R1/c2.

### Evidence

- `task_000505_50b5162d` msg 3 (tool): `tesseract ... ssh-ed25519
  AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8` (OCR garble).
- `task_000505` msg 7: `cat > /home/user/detect_trojan.sh` hardcodes
  `SSH_KEY="ssh-ed25519 AAAAC3NzaC1IZDII..."` (the garble) into the script.
- `task_000505` msg 9: `echo "ssh-ed25519 ...IZDII..." > /tmp/malicious_test.txt`
  then runs the detector on it → exit 1 "correct" — circular.
- `task_000505` result.json final_pytest: `AssertionError: 2 of 2 evil bypassed:
  ls_evil, cat_evil`.
- `task_000015_89886d8d` msgs 40 & 66: only correctness check runs migrate.py on the
  3-line `sample_urls.txt`; msg 73 concludes "works correctly with the sample URLs";
  final_pytest `Accuracy metric 0.0000 ... below 0.98`.

### Uncertainty

Assumes the model acts on the injected integrity reminder rather than skimming past it
and re-declaring done — the same instruction-adherence risk as any exit nudge, but
delivered exactly at the exit decision via the existing gate. For task_000505 it also
assumes that, once prompted, the model can re-run tesseract well enough to read the key
correctly (partly a capability question) OR find and test against the real binaries in
the environment. If R3 shows both tasks still failing with the processor firing, the
residual blocker is extraction/reasoning capability and the next escalation is a
Control hook that actively enumerates provided input artifacts at exit rather than more
guidance. Watch for regressions where the reminder pushes a needless re-check on an
already-correct extraction task — the arm requires BOTH an extraction signal AND a
self-fabricated-fixture signal to limit this.

## Round 2 — active required-output-path audit (c7)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T07:10:00Z
hypothesis_id: h_required_path_audit_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the TB2 correct-logic-wrong-path structural failure class: any task whose text names an exact absolute output artifact path now gets a concrete EXISTS/MISSING tool result at its exit turn, so a deliverable placed at a plausible-but-wrong filename/extension is surfaced and fixed before finishing. Directly targets task_000010 test_operator_script_exists (k8s_operator.py vs required operator.py); generalizes to every exact-path task where a wrong filename would otherwise silently score 0."
regression_risk: "Low - fires at most once per task, only when the description quotes at least one backtick absolute file path; tasks without a stated exact-path contract never arm (byte-for-byte unaffected). The injected command is read-only (ls/test -e), so it can never create/move/delete or clobber a correct artifact. Ordered after self-verify (90) and svc-deps (91) so exit-intent hooks serialize; each acts only on a turn that still has no tool calls."
cost_shift: "+1 read-only Bash round-trip + ~1 reconcile message on armed tasks that reach exit; +1-2 short mv/cp calls only on tasks that actually had a misplaced deliverable; ~0 on non-armed tasks. Net-positive vs a wasted 0-reward run whose only defect was a wrong output path."
rollback_trigger: "If R3 shows task_000010 still failing test_operator_script_exists with the processor firing (the model ignored the MISSING tool result), OR any previously-passing exact-path task regresses to done/reward=0 or budget_exceeded with this processor firing, revert the RequiredPathAuditProcessor entry."
-->

### Why

Assigned focus: task_000010_644ab1c2 (budget_exceeded, 80 steps) and
task_000015_89886d8d (done, accuracy 0.0000). They have distinct causes.

task_000010 fails two verifier tests. (1) test_operator_script_exists: the task
text says the script MUST live at the exact path /home/user/operator.py; the
agent authored a correct-looking script at /home/user/k8s_operator.py (msg 42)
and never reconciled against the exact required name - the canonical TB2
correct-logic-wrong-path structural failure (playbook: a file at the wrong
location/extension is a hard failure regardless of content). (2)
test_api_success_log: only the ConfigMap was applied through a flaky
port-forward; the service-churn budget-burn root is owned by sibling pending
hypotheses (h_bg_service_hygiene_v1) and is NOT re-proposed here.

The stock/self-verify pipeline only narrates a TEXT "verify your outputs"
reminder, which the model demonstrably skims past and re-declares done. This
round is the mechanical escalation for the exact-path half: extract the
backtick-quoted absolute output paths from the task text and, at the first
exit-intent turn, run a REAL read-only Bash existence check over those exact
paths so the ground-truth EXISTS/MISSING listing lands in context as a tool
result the model cannot narrate past, then append one reconcile instruction to
move/create any MISSING deliverable to its exact required path before finishing.

task_000015 is a model OCR/reasoning capability gap: the garbled OCR (msgs
2/4/12) carried the correct output keys (product_id/category/order - visible
even garbled), but the agent wrote output keys department/sort_order derived
from the literal query-param names (msg 37 ROUTES), so zero records matched
(accuracy 0.0000). The correct answer was in context and the model
misinterpreted the mapping direction - no harness mechanism reliably conjures
correct interpretation of garbled OCR, and prior rounds already tried an OCR
ladder and a rigor reminder (both pending). Logged as a capability gap; not
patched here (see candidates.md Note).

### Changes

- processors/required_path_audit.py - new RequiredPathAuditProcessor
  (MultiHookProcessor, singleton group tb2_required_path_audit, _order=93).
  On on_task_start parses backtick-quoted absolute file paths from the task
  description (prefers paths near a produce/output verb). At the first
  exit-intent turn (finish_reason in {end_turn,stop}, no tool calls), if it has
  at least one required path, rewrites the model turn into a single REAL
  read-only Bash existence check (ls -la / MISSING) over those exact paths, then
  queues one reconcile instruction on the next on_before_model. Bounded to one
  fire per task; never mutates the filesystem; keyed off generic path syntax,
  never off task ids; contract/dry-fire/canonicalize clean. (C-001)
- config.yaml - registered the new file://...::RequiredPathAuditProcessor after
  ServiceDepsReminderProcessor; everything else byte-identical to R1/c2.

### Evidence

- task_000010_644ab1c2 msg 0 (prompt): "write a Python script at
  /home/user/operator.py" - exact required output path, backtick-quoted.
- task_000010_644ab1c2 msg 42 (tool): agent's authored path
  /home/user/k8s_operator.py - plausible-but-wrong filename.
- task_000010_644ab1c2 result.json final_pytest: FAILED
  test_operator_script_exists (assertion on os.path.isfile of the exact path).
- task_000015_89886d8d msgs 2/4/12 (tesseract output) show product_id /
  category / order in the garble; msg 37 ROUTES uses department/sort_order;
  final_pytest Accuracy metric 0.0000 below 0.98 - capability gap, skipped.

### Uncertainty

Assumes that once the MISSING /home/user/operator.py line is a concrete tool
result in context, the model acts on it with a one-line mv/cp - a small
in-budget fix (it had already authored a working script). The mechanism is
stronger than a text reminder because ground truth arrives as a tool result, but
for task_000010 the run must also reach an exit-intent turn before
budget_exceeded; the flip therefore also depends on the service-churn budget-burn
not consuming all 80 steps (owned by a sibling). If R3 shows task_000010 still
failing test_operator_script_exists with this processor firing, the model is
ignoring the MISSING result and the next escalation is a Control hook that offers
a disambiguated candidate-path list. Watch any exact-path task that flips F with
this processor firing.
