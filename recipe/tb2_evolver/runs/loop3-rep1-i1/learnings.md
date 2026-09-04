# Evolve journal — loop3-rep1-i1

## Round 1 — no-op (all failures model/env)

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-10T09:00:00Z
hypothesis_id: h_noop_r1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=22/28; +3/-3 gained=code-from-image,configure-git-webserver,portfolio-optimization lost=headless-terminal,largest-eigenval,query-optimize; score 0.7857 >= incumbent(mean) 0.7857 - tol 0.0667
expected_global_gain: "0 flips; protects the long-passing-task cluster (cobol-modernization 90 steps, build-cython-ext 63 steps, sanitize-git-repo 41 steps) from a speculative loop/step-cap regression"
regression_risk: "none — config is byte-identical to R0"
cost_shift: "0 — no change"
rollback_trigger: "n/a (no-op)"
-->

### Why

R0 scored 22/28. The routed evidence attributes **0 defects to the
harness/tool layer**: 5 failures routed to MODEL (SFT), 1 to ENV. The
affordance-gap section confirms recovery was *reachable* in the current
config — errors were surfaced in full and step budget remained — so the
gaps are model capability, not harness mechanism. Per SOUL.md's
evolution philosophy, these are not the harness's job.

Failure triage (verified against trajectory bodies):

- `cancel-async-tasks` (MODEL): agent got "Cancellation test PASSED" at
  step 5, then step 6 spun ~2000 tokens of rumination ("let me try /
  actually / OK I've been overthinking this" ×15) and emitted a
  self-authored blocking test (`await asyncio.sleep(10)`) that hit the
  Bash timeout (exit 124); the *next* model request then stalled
  ~11 min until wall-clock interrupt (`input_tokens=0`,
  `stop_reason=interrupted`). Verifier missed only 1/6 edge-case tests.
- `code-from-image` (ENV/MODEL): `tesseract-ocr` unavailable (`E: Unable
  to locate package`), `file` command missing (exit 127); then the next
  model request stalled ~20 min until interrupt. `/app/output.txt` never
  written. Routed ENV.
- `custom-memory-heap-crash` (MODEL): release build SIGSEGVs
  (returncode -11) while debug build passes 5/6 tests; model did not
  diagnose the release-only memory bug.
- `portfolio-optimization` (MODEL): C extension correct but speedup
  1.12x < required 1.2x on N=5000/6000 — an optimization-depth gap.
- `pytorch-model-cli` (MODEL): CLI predicts digit `2` and the agent
  verified thoroughly (37 steps) against the one image present, but the
  verifier uses a different unseen image → wrong prediction. Cannot be
  inferred from the task description.
- `configure-git-webserver` (MODEL): apt 404s + webserver returns 404;
  model did not complete the serving config.

### Changes

- `config.yaml` — byte-identical copy of R0 (explicit no-op).

### Evidence

- `_ROUTED_EVIDENCE.md`: "attributed to the harness/tool layer (yours): **0**".
- `cancel-async-tasks` episode: step-6 assistant message loops on
  "let me try a different approach ... actually ... OK I've been
  overthinking this"; final tool `(exit 124, no output captured)` then
  `stop_reason: interrupted`, `usage.input_tokens: 0`.
- `code-from-image` result.json: `n_input_tokens: 6064`, agent_execution
  ran 07:53:28→08:13:28 (~20 min) though only 2 productive steps —
  a stalled model request, not a config-addressable loop.
- Passing long-task cluster: `cobol-modernization` 90 steps / `reward=1`,
  `build-cython-ext` 63 steps / `reward=1`, `sanitize-git-repo` 41 steps
  / `reward=1` — any step-cap tightening or loop-breaker to catch the
  rumination in `cancel-async-tasks`/`pytorch-model-cli` would put these
  at direct regression risk.

### Uncertainty

The only recurring *harness-adjacent* signal is semantic rumination /
over-verification (`cancel-async-tasks` step 6, `pytorch-model-cli`
37 steps). A mechanical loop-breaker can't distinguish it from
legitimate long work (the 90/63/41-step passers) without high
false-positive risk — so shipping one trades a speculative 1-2 task
flip for a material regression on a larger passing cluster, which the
Pareto rule forbids. The stalled-model-request deaths
(`cancel-async-tasks`, `code-from-image`) are inference-infra, not
config-editable (`request_timeout_sec` lives in agent kwargs). If a
future round observes the rumination pattern recur on >=3 tasks with a
clean separation from passing long tasks, revisit as a Control
candidate (e.g. an `on_step_start` no-progress detector keyed on
repeated identical tool commands, not step count).


## Round 2 — no-op (stall=infra, rest=model)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-10T09:30:00Z
hypothesis_id: h_noop_r2
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=21/28; +2/-3 gained=largest-eigenval,pytorch-model-cli lost=build-cython-ext,cobol-modernization,code-from-image; score 0.7500 >= incumbent(mean) 0.7738 - tol 0.0667
expected_global_gain: "0 flips; protects the passing cluster (portfolio-optimization 1233s, cobol-modernization 842s, build-cython-ext 436s, sanitize-git-repo, configure-git-webserver) from a speculative time-reminder / loop-breaker regression"
regression_risk: "none — config is byte-identical to R1/R0"
cost_shift: "0 — no change"
rollback_trigger: "n/a (no-op)"
-->

### Why

Same 6 failures as R1. This round I localized the mechanism precisely
against the episode JSONL timing and confirmed **no failure is
config-addressable with positive expected global gain**:

- **Stalled-inference cluster (4/6):** `cancel-async-tasks`,
  `custom-memory-heap-crash`, `largest-eigenval`, `headless-terminal`
  all exited `interrupted`. Measured last-productive-tool timestamps:
  cancel-async 231s/900s (26%), largest-eigenval 249s/900s (28%),
  custom-memory-heap 352s/1800s (20%). In each case a single
  `provider.complete()` request then stalled **651-1447s** and ate the
  rest of the wall clock. `request_timeout_sec=600` is set but not
  firing (vLLM at 127.0.0.1:8303 hangs); the run loop
  (`runloop.py:419`) doesn't wrap `complete()` in `wait_for`, and no
  processor hook can abort an in-flight request. -> infra, not
  `config.yaml`-editable. Logged in `NEEDS_FROM_HUMAN.md` #2.
- **Model-capability cluster (2/6):** `pytorch-model-cli` — wrong digit
  prediction on an unseen verifier image (5/6 tests pass;
  uninferrable from the task). `query-optimize` — SQL runtime 0.75x
  golden, an optimization-depth gap (5/6 tests pass). No harness fix.

I also considered restoring the **dead `TaskTimeReminderProcessor`**
(see below) and rejected it: its retroactive check (Variant A) is
**no** — the stalls occur at 20-28% of budget, so the 70%/90% nudges
would never fire in time; and setting a static `timeout_seconds` risks
a premature "finalize now" nudge on the 1233s passing task
(portfolio-optimization). No evidence of gain + real regression risk =
drop, per the Pareto rule.

### Discovery (latent bug, not this round's failure cause)

`TaskTimeReminderProcessor` and `EnvironmentContextInjector` are wired
with `timeout_seconds` **only** on the default-builder path. When an
evolved `config.yaml` is loaded (`agent.py:189-192` uses
`HarnessConfig.from_yaml_file` without re-injecting `task_timeout`),
both deserialize with `timeout_seconds=None`, so `on_step_start`
short-circuits (`harness.py:246`) and the time-budget mechanism is
dead in every evolved run. Fix requires editing `agent.py` — outside
meta-agent write scope; the per-task timeout is not reachable from any
processor event. Logged in `NEEDS_FROM_HUMAN.md` #1. Not urgent for
score (would not have flipped any R2 failure).

### Changes

- `config.yaml` — byte-identical copy of R1/R0 (explicit no-op).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — two harness-layer items
  (stalled `complete()`; dropped `timeout_seconds` on YAML path).

### Evidence

- Final-gap sweep (last `raw_tool` -> `episode_end`) across all 28
  tasks: every passing task with a measurable gap is <12s; the three
  interrupted tasks are 651s / 668s / 1447s — clean separation.
- Budget-fraction of last productive tool: 26% / 28% / 20% — a time
  reminder keyed on 0.70/0.90 cannot fire before the stall.
- `largest-eigenval` verifier: 26/27 pass, only `test_speedup[2]`
  (tiny-input edge case) fails — substantially solved before interrupt.
- `query-optimize` verifier: `speedup_solution_vs_golden: 0.746`,
  `test_compare_golden_vs_solution_runtime` FAILED, 5/6 pass.
- `cancel-async-tasks` step-8 assistant (08:53:14): "The test passes.
  All 3 tasks started and their cleanup code ran when the task was
  cancelled." — solved, then over-verified into a stall.

### Uncertainty

Until the vLLM stall is fixed at the provider/run-loop layer, no
`config.yaml` change moves this benchmark. If a future round shows the
stall resolved but premature finalize appears, restoring
`TaskTimeReminderProcessor` becomes worth it — but only once `agent.py`
re-injects a per-task `timeout_seconds`, which the meta-agent cannot do.


## Round 3 — no-op (variance-dominated; stall confirmed infra)

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-10T10:30:00Z
hypothesis_id: h_noop_r3
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=21/28; +3/-3 gained=build-cython-ext,code-from-image,headless-terminal lost=largest-eigenval,portfolio-optimization,pytorch-model-cli; score 0.7500 >= incumbent(mean) 0.7679 - tol 0.0667
expected_global_gain: "0 flips; protects the passing cluster from a speculative loop-breaker / compaction-tighten / time-reminder regression that the per-task variance shows would be graded against noise"
regression_risk: "none - config is byte-identical to R0/R1/R2 (md5 de40915c...)"
cost_shift: "0 - no change"
rollback_trigger: "n/a (no-op)"
-->

### Why

R2 scored 21/28 (repeats 22,22,21 - within spread). Same byte-identical
config (`426fa407196e6210`) across R0/R1/R2 produces **different per-task
outcomes each round**, which is the decisive signal this round:

- build-cython-ext: T,T,**F** ; cobol-modernization: T,T,**F**
- code-from-image: F,**T**,F ; headless-terminal: T,**F**,F
- largest-eigenval: T,**F**,T ; query-optimize: T,F,F
- pytorch-model-cli: F,F,**T** ; portfolio-optimization: F,T,T

Tasks flip pass<->fail with no config change. The failures are not a
deterministic, config-addressable property of the harness - they are
run-to-run variance driven by (a) the vLLM stall striking a different
task each run and (b) near-miss model edge cases (5/6 or 10/11 tests).
Any intervention I ship would be graded against this same noise and
either falsely accepted or auto-reverted. Routing agrees for the 3rd
consecutive round: **0 defects attributed to the harness/tool layer.**

### Stall root cause - verified in run-loop code this round

Confirmed the prior rounds' "infra, not config-editable" claim against
the source, not just timing:

- `harnessx/core/runloop.py:419` - `await active_model_provider.complete(...)`
  is **not** wrapped in `asyncio.wait_for`. `before_model` fires before
  line 419 and `after_model` after; **no processor hook exists during
  an in-flight request**, so no `MultiHookProcessor` in the pipeline can
  abort a hung `complete()`.
- `litellm_provider.py:197-214` does pass `request_timeout`/`timeout` to
  litellm, but this lives in the model/provider kwargs, not in the
  behaviour-pipeline `config.yaml` the meta-agent edits - and vLLM hangs
  at a level `request_timeout` isn't catching.

### Stall is NOT context-size correlated (rules out compaction tune)

Context at the stall (from `*_state.json`):
- code-from-image: **3,513 tokens**, stalled at step 2 -> tiny context,
  still an 1200s stall.
- cobol-modernization: 94,269 tokens, step 10 (12-min gap 09:31:35 ->
  09:43:24 in the episode jsonl, then interrupt; `/app/program.py`
  never written - task was mid-exploration, budget remained).
- custom-memory-heap-crash: 337,802 tokens, step 13.

code-from-image stalling at 3.5k tokens definitively rules out "large
context triggers the hang", so tightening CompactionProcessor
(`token_threshold: 140000`) would buy zero stall relief while risking
regressions on the passing long-context tasks. Dropped on the Pareto
rule.

### Non-stall failures = model/env, per verifier tails

- build-cython-ext (done, 69 steps): 10/11 pass, 1 AttributeError - model.
- cancel-async-tasks (13 steps): 5/6, edge case test - model.
- headless-terminal (done, 18 steps): 6/7, `mkdir /server` "File exists"
  (non-idempotent code) - model.
- query-optimize (done, 29 steps): SQL 0.75x speedup vs golden bar
  - optimization-depth gap, model.

### Changes

- `config.yaml` - byte-identical copy of R2/R1/R0 (explicit no-op),
  md5 `de40915c8013a0a510fe1ee2f9d66661`. canonicalize ok=true.

### Evidence

- `history/R0,R1,R2 _per_task.json`: 8 tasks flip pass<->fail under the
  identical config (listed above).
- `_ROUTED_EVIDENCE.md`: "attributed to the harness/tool layer (yours): **0**".
- `runloop.py:419`: unwrapped `complete()`; `state.json` token counts
  (3.5k / 94k / 338k) at the three stalls show no context correlation.

### Uncertainty

The benchmark is variance-bound at ~21-22/28 under this config; no
`config.yaml` lever moves it until the vLLM `complete()` stall is fixed
at the provider/run-loop layer (logged in prior rounds'
NEEDS_FROM_HUMAN). If a future round shows the stall resolved and a
*deterministic* config-addressable failure recurs on multiple tasks with
clean separation from the passing cluster, revisit - but shipping now
would trade noise for regression risk, which the Pareto rule forbids.


## Round 4 — no-op (4/6 fails = uninterruptible in-flight stall; 2/6 model near-miss)

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-10T11:30:00Z
hypothesis_id: h_noop_r4
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=23/28; score 0.8214 >= incumbent(mean) 0.7786 - tol 0.0667 (final-round scoring)
expected_global_gain: "0 flips; protects the passing cluster (build-cython-ext 353s exec, code-from-image, headless-terminal, plus the ~21/28 core) from a speculative session-shortener / loop-breaker / time-reminder regression that this round's per-task timing shows cannot touch the actual failure mechanism"
regression_risk: "none — config byte-identical to R0/R1/R2/R3 (md5 de40915c8013a0a510fe1ee2f9d66661)"
cost_shift: "0 — no change"
rollback_trigger: "n/a (no-op). Revisit only if a future round shows the vLLM complete() stall resolved (exec_s < per-task timeout on the failing set) AND a deterministic config-addressable failure recurs on >=3 tasks with clean separation from passers"
-->

### Why

R3 scored 21/28. This round I re-derived the failure mechanism from
`result.json` timing + episode JSONL gaps rather than trusting the prior
no-op verdicts, and the evidence is now *sharper* than R1–R3: **4 of 6
failures are in-flight `provider.complete()` stalls that no config-layer
processor can interrupt, and the remaining 2 are 5/6-test model
near-misses.** Routing agrees for the 4th consecutive round: 0 defects
attributed to the harness/tool layer.

Failure split (verified against `agent_execution` timing + last
productive-tool timestamp in the episode jsonl):

- **Stall cluster (4/6)** — exec_s equals the exact per-task wall clock,
  with a multi-minute gap between the last productive tool and
  `episode_end`:
  - `pytorch-model-cli`: last productive tool at **step 15, 10:31:21**,
    then dead until interrupt at **10:44:15** (~13 min stall); exec_s=900
    (=wall clock). Verifier: `test_cli_tool_exists`/`test_prediction_file_exists`
    FAILED — the stall struck *mid-work*, before required outputs were
    written. This is NOT over-verification a shortener could pre-empt.
  - `largest-eigenval`: last productive tool 10:29:50 → episode_end
    10:41:26 (~11.5 min stall); exec_s=900. Verifier 26/27 pass, only
    `test_speedup[6]` (edge case) fails — solution was written, then
    stalled; even with the stall fixed this stays a model near-miss.
  - `custom-memory-heap-crash`: exec_s=1800 (=2× wall clock).
  - `portfolio-optimization`: exec_s=2998 (=wall clock).
- **Model near-miss cluster (2/6)** — ended early, NOT stalls:
  - `query-optimize`: exec_s=295, ended cleanly. SQL is *correct*
    (`test_outputs_match_exactly` passes) but 0.75× golden speed
    (`speedup_solution_vs_golden: 0.748`) — optimization-depth gap.
  - `cancel-async-tasks`: exec_s=73, ended cleanly. 5/6 pass; missing
    cleanup-on-cancel for the above-max-concurrent case
    (`assert 0 == 2` cleanup count) — a code-correctness gap.

### Stall is structurally unreachable from config.yaml — reconfirmed at source

Read `harnessx/core/runloop.py` this round:
`model_response = await active_model_provider.complete(...)` is **not**
wrapped in `asyncio.wait_for`/`asyncio.timeout` (grep for `wait_for`
returns only the `stream_callback` kwarg at line 126 and the `complete`
call at 422 — no timeout wrapper). `before_model` fires *before* the
call and `after_model` *after*; **no processor hook exists during the
in-flight request**, so no `MultiHookProcessor` I can add to the pipeline
can abort a hung `complete()`. `request_timeout_sec=600` is set in agent
kwargs (not meta-editable) yet the stalls run 900/1800/2998s — the vLLM
hang is below litellm's timeout layer. Confirmed prior rounds' claim.

### Why the one speculative lever is net-negative (Pareto)

The only harness-adjacent idea is shortening sessions to reduce the
number of `complete()` dice-rolls and thus stall exposure. The R3 timing
refutes it: the stall hit `pytorch-model-cli` at **step 15, ~2 min into a
mid-work session** — not during a tail of over-verification. A shortener
(tighter step cap / loop-breaker / earlier finalize nudge) would not have
avoided that stall, and would put the passing long-work tasks
(build-cython-ext 353s exec, and portfolio when it passes) at direct
regression risk. High collateral, ~zero addressable upside → drop.

### Changes

- `config.yaml` — byte-identical copy of R3/R2/R1/R0, md5
  `de40915c8013a0a510fe1ee2f9d66661`. canonicalize ok=true,
  checked_templates=0.

### Evidence

- `pytorch-model-cli/result.json`: `agent_execution` 10:29:15→10:44:15
  (=900s wall clock), `n_output_tokens: 0`; episode jsonl last `raw_tool`
  at step 15 / 10:31:21 then `episode_end` 10:44:15 (~13 min dead).
- `largest-eigenval/result.json`: exec 900s; episode last tool 10:29:50
  → `episode_end` 10:41:26; verifier tail `1 failed, 26 passed`,
  only `test_speedup[6]`.
- `custom-memory-heap-crash`/`portfolio-optimization`: exec_s 1800/2998
  = per-task wall clock (stall-to-interrupt).
- `query-optimize` verifier: `speedup_solution_vs_golden: 0.748`,
  `test_outputs_match_exactly` PASSED (correct query, slow) — exec 295s.
- `cancel-async-tasks` verifier: `test_tasks_cancel_above_max_concurrent`
  FAILED `assert 0 == 2`, 5/6 pass — exec 73s.
- `_ROUTED_EVIDENCE.md`: "attributed to the harness/tool layer (yours): **0**".
- `runloop.py`: `complete()` call has no `wait_for` wrapper; no
  in-request processor hook.

### Uncertainty

The benchmark stays variance-bound at ~21–22/28 under this config; no
`config.yaml` lever moves it while the vLLM `complete()` stall accounts
for the majority of failures and lives below the meta-editable surface
(logged in prior rounds' NEEDS_FROM_HUMAN). Rollback/revisit trigger:
a future round where exec_s drops below the per-task timeout across the
failing set (stall resolved) AND a deterministic, config-addressable
failure recurs on ≥3 tasks with clean separation from the passing
cluster.
