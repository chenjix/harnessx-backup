# Evolve Journal — tmax-coev-rep22-i2

## Round 1 — cyclic tool-loop detector

<!-- journal:frontmatter
round: 1
timestamp: 2026-05-01T00:00:00Z
hypothesis_id: h_cyclic_loop_v1
levers: [control]
predicted_affected: [task_000011_d089ef35, task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=20/50; +3/-4 gained=task_000011_d089ef35,task_000106_23215092,task_000760_e197f7ff lost=task_000587_9862bb19,task_000683_7c966a71,task_001090_c61c71f2,task_001832_dd672877; gating disabled (tolerance < 0)
expected_global_gain: "Terminates degenerate period-k tool-call loops early, reclaiming ~900s/~75 steps per stuck task so one task can't starve the round; occasionally lets a recovering agent finish."
regression_risk: "False-positive kill of a task that legitimately re-runs an identical command 3x with identical output — mitigated by call+output identity requirement and max_cycles=3."
cost_shift: "Net negative (savings): early termination of stuck tasks; only a sha256 per tool result otherwise."
rollback_trigger: "If any previously-passing task flips F with exit_reason=loop_detected, or pass_rate drops, revert."
-->

### Why

Assigned focus task_000011_d089ef35 (scientific_computing) failed after 79
steps / 962s in a strict A-B-A-B tool-call loop: the model kept "verifying" its
MSE server by re-running two Bash commands that returned byte-identical output
each time (`Q0: 8.25` and `Q0..Q3`), interleaved with token-limit cut-offs, for
~10 full cycles, and never terminated on its own. The existing
`LengthTruncationRecoveryProcessor` never engaged because these turns *carry
tool calls* (its consecutive counter resets on any tool call). The
`LoopDetectionProcessor` is not even in the pipeline, and its exact detector
only counts period-1 consecutive repeats — the alternating A-B pattern resets
its count to 1 every step. task_000010_644ab1c2 shows the same class:
fingerprint repeated 17× → budget_exceeded at 80 steps / 787s. This is a
genuine harness deficiency: a degenerate multi-step cycle that no current
mechanism terminates.

The underlying wrong MSE values in task_000011 (Q2/Q3 computed as 38.25/55.25
instead of 68.25/93.25) are a model reasoning/capability gap, not a harness gap
— no prompt injection should hand-feed the correct extraction. Skipped as
capability gap; the harness fix targets only the non-termination.

### Changes

- `processors/cyclic_loop.py` — new `CyclicToolLoopDetector` MultiHookProcessor.
  Fingerprints (tool_name, tool_input, result); detects a repeating cycle of
  period 1..max_period at the window tail; warns at 2 identical cycles, raises
  `LoopDetectedError` (clean exit_reason=loop_detected) at 3.
- `config.yaml` — register the processor after PostCompactionRefreshProcessor.

### Evidence

- task_000011 result.json: steps=79, elapsed_s=962, finished=no_tool_calls,
  reward=0. messages.json: `uniq -c` over tool results shows exactly two
  distinct outputs, each 10×, in strict alternation (period-2 cycle).
- task_000010 result.json: steps=80, elapsed_s=787, exit=budget_exceeded.
  Fingerprint `ae0ed8` × 17, tail = 10+ consecutive identical calls (period-1).
- task_000106_23215092 (NON-target, guards against false positive): period-5
  cycle but outputs change across cycles (Traceback → success after scipy
  install); the combined call+output fingerprint differs each cycle so the
  detector stays silent. Verified in a standalone simulation of the detection
  logic: A-B×10 raises at index 5; the varying-output cycle returns `none`.

### Uncertainty

Whether early loop-detected termination flips reward on task_000011 is
unlikely (the wrong answer is a reasoning error); the primary win is reclaimed
budget and round-robustness. Watch for any T→F regression with
exit_reason=loop_detected on previously-passing tasks — that would mean the
cycle threshold is too tight; rollback if so.

## Round 2 — OCR low-DPI remediation guard

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-01T05:00:00Z
hypothesis_id: h_ocr_low_dpi_guard_v1
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000505_50b5162d, task_002063_8c8adcfe]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the image-OCR cluster (>=3 tasks) where a PNG with no DPI metadata makes tesseract fall back to ~70 dpi and garble the exact tokens (schema mapping, SSH key, CRC constants) the verifier checks"
regression_risk: "Silent on all non-OCR tasks (regex-gated on the engine's own low-DPI warning in tool output); worst case on an OCR task is one ~120-word hint appended once to a tool result"
cost_shift: "Near-zero to slightly cost-negative: at most one short hint per OCR task; expected to curtail the multi-attempt PSM/threshold thrashing (11+ OCR retries seen on task_000015)"
rollback_trigger: "If R3 shows the OCR cluster still failing with garbled text despite the hint (agent ignores rescale advice), or any regression on non-OCR tasks attributable to the injected hint, revert the processor"
-->

### Why

Assigned focus `task_000015_89886d8d` (software_engineering) had to OCR a
routing-schema PNG at `/app/routing_schema.png`, then write a `migrate.py`
that maps URLs to a new JSON schema. tesseract emitted
`Warning: Invalid resolution 0 dpi. Using 70 instead.` and OCR'd the image at
a degraded ~70 dpi. The extracted text was garbled (`eatalogyitem`,
`Ppreduet id`, `edepartment>`); the agent tried contrast/threshold/PSM tweaks
— none of which change the effective resolution — then gave up and *guessed*
the mapping. `final_pytest` accuracy 0.6793 < 0.98 threshold. The same
mechanism recurs on two other tasks: `task_000505_50b5162d` (SSH key OCR'd as
`...H9 + |J9tY +X07yG...` with space/`1`/`I`/`+`/`|` corruption) and
`task_002063_8c8adcfe` (`CRO-1 POLYNOMIAL: 0x9E82`, `INITIAL VALUE OXFFFE` —
`O`/`0` and `C`/`O` confusion on the CRC constants). All three carry the same
`Invalid resolution 0 dpi` warning and none ever attempted the standard
remediation (upscale / explicit `--dpi`) — grep across all three logs found no
`resize`/`--dpi`/`upscale` command. Model has the capability (tesseract + PIL
installed); it lacks the runtime knowledge of when to reach for it. That is a
Control-lever gap: fire a hint keyed on the observable low-DPI warning.

### Changes

- `processors/ocr_quality_guard.py` — new `OcrQualityGuardProcessor`
  (`MultiHookProcessor`). `on_after_tool` matches `_LOW_DPI_RE`
  (`invalid resolution N dpi` / `estimating resolution as N`) in the tool
  result and appends a one-time, task-agnostic hint: the low-DPI fallback
  garbles exact tokens, contrast/PSM tweaks won't help, rescale the image to
  ~300 dpi and re-OCR, cross-check ambiguous values, don't guess. Fires at
  most `max_hints` (default 1) per task; contains no task-specific constants,
  paths, or answers.
- `config.yaml` — register `OcrQualityGuardProcessor` (`_order=31`, right
  after `CustomEditToolProcessor`), before `CustomSelfVerifyProcessor`.

### Evidence

- `task_000015_89886d8d` tool[2..24]: repeated garbled OCR
  (`eatalogyitem`, `Ppreduet id`); step 31 body "let me just proceed ...
  based on the mapping I can infer"; `final_pytest`
  `AssertionError: Accuracy metric 0.6793 is below the 0.98 threshold`.
- `task_000505_50b5162d` tool[4]: `Warning: Invalid resolution 0 dpi ...`
  then `ssh-ed25519 AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8`
  — corrupted key the detector must match exactly.
- `task_002063_8c8adcfe` tool[2]: `(CRO-1 POLYNOMIAL: 0x9E82`,
  `INITIAL VALUE OXFFFE` — wrong CRC constants; also carries the low-DPI
  warning.
- Grep of all three message logs: zero `--dpi` / `resize` / `upscale` /
  `.resize` commands — the standard remediation was never attempted.

### Uncertainty

If a source image is genuinely too small/low-quality to recover even after
upscaling, the hint won't save the task — but it costs little. If the model
ignores the rescale advice, the fix doesn't land and we revert. Note
`task_002063_8c8adcfe` is also cited by Round 1's `h_http_verifier_requests_v1`
for a *later* `requests` collection error; the two hypotheses target different,
sequential blockers (OCR-garble upstream, verifier-dep downstream) — if both
land, that task needs both fixes to pass.

## Round 2 (c2) — no-op: assigned task is a docker-infra flake

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-01T06:00:00Z
hypothesis_id: h_noop_docker_name_conflict_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None from config — the failure is a pre-run-loop docker container-name conflict unreachable by any HarnessConfig artifact; net gain requires a fix in read-only recipe/tmax_eval/docker_env.py (see NEEDS_FROM_HUMAN.md)."
regression_risk: "None — byte-identical config copy."
cost_shift: "Zero."
rollback_trigger: "N/A (no change)."
-->

### Why

Assigned focus `task_000028_7fe033ac` did NOT fail inside the agent loop. Its
`result.json`: `status=error`, `elapsed_s=0.1`, no `messages.json` — the
container never started. Error:
`docker run failed ... Conflict. The container name
"/tmax-task0000287fe033ac-1788232928" is already in use`.

This is systemic, not a one-off: 26 of 137 task result rows (~19%) errored with
the identical container-name conflict, all `elapsed_s~=0.1`, all trajectory-less
(includes R1 targets task_000010/task_001090 and R2 OCR targets
task_000015/task_000505 — prior interventions could not even be evaluated this
round). Root cause is in `recipe/tmax_eval/docker_env.py::start_container`:
`name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"` collides on
same-second retries / un-reaped stale containers, and there is no `docker rm -f`
+ retry. `run_eval.run_one` calls `start_container()` BEFORE
`harness_runner.run_harness_agent()`, so no processor/tool/system-prompt hook in
the meta-agent's write scope fires before `docker run`. The failure is
structurally unreachable by `HarnessConfig`.

### Changes

- `config.yaml` — byte-identical copy of R1 config (explicit no-op).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — root cause + three suggested fixes for
  the read-only `docker_env.py` (rm+retry on Conflict / uuid suffix / reap
  `tmax-*` at startup).

### Evidence

- `task_000028_7fe033ac` result.json: `status=error`, `elapsed_s=0.1`,
  `error="... container name ... already in use ..."`; only `result.json`
  present, no trajectory.
- Sweep of all result rows: `status` counts `{ok:70, agent_error:4, error:26}`;
  every one of the 26 `error` rows carries `docker run failed ... Conflict`.

### Uncertainty

If the docker_env fix lands upstream, these 26 tasks re-run with real
trajectories and the true harness signal (R1/R2 targets) becomes visible again.
Until then, any meta-agent focus assigned to one of these 26 task_ids is
un-actionable from config — recommend the orchestrator re-run affected tasks
before assigning them as evolve focuses.

## Round 2 (c0) — no-op: assigned task_000010 is same docker-infra flake

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-01T06:30:00Z
hypothesis_id: h_noop_docker_name_conflict_v2
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None from config — assigned focus task_000010_644ab1c2 died at docker container startup (elapsed_s=0.1, no messages.json), upstream of any HarnessConfig hook. Real net gain requires fixing container-name generation in read-only recipe/tmax_eval/docker_env.py (see NEEDS_FROM_HUMAN.md)."
regression_risk: "None — byte-identical copy of R1 config."
cost_shift: "Zero."
rollback_trigger: "N/A (no change)."
-->

### Why

My assigned focus `task_000010_644ab1c2` failed with
`docker run failed ... container name '/tmax-task000010644ab1c2-1788232928'
is already in use` — `status=error`, `elapsed_s=0.1`, no `messages.json`. The
run loop never booted; the `HarnessConfig` was never loaded (`run_eval.run_one`
calls `docker_env.start_container()` at line 149, before
`harness_runner.run_harness_agent()` at line 171). This is the same
pre-run-loop docker-name-collision cluster that the c2 proposal
(`h_noop_docker_name_conflict_v1`) diagnosed independently — my sweep of this
50-task trajectory set found 13/50 tasks (26%) with the identical error and
`elapsed_s≈0.1`, including R2 OCR targets task_000015 and task_000505, so R2's
`h_ocr_low_dpi_guard_v1` cannot be judged from this set. No processor / tool /
system-prompt lever can reach a failure that occurs before the container
exists. Independent second confirmation of the c2 finding strengthens the
case for the upstream `docker_env.py` fix.

### Changes

- `config.yaml` — byte-identical copy of R1 config (explicit no-op).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — root cause (1-second-resolution
  timestamp + 20-char task_id-prefix name, no `docker rm -f` + retry) and
  three concrete upstream fixes (uuid suffix / pre-run reap / retry-on-Conflict).
- `_meta_scratch/candidates.md` — "focus unsupported" argument, no C-NNN
  candidate (no reachable lever).

### Evidence

- `task_000010_644ab1c2` result.json: `status=error`, `reward=0`,
  `elapsed_s=0.1`, `error="RuntimeError: docker run failed ... container name
  ... already in use ..."`; no `.messages.json` present.
- `recipe/tmax_eval/run_eval.py:149` `start_container()` precedes `:171`
  `run_harness_agent()` — crash is upstream of the entire config surface.
- `recipe/tmax_eval/docker_env.py::start_container`:
  `name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]`.
- This-set sweep: 13/50 rows are `status=error` with `docker ... Conflict`.

### Uncertainty

None on the diagnosis — the missing trajectory and the crash site are
conclusive. Recommend the orchestrator not assign focuses drawn from the
13-task infra-error set until the `docker_env.py` name-collision fix lands.

## Round 2 (c1) — no-op: assigned task_000015 is same docker-infra flake

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-01T07:00:00Z
hypothesis_id: h_noop_docker_name_conflict_v3
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None from config — assigned focus task_000015_89886d8d died at docker container startup (status=error, elapsed_s=0.1, no messages.json), upstream of every HarnessConfig hook. Its real harness-addressable blocker (OCR low-DPI garble) is already owned by sibling c2's h_ocr_low_dpi_guard_v1; duplicating it here would violate batch diversity. Net gain requires the upstream docker_env.py name-collision fix (see NEEDS_FROM_HUMAN.md)."
regression_risk: "None — byte-identical copy of R1 config (config.yaml + system_prompt.txt)."
cost_shift: "Zero."
rollback_trigger: "N/A (no change)."
-->

### Why

Assigned focus `task_000015_89886d8d` did NOT fail inside the agent loop in this
trajectory set. Its `result.json`: `status=error`, `reward=0`, `elapsed_s=0.1`,
no `messages.json` — the container never started. Error:
`docker run failed ... Conflict. The container name
"/tmax-task00001589886d8d-1788232928" is already in use`. This is the same
pre-run-loop docker container-name-collision cluster that siblings c2
(`h_noop_docker_name_conflict_v1`) and c0 (`..._v2`) diagnosed independently.
My sweep of this 50-task set reproduces 13/50 (26%) `status=error` rows, all
`elapsed_s~=0.1`, all carrying `docker ... Conflict` — a third independent
confirmation.

Root cause is in READ-ONLY `recipe/tmax_eval/docker_env.py::start_container`:
`name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]` collides
on same-second parallel launches (`-c4`) and un-reaped stale containers, with no
`docker rm -f` + retry. `run_eval.run_one` calls `start_container()` (~line 149)
BEFORE `harness_runner.run_harness_agent()` (~line 175), where the HarnessConfig
is first loaded. No processor / tool / system-prompt hook in my write scope can
fire before `docker run`; the failure is structurally unreachable by
`HarnessConfig`.

Separately, the genuine harness-addressable blocker for task_000015 (when the
container DOES start) is the tesseract low-DPI OCR garble that sibling c2's
`h_ocr_low_dpi_guard_v1` (`ocr_quality_guard.py`) already targets. Per the batch
brief ("a batch is useful only when its members differ"), I decline to duplicate
that processor and instead ship the smallest defensible edit: an explicit no-op.

### Changes

- `config.yaml` — byte-identical copy of R1 config (explicit no-op).
- `system_prompt.txt` — byte-identical copy (SiblingSystemPromptBuilder input).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — root cause + three suggested upstream
  fixes for the read-only `docker_env.py` (rm+retry on Conflict / uuid+pid+ms
  suffix / reap stale `tmax-*` at startup); lists all 13 affected task_ids.

### Evidence

- `task_000015_89886d8d` result.json: `status=error`, `reward=0`,
  `elapsed_s=0.1`, `error="RuntimeError: docker run failed ... container name
  ... already in use ..."`; only `result.json`, no `.messages.json`.
- `recipe/tmax_eval/run_eval.py`: `start_container()` (~L149) precedes
  `run_harness_agent()` (~L175) — crash is upstream of the entire config surface.
- `recipe/tmax_eval/docker_env.py::start_container` name formula (above).
- This-set sweep: `status` counts `{ok:35, agent_error:2, error:13}`; every one
  of the 13 `error` rows carries `docker run failed ... Conflict`.

### Uncertainty

None on the diagnosis — the missing trajectory and the crash site are
conclusive. Recommend the orchestrator (a) fix the `docker_env.py`
name-collision upstream, and (b) not assign evolve focuses drawn from the
13-task infra-error set until it lands, so real harness signal (including R2's
pending OCR guard on task_000015) becomes measurable again.

## Round 2 (c3) — numeric single-implementation cross-check

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-01T06:00:00Z
hypothesis_id: h_numeric_crosscheck_v1
levers: [control]
predicted_affected: [task_000111_cbada64a, task_001653_c4cafa73, task_000587_9862bb19]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the deterministic-compute 'committed a wrong number from a single implementation' cluster (>=3 tasks across scientific_computing + data_science): OLS slope 2.5056 vs 2.5997, centroid off by ~5 on every coordinate, wrong CSV numeric parse — one exit-time nudge to independently re-derive the key quantity and reconcile"
regression_risk: "Fires only when BOTH a compute signal and a numeric-output-file signal are seen, and only on the genuine second exit-intent (self-verify consumes the first). Worst case on a matched task is one extra reconcile round; non-numeric tasks never match. A spurious reconcile could in principle re-introduce a bug, but the nudge biases toward keeping a value confirmed two ways."
cost_shift: "Mildly positive on matched numeric tasks (one verify/reconcile cycle, few hundred tokens); zero on the majority that don't match. Fire-once-per-task; bounded by per-call max_tokens."
rollback_trigger: "If R3 shows a previously-passing numeric task flipping F after a spurious reconciliation, or the matched cluster still failing on wrong numbers despite the nudge, revert the processor."
-->

### Why

Assigned focus `task_000111_cbada64a` (scientific_computing) failed in 7
steps / 23s with exit_reason=done. The agent wrote textbook-correct OLS C++
(`m=(N*Sxy-Sx*Sy)/(N*Sx2-Sx*Sx)`), compiled and ran it ONCE, got
`2.5056,1.2262,3.9742,6.2925`, declared "The task is complete", and exited.
`final_pytest`: `Expected m to be approx 2.5997, got 2.5056`
(`abs(0.0941) <= 0.001` fails) — wrong by ~38x the tolerance. The agent had
no independent yardstick: it verified only that the file existed and re-read
the task. This single-implementation numeric-commit shape recurs across the
batch: `task_001653_c4cafa73` committed a centroid `37.31,47.07,38.03` and
distance `17.69` where the grader expects `42.01,45.90,43.07` / `11.34`
(every number wrong, 10 steps, done); `task_000587_9862bb19` committed a
wrong CSV numeric parse (done, 24 steps). All are deterministic computations
where an independent second implementation (python3/numpy vs C++, or a
hand-check on a slice) would have exposed the discrepancy before commit. The
capability and toolchain are present; the agent just never reaches for the
cross-check at the decisive step — a Control-lever runtime-trigger gap, not
a prompt-knowledge gap (the self-verify checklist already asks for semantic
correctness but cannot supply the missing independent comparison).

### Changes

- `processors/numeric_crosscheck.py` — new `NumericCrossCheckProcessor`
  (`MultiHookProcessor`, `_order=92`). Tracks two observable Bash signals per
  task: a compute run (`g++`/`gcc`/`numpy`/`polyfit`/`percentile`/`centroid`/
  ...) AND a numeric result written to an output file (redirect/tee to
  `*.txt|csv|json|dat|out`, `ofstream`, `np.savetxt`, `to_csv`, ...). On the
  genuine exit-intent (after the self-verify keepalive has consumed the first
  one) it injects exactly one task-agnostic user message asking the agent to
  re-derive the key quantity a second, independent way and reconcile before
  finishing. Fires at most once per task; no task ids, paths, constants, or
  algorithms hard-coded.
- `config.yaml` — register `NumericCrossCheckProcessor` after
  `HttpVerifierDepProcessor` (`_order=92`).

### Evidence

- `task_000111_cbada64a` messages.json step 4 tool result `2.5056,1.2262,...`;
  step 5 body "The program compiled and ran successfully"; step 7 body "The
  task is complete". result.json `final_pytest`: `Expected m to be approx
  2.5997, got 2.5056`.
- `task_001653_c4cafa73` result.json `final_pytest`:
  `- Centroid: 42.0095, 45.9009, 43.0685 / + Centroid: 37.3120, 47.0664,
  38.0278` and `- Distance: 11.3444 / + Distance: 17.6896`; exit_reason=done,
  10 steps.
- `task_000587_9862bb19` result.json `final_pytest`: `test_csv_parser_fixed`
  AssertionError on the produced numeric parse; exit_reason=done, 24 steps.
- Validators: canonicalize ok (0 templates), dry_fire 0 likely_bugs,
  contract 0 violations, literals 0 findings.

### Uncertainty

If `task_000111`'s grader value 2.5997 stems from a task-spec nuance the
agent cannot infer (rather than a bug in the agent's OLS), the cross-check
surfaces the discrepancy but may not by itself flip that one task. The
discipline still directly flips `task_001653`-style cases where an
independent recompute exposes the wrong values. Watch R3 for any T->F on a
previously-passing numeric task caused by a spurious reconcile — that would
mean the compute+output signal is too broad; rollback if so.

## Round 2 (c4) — cyclic loop breaker (block-and-redirect)

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-01T08:00:00Z
hypothesis_id: h_cyclic_loop_breaker_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_001857_24daeef3, task_001979_a1e24b6f]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the degenerate period-k tool-loop cluster (>=3 tasks across system_administration/debugging/data_processing) by reclaiming ~25 steps/task, WITHOUT the -4 regression surface R1's hard-kill detector caused — a false trigger costs one blocked repeat + a nudge, never a run termination."
regression_risk: "Low. Output-inclusive fingerprint means productive exploration (install finally succeeds, Traceback becomes success) never matches; a legit identical double-run gets at most one soft warning (break_cycles=3), never a block. No loop_detected exit unless the same loop re-forms max_total_blocks=12 times."
cost_shift: "Net negative (savings): curtails dozens of wasted identical tool executions per stuck task; steady-state overhead is one sha256 per tool result plus text only on a real detection."
rollback_trigger: "If any previously-passing task flips F with exit_reason=loop_detected attributable to this processor, or a matched task blocks then still fails on wrong work with no budget gain, revert."
-->

### Why

Assigned focus `task_000118_3043e92d` (system_administration, budget_exceeded @
80 steps) failed because the agent fixated on an irrelevant zombie process,
repeating a byte-identical (call, output) probe ~28 consecutive times (period-1
loop) across messages 2–59. It self-recognised the loop ("I'm stuck in a loop")
but could not escape; it only reached the actual `deployment_monitor.py` work at
step ~60 with <20 steps of budget left, and the log dir peaked at 209MB vs the
45MB requirement → reward 0. The cluster is systemic: `task_001857_24daeef3`
runs a strict period-2 A-B-A-B cycle (~8 cycles, so `maxconsec_run=1` — period-1
detectors are structurally blind to it), and `task_001979_a1e24b6f` repeats one
fingerprint 37×. The stock `LoopDetectionProcessor` isn't in this pipeline and
its exact detector only counts a period-1 consecutive tail; `LengthTruncation
RecoveryProcessor` resets on any tool call. Genuine harness deficiency: no
current mechanism terminates a degenerate multi-step cycle.

R1's `h_cyclic_loop_v1` (`CyclicToolLoopDetector`) targeted this same shape but
raised `LoopDetectedError` (hard kill). Its gating attribution was +3/-4 — it
flipped 4 previously-passing tasks to F because a `loop_detected` exit ends the
run and can never let a *recovering* agent finish. task_000118's body proves
recovery is possible but too late. This round ships the block-and-redirect
variant instead.

### Changes

- `processors/cyclic_loop_breaker.py` — new `CyclicLoopBreaker`
  `MultiHookProcessor` (`_order=22`). `on_after_tool` fingerprints
  (tool_name, tool_input, result) and appends a soft warning at `warn_cycles=2`;
  `on_before_tool` blocks the incoming repeat (`approved=False` +
  `synthetic_result` redirect) once the tail cycle has repeated `break_cycles=3`
  times, WITHOUT terminating the run, so the agent keeps its remaining budget.
  Safety net: raise `LoopDetectedError` only if the same loop re-forms past a
  run-wide `max_total_blocks=12`. Compaction-aware (clears window on large
  message-count drop). No task-specific constants, paths, or answers.
- `config.yaml` — register `CyclicLoopBreaker` after BgInstallGuard /
  PostCompactionRefreshProcessor, before CustomEditToolProcessor. Retains R1's
  `HttpVerifierDepProcessor`.

### Evidence

- `task_000118_3043e92d`: messages 2–59 = ~28 consecutive identical zombie-probe
  (call, `<defunct>` output) triples; body emits "I'm stuck in a loop"; real
  work starts ~step 60; peak log dir 209MB vs 45MB threshold → reward 0.
- `task_001857_24daeef3`: fingerprints `f2ccf2, 2bf091, f2ccf2, 2bf091, …` (~8
  cycles, indices 8–25) then budget_exceeded — period-2, invisible to period-1
  detectors.
- `task_001979_a1e24b6f`: one fingerprint recurs 37×; exit=error.
- Standalone simulation on the real fingerprint sequences: task_000118 first
  block at index 3 (saves ~25 steps); period-1/period-2 "unbroken" cases block
  then trip the `max_total_blocks` safety net; varying-output and legit
  double-run cases produce 0 blocks (no false positives).
- Validators: canonicalize ok (0 templates), dry_fire 0 likely_bugs, contract
  0 violations, literals 0 findings.

### Uncertainty

Reclaiming budget is necessary but not sufficient — the agent must still do
correct work in the reclaimed steps to flip reward. The certain win is
round-robustness: one stuck task can no longer starve the batch, and unlike the
R1 hard-kill variant a false trigger cannot terminate a passing run. Watch R3
for any T→F with exit_reason=loop_detected attributable to this processor
(would mean the loop re-formed past max_total_blocks on a task that would
otherwise have recovered); rollback if so.
