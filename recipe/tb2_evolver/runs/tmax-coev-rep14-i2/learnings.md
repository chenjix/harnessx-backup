# Evolve Journal — tmax-coev-rep14-i2

## Round 1 — ensure requests for verifier

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-28T21:30:00Z
hypothesis_id: h_verifier_dep_requests_v1
levers: [control]
predicted_affected: [task_000106_23215092, task_000028_7fe033ac, task_000958_4bb2b05d, task_001857_24daeef3, task_002063_8c8adcfe]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=19/50; +1/-2 gained=task_000477_de422e4d lost=task_000737_e692349c,task_000936_2a78f3ca; score 0.3800 >= incumbent(mean) 0.4000 - tol 0.0400
expected_global_gain: "Unblocks a 5-task HTTP-service cluster (5 domains) whose verifier pytest aborts at collection on `import requests` — currently guaranteed reward=0 for a purely infrastructural reason."
regression_risk: "Very low: guard is `|| true`-terminated and runs the original command unchanged after `;`; no-op when requests already imports; fires once, only on armed HTTP-service tasks, so non-service passing clusters are untouched."
cost_shift: "Negligible: one import probe (+ at most one quiet pip install of requests) prepended to a single Bash call on armed tasks only; no extra model turns."
rollback_trigger: "Revert if any previously-passing HTTP-service task regresses to F attributable to the prepended guard, or replay fails on the guard."
-->

### Why

Assigned focus task_000106_23215092 built a correct Flask API on
127.0.0.1:8000, started it, and verified all endpoints with `urllib` — yet
scored reward=0. The failure is entirely in the verifier phase: the injected
`test_final_state.py` begins with `import requests`, the base image lacks
`requests`, so pytest aborts at *collection* (`Interrupted: 1 error during
collection`) and every test errors at once regardless of solution correctness.
The agent had no way to anticipate this — the test file does not exist during
the agent phase, and testing with `urllib`/`curl` is entirely valid. A grep
over all `*.result.json` shows the identical `No module named 'requests'`
collection abort on exactly 5 tasks — task_000106, task_000028, task_000958,
task_001857, task_002063 — every one an HTTP server / microservice task, none
of which used or installed `requests`. This is a harness deficiency, not a
model capability gap.

### Changes

- `processors/verifier_dep_ensurer.py` — new `VerifierDepEnsurer`
  `MultiHookProcessor`. `on_task_start` arms only tasks whose description names
  an HTTP/REST server AND a concrete endpoint/port/listen surface. On the first
  approved Bash call of an armed task, prefixes the command with an idempotent,
  silent, best-effort guard:
  `python3 -c 'import requests' 2>/dev/null || pip install -q requests >/dev/null 2>&1 || true ; <original>`.
  Fires at most once per task; never mutates message history.
- `config.yaml` — register `VerifierDepEnsurer` via absolute `file://` path,
  placed after the time-reminder and before length-recovery (its `_order=10`
  makes it run early among before-tool processors).

### Evidence

- `task_000106_23215092` result.json: `initial_pytest.passed=true`,
  `final_pytest.passed=false, rc=2`, output_tail:
  `import requests` → `ModuleNotFoundError: No module named 'requests'` →
  `Interrupted: 1 error during collection`.
- `task_000106` messages step 18: `curl: command not found` → agent switches to
  `urllib.request`; steps 50-62: all endpoints tested correct via urllib;
  agent never imports/installs `requests`.
- `task_000028_7fe033ac`, `task_000958_4bb2b05d`, `task_001857_24daeef3`,
  `task_002063_8c8adcfe` result.json: identical `import requests` collection
  abort; each prompt describes an HTTP server / microservice with a listen
  surface; grep confirms none of these agents used or `pip install`ed requests.
- Environment supports pip installs in-run (this run's tasks fetched numpy /
  scipy on demand), so ensuring the dependency is viable, not blocked.

### Uncertainty

Two ways to tell if the bet is wrong next round: (1) if the 5 predicted tasks
don't flip, either the agents' solutions were also wrong (task collection now
runs but assertions fail) — in which case the guard did its job and the
remaining gap is a model capability issue to log separately; or (2) if an
offline image makes the pip install fail — the guard is `|| true`, so the task
is exactly as it is today (no new failure). Regression on a previously-passing
HTTP-service task attributable to the guard is the rollback trigger.

## Round 2 — no-op: assigned focus is capability gap, not harness

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-29T00:00:00Z
hypothesis_id: h_noop_task_000264_sql_semantics
levers: []
predicted_affected: []
gating_outcome: reverted
gating_attribution: score=17/50; +2/-4 gained=task_000737_e692349c,task_000936_2a78f3ca lost=task_000133_20c45b39,task_000683_7c966a71,task_000760_e197f7ff,task_001832_dd672877; score 0.3400 < incumbent(mean) 0.4000 - tol 0.0400 -> revert to R0
expected_global_gain: "None claimed. Assigned task_000264 fails for two non-harness reasons; no defensible generalizable mechanism found."
regression_risk: "None — byte-for-byte copy of R0 config."
cost_shift: "Zero — no config change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus: task_000264_ab8c7253 (data_querying). The agent built a
recursive-CTE subordinate-count query, created `idx_employees_manager_id`,
wrote `top_managers.csv` and `query_plan.txt`, and self-verified files exist.
It still scored reward=0 on two independent grounds, both of which are OUTSIDE
the harness's remit:

1. **Semantic off-by-one in the CTE (model reasoning gap).** Agent output
   `Alice (CEO),12 / Bob,5 / Charlie,5 / David,4 / Grace,4`; oracle expected
   `Alice,11 / Bob,5 / Charlie,5 / David,4 / Grace,3`. The base case
   `SELECT e.id AS manager_id, e.id AS subordinate_id` counts each node as its
   own subordinate. Critically the delta is NOT a uniform -1 (Bob/Charlie/David
   match at 5/5/4 while Alice 12→11 and Grace 4→3), so the oracle's exact
   "subordinate" semantics are subtle/ambiguous and the model simply modeled
   the recursion wrong. No harness mechanism can inject the correct counting
   semantics without hardcoding this one task's answer — a task-specific
   domain patch that the evolution philosophy forbids and that would not
   survive the next round.

2. **Brittle verifier substring (`USING INDEX`).** `test_query_plan_output`
   greps `"USING INDEX" in content.upper()`. SQLite emitted
   `USING COVERING INDEX idx_employees_manager_id` — the index IS created and
   used, but the exact substring `USING INDEX` never appears (COVERING breaks
   it). The verifier test file does not exist during the agent phase (TB2
   sandbox topology), so the agent cannot observe this string requirement. This
   is a verifier-brittleness artifact, not something a general harness fix can
   anticipate.

Systemic check: I scanned all 50 result.json. The other failing data_querying
tasks fail by different mechanisms — task_000506 is a fuzz-equivalence logic
error, task_000958 is the `requests`-collection cluster already targeted by
R1's h_verifier_dep_requests_v1. No second task shares task_000264's mechanism,
so the idiosyncratic filter (≥2 distinct tasks, same root cause) is not met.

I considered a general "verify computed results by an independent second
method" strengthening of CustomSelfVerifyProcessor's checklist, but its
retroactive check fails: the off-by-one here is a shared-mental-model error;
re-deriving the count with the same wrong notion of "subordinate" would not
catch it. It would also risk cost inflation and regressions across the many
already-passing clusters. Not shipped.

### Changes

- `config.yaml` — byte-for-byte copy of R0 config (explicit no-op).
  Canonicalizes: `{"ok": true, "checked_templates": 0}`.

### Evidence

- `task_000264` final_pytest: `test_csv_output` AssertionError
  `'Alice (CEO),12' != 'Alice (CEO),11'`; `test_query_plan_output`
  AssertionError `'USING INDEX' not in ...USING COVERING INDEX...`.
- `task_000264` messages step ~4: base case `SELECT e.id as manager_id,
  e.id as subordinate_id` (counts self); step ~14 EXPLAIN QUERY PLAN output
  shows `USING COVERING INDEX idx_employees_manager_id`.
- Cross-task scan: no second failing task reproduces this counting-semantics
  or query-plan-substring mechanism.

### Uncertainty

If a future round finds a real cluster of "agent ships plausible-but-wrong
computed output after a superficial self-verify", the general lever would be a
stronger recomputation discipline in the self-verify checklist — but that needs
≥2 tasks whose independent-recheck retroactive test returns `yes`, which this
round did not have. Skipped per capability-gap rule.

NEEDS_FROM_HUMAN: task_000264 requires (a) correct recursive-CTE subordinate
semantics and (b) tolerance of `USING COVERING INDEX` in the verifier — both
outside harness control; no harness fix — skip.


## Round 1 (c5) — warn-only loop nudge, regression-safe calibration

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-29T00:00:00Z
hypothesis_id: h_loop_detection_v2
levers: [configuration]
predicted_affected: [task_000313_1dce9844, task_001098_f5acdd79, task_001979_a1e24b6f, task_001032_1adaccb9]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Reclaims wasted compute from the non-recovering byte-identical tool-call loop cluster (>=3 tasks, 33-42 repeats ending in budget_exceeded/error) via a clean loop_detected exit"
regression_risk: "A task legitimately issuing >=30 byte-identical calls in a row would be cut short; the only observed recover-after-loop case (task_000936, PASSED) peaks at 26 identical calls, safely under threshold=30"
cost_shift: "Net negative (lower) - extreme loops exit ~30-70 steps earlier; one processor adds negligible per-call hashing overhead"
rollback_trigger: "Next-round pass_rate drops OR any loop_detected exit appears on a task that previously passed (especially task_000936_2a78f3ca)"
retry_rationale: "R1 c-batch sibling proposed h_loop_detection_v1 raise-at-5; NEW evidence this focus surfaced - task_000936 PASSED after 26 identical consecutive tool calls, so any raise <=26 regresses it. This is a different shape (warn-only escalation, threshold above the recovery ceiling, Strategy 2 off) at the same lever."
-->

### Why

Assigned focus task_000118_3043e92d (write a log-quota monitor daemon) failed
reward=0 exit=done at 48 steps. Root cause verified at step 14: the agent's own
test printed "Monitor PID: 91 / All worker processes have completed. Exiting. /
Starting deployment..." - its worker-check + break sits at the TOP of the loop,
so when the verifier starts the monitor before the deployment (start monitor,
sleep 0.5s, start deploy) the monitor exits immediately, never monitors, and 20
logs reach 10 MB each = 209 MB, far over the 45 MB threshold. The agent
CORRECTLY diagnosed this in natural language (steps 37/39/79/95) but could not
implement the fix - it fell into a byte-identical rewrite loop (6 consecutive
identical file writes), and the existing EditDetection WARN at step 62 was
ignored. task_000118's terminal blocker is thus a MODEL CAPABILITY GAP (cannot
turn a correct diagnosis into a working daemon with a startup grace period), not
harness-fixable without task-specific knowledge injection.

The systemic harness deficiency the failure exposes is real: a cross-task
cluster loops on byte-identical tool calls with NO clean-exit escape. Measured
max-consecutive-identical runs: task_000313=33 (budget_exceeded),
task_001098=33 (error), task_001979=42 (error), task_001032=13 (budget_exceeded),
task_000118=6. All already reward=0. The fix reclaims their wasted budget.

### Changes

- config.yaml - wire the existing-but-unwired LoopDetectionProcessor in
  warn-only escalating mode: warn_threshold=4, threshold=30 (raise to clean
  loop_detected exit), name_warn_threshold=999 (Strategy 2 disabled since a
  Bash-only agent issues many legit consecutive Bash calls), window_size=50
  (greater than threshold so the consecutive-run tail can reach 30),
  compaction_drop_threshold=5.

### Evidence

- task_000118 step 14 tool result: monitor exits before workers start; logs
  listing shows all 20 x 10 MB files = 209 MB (verifier threshold 45 MB).
- task_000118 steps 57,61,65,69,73,77: 6 byte-identical file writes in a row;
  step 62 EditDetection warn ignored.
- task_000313: 33 byte-identical consecutive tool calls, 0 truncation markers,
  budget_exceeded (pure non-recovering loop).
- task_000936_2a78f3ca (REGRESSION probe, reward=1): 26 byte-identical
  "cd /home/user/pipeline && ls -la" at steps 2-52, recovered at step 55,
  PASSED. threshold=30 leaves it untouched.

### Uncertainty

Honest: this does NOT flip the focus task_000118 (capability gap) - its value is
budget reclamation on the loop cluster (all already failing, near-zero
regression) plus a marginal early-nudge chance. If a legitimate 30+ identical-
repeat task exists in the eval, threshold=30 could cut it short; the observed
recovery ceiling is 26, giving a 4-repeat margin. Watched via rollback_trigger.


## Round 2 — two-sided service lifecycle reminder

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-29T00:00:00Z
hypothesis_id: h_service_lifecycle_reminder_v1
levers: [control]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the lingering-process task and corrects a one-sided self-verify bias (keep services alive) across ~14 service/server tasks so any clean-teardown criterion is no longer silently steered against."
regression_risk: "Low: reminder is two-sided and additive - for tasks needing the service alive it explicitly says confirm it is still listening, so it cannot push those toward wrongly killing a needed service. Fires <=1x/task, only on explicit background-launch Bash patterns, mutates one tool-result string, adds no messages."
cost_shift: "Negligible: one appended reminder string on a single armed tool result per task; may prompt 1-2 short verification calls. No forced extra model turns."
rollback_trigger: "Revert if any previously-passing service/server task regresses to F attributable to the reminder (agent kills a service the verifier needed alive), or if the reminder fires on non-service commands."
-->

### Why

Assigned focus task_000140_01c78b42 is a service-lifecycle task (fix a Go HTTP
service, fix its init script, author a CI/CD start->exercise->graceful-stop
pipeline). The agent solution was functionally correct - the log held the
expected provisioning line and 2/3 verifier tests passed - but
test_no_lingering_service_processes failed: pgrep found stray vm_service PIDs
[348, 610, 808]. The agent started the service (own testing + pipeline) and left
processes alive at exit. The stock TB2 self-verify checklist (read-only) is
one-sided: item 5 only ever nudges "confirm services are still alive and
reachable", never "tear down what you started". That steering is actively wrong
for lifecycle/CI/init-script tasks whose verifier requires a clean teardown.
This is a harness mechanism deficiency, not purely a model gap.

### Changes

- processors/service_lifecycle_reminder.py - new ServiceLifecycleReminder
  MultiHookProcessor. Arms on the first Bash call whose command matches an
  explicit background-launch pattern (trailing background operator, nohup,
  disown, setsid, systemctl/service start, uvicorn/gunicorn/flask/http.server,
  start_service, or a listen-bind pattern combined with trailing background).
  On that command on_after_tool, appends a two-sided lifecycle reminder to the
  result string and disarms (fires <=1x/task). Contract-safe: mutates only the
  tool-result string (same shape as CustomEditToolProcessor), no message
  insertion.
- config.yaml - register ServiceLifecycleReminder via absolute file:// path,
  _order=35 (after CustomEditToolProcessor 30, before CustomSelfVerifyProcessor
  90).

### Evidence

- task_000140 result.json: initial_pytest.passed=true; final_pytest
  AssertionError lingering vm_service processes [348, 610, 808]; reward=0.
- task_000140 messages.json: the init script backgrounds the service and records
  its pid; the pipeline ends with a SIGTERM kill but never waits or checks for
  strays; after self-verify fired the agent re-listed/cat-ed files but issued NO
  process check or teardown.
- Read-only harness self-verify item 5 ("confirm they are still alive and
  reachable right now") is the one-sided bias the reminder counters.

### Uncertainty

Two ways to know the bet is wrong: (1) task_000140 stays F even though the
reminder fired - then the agent authored teardown script is itself broken
(model solution-quality gap), logged as capability not harness; (2) a
previously-passing service task where the verifier needs the service alive
regresses to F because the agent over-eagerly killed it - the rollback trigger,
though the "confirm still listening" branch is designed to prevent exactly that.
Only 1 literal lingering-process task exists in R0, so attribution is narrow;
the broader value is de-biasing self-verify for the service-task cluster.

## Round 1 — verify-computed-results prompt

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-29T00:00:00Z
hypothesis_id: h_numeric_crosscheck_v1
levers: [instruction]
predicted_affected: [task_000111_cbada64a, task_001330_f5aff1f5, task_001653_c4cafa73, task_001937_ac874115, task_001035_26564093]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the recurring done-but-numerically-wrong cluster (scientific_computing / data_science compute tasks); at least five tasks this round commit a self-consistent but wrong value after format-only self-verification"
regression_risk: "Extra verification tool-calls near exit on compute tasks; on already-passing compute tasks it is a confirmatory no-op (recompute agrees, exit). Small chance of pushing a near-budget task over, but the cited cluster all finished within 15 steps with headroom"
cost_shift: "Plus one to four tool calls on compute-heavy tasks that reach exit; negligible on non-compute tasks (guidance scoped to when a computed/numeric result is produced). Net positive: recovered passes outweigh extra calls"
rollback_trigger: "Next round pass_rate flat/down on the compute cluster OR previously-passing short compute tasks regress to budget_exceeded/max_steps from the added verification steps"
-->

### Why

Assigned focus `task_000111_cbada64a` (C++ OLS + bootstrap CI) exited
`done` at only 7 steps with reward 0: the committed slope m=2.5056 vs
expected 2.5997 -- a deterministic OLS, so a second correct computation on
the same file would necessarily disagree, exposing a read/precision bug the
agent never looked for. Its entire "verification" was a requirements
checklist plus "this is in the format ... as required". Sweeping the round
found the same mechanism recurring across scientific_computing / data_science
compute tasks: the agent writes one plausible implementation, runs it once,
declares the number "physically sensible" or format-correct, and exits --
never independently recomputing the value or probing the spec ambiguities
(seed draw-order, indexing, search boundaries) that produced the wrong answer.
The capability is fully present (Bash to Python/numpy/C++); the missing thing
is the discipline to cross-validate before committing -- an Instruction gap,
not a mechanism the harness can supply without embedding per-task answers.

### Changes

- `config.yaml` -- swap `SiblingSystemPromptBuilder` for
  `StaticTmaxSystemPromptBuilder` with an inline prompt that keeps the original
  baseline text and adds a "Verifying computed results before you finish"
  strategy section: (1) cross-validate the key result with an independent
  method, (2) surface and test spec ambiguities (indexing, seed draw-order,
  regressor axis, rounding, off-by-one, precision), (3) sanity-check input
  record counts / intermediate ranges. Scoped to computational tasks; no
  task-specific literals, constants, or code.

### Evidence

- `task_000111_cbada64a` result.json: reward 0, exit_reason done, steps 7;
  final_pytest: `Expected m to be approx 2.5997, got 2.5056`.
  Body: verification = "the values make sense ... This is in the format".
- `task_001330_f5aff1f5` body final turn: "The results make physical sense";
  committed m=0.048 -- Monte-Carlo answer is sensitive to per-iteration
  `np.random.normal` draw order, an ambiguity never surfaced.
- `task_001653_c4cafa73` final_pytest: `Centroid 36.3687,... Distance 18.6199`
  vs expected `42.0095,... 11.3444`.
- `task_001937_ac874115` final_pytest: `Optimal Grid 60` vs expected `50`.
- `task_001035_26564093` final_pytest: optimal primer `GCCT` vs `GCAT`.

### Uncertainty

If the added verification step over-runs on compute-heavy tasks it could push
a near-budget task to max_steps; the cited cluster all had headroom (within 15
steps) so risk is low. If cross-checking a correct answer produces spurious
"disagreement" churn (floating-point noise misread as a bug), that would
inflate cost without flipping passes -- watch via the rollback trigger.

## Round 2 (c3) — ensure requests for verifier (broadened arming)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-29T00:00:00Z
hypothesis_id: h_verifier_dep_requests_v2
levers: [control]
predicted_affected: [task_000106_23215092, task_000028_7fe033ac, task_000958_4bb2b05d, task_001857_24daeef3, task_002063_8c8adcfe]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Unblocks a 5-task HTTP-service cluster (multiple domains) whose verifier pytest aborts at collection on `import requests` — currently guaranteed reward=0 for a purely infrastructural reason, independent of solution quality."
regression_risk: "Very low: guard is `|| true`-terminated and runs the original command unchanged after `;`; no-op when requests already imports; fires <=1x/task, only on armed network/service tasks. Offline -> pip fails silently -> status quo, no new failure."
cost_shift: "Negligible: one fast import probe (+ at most one quiet pip install) prepended to a single Bash call on armed tasks only; no extra model turns."
rollback_trigger: "Revert if any previously-passing network/service task regresses to F attributable to the prepended guard, or replay fails on the guard."
-->

### Why

Assigned focus task_000106_23215092 (data_querying) built a correct co-authorship
graph API on 127.0.0.1:8000, started it, and verified /author/1, /author/2, and
/author/999->404 all via `urllib.request` (messages steps 50/52/54/58/70) — a
functionally complete solution. It still scored reward=0 for a purely verifier
reason: the injected `test_final_state.py` begins `import requests`, the base
image lacks `requests`, so pytest aborts at *collection*
(`Interrupted: 1 error during collection`) and all four assertions error at once
regardless of correctness. The agent had no way to anticipate this — the test
file does not exist during the agent phase, and testing with urllib/curl is
valid. `grep "No module named" *.result.json` returns exactly 5 tasks
(task_000106, task_000028, task_000958, task_001857, task_002063), every one an
HTTP API/service task with the identical `'requests'` collection abort, none of
which installed requests. This is a harness deficiency, not a capability gap.

Note: R1's h_verifier_dep_requests_v1 targeted the same cluster and was
accepted, but the `current_config` for this proposal is R0 (no ensurer), so the
focus task is still broken under the config I evolve. This round re-establishes
the mechanism and broadens the arming predicate (OR instead of AND of
service/listen surfaces; adds /api, /author, 127.0.0.1, PUT/DELETE routes,
serve/serving) so no collection-abort task slips through the narrower AND gate.

### Changes

- processors/verifier_dep_ensurer.py — new VerifierDepEnsurer MultiHookProcessor.
  `on_task_start` arms tasks whose description names an HTTP/REST/API/server/
  service keyword OR a concrete listen surface (endpoint/port/listen/route/host).
  On the first substantive approved Bash call of an armed task, prefixes the
  command with an idempotent, silent, best-effort guard:
  `python3 -c 'import requests' 2>/dev/null || pip install -q requests >/dev/null 2>&1 || true ; <original>`.
  Fires <=1x/task; never mutates message history (contract-clean, dry-fire clean).
- config.yaml — register VerifierDepEnsurer via absolute file:// path,
  placed after ToolCallCorrectionLayer and before TaskTimeReminderProcessor
  (its _order=10 makes it run early among before-tool processors).

### Evidence

- task_000106 result.json: initial_pytest.passed=true; final_pytest
  passed=false rc=2, tail `import requests` -> `ModuleNotFoundError` ->
  `Interrupted: 1 error during collection`.
- task_000106 messages steps 27-70: Flask API on 127.0.0.1:8000; all endpoints
  correct via urllib; step 33-34 `pip3 install numpy` downloads a 16.8 MB wheel
  successfully (pip works in-run); requests never installed.
- task_000028/task_000958/task_001857/task_002063 result.json: identical
  `import requests` collection abort; each an HTTP API/service task.

### Uncertainty

Two ways to tell the bet is wrong: (1) the 5 tasks don't flip because their
solutions were also wrong (collection now runs but assertions fail) — the guard
did its job and the remainder is a capability gap to log; (2) an offline image
makes pip fail — guard is `|| true`, so status quo, no new failure. Regression
on a previously-passing service task attributable to the guard is the rollback
trigger.

## Round 3 — low-DPI OCR quality advisor

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-29T02:00:00Z
hypothesis_id: h_ocr_lowdpi_advisor_v1
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000505_50b5162d]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=17/50; +3/-3 gained=task_000313_1dce9844,task_000760_e197f7ff,task_001832_dd672877 lost=task_000011_d089ef35,task_000187_30465ebf,task_000477_de422e4d; score 0.3400 >= incumbent(mean) 0.3700 - tol 0.0400
expected_global_gain: "Unblocks the OCR-input cluster (>=2 distinct-domain tasks) whose failures are caused by garbled tesseract output on small/no-DPI images, not by reasoning errors. Generalizes to any unseen image-to-text task; the injected recipe (upscale + explicit --dpi + binarize + cross-check) is standard tesseract practice with zero task-specific literals."
regression_risk: "Very low: fires <=1x/task and ONLY when a real tesseract/pytesseract Bash call emits the low-resolution signal, so the ~47 non-OCR tasks never see it. Appends only to a tool-result string (same contract as CustomEditToolProcessor); no message insert/drop/reorder. contract=0 violations."
cost_shift: "Negligible-to-slightly-positive on OCR tasks (may prompt 1-2 corrective re-runs, replacing wasted near-identical retries); exactly zero on non-OCR tasks. No forced extra model turns."
rollback_trigger: "Revert if any previously-passing task regresses to F attributable to the advisory, or if synthetic replay fails on the OcrQualityAdvisor processor."
-->

### Why

Assigned focus task_000015_89886d8d (build a URL-migration utility from a
schema image) failed reward=0 at 41 steps exit=done: the verifier ran the
agent migrate.py over 2000 hidden URLs and required accuracy above the 0.98
bar; the agent got 0.6664. Root cause is upstream of the code: tesseract on
/app/routing_schema.png produced garbled text (atalogyitem, depariment, Esort,
trafic source, feccount) because the image carried no DPI metadata — tesseract
own stderr flagged `Invalid resolution 0 dpi. Using 70 instead. Estimating
resolution as 111`. The agent then inferred a plausible-but-wrong ROUTES table
(collapsing category and department into one dept group, guessing key names)
and shipped it. Across steps 9-27 the agent re-ran contrast/threshold/sharpen/
PSM/OEM/whitelist variants on the ORIGINAL 800x400 resolution but NEVER
upscaled the image or set an explicit --dpi — the two highest-yield fixes for
small/low-DPI OCR. A cross-task scan found the SAME root cause on
task_000505_50b5162d (security): tesseract on /app/evidence.png hit the
identical Invalid-resolution warning and misread the base64 SSH key (I/l/1 and
5/S confusions, spurious spaces), so the agent exact-string trojan detector
missed every adversarial sample (2 of 2 evil bypassed). Two distinct domains,
one harness-fixable mechanism: the agent trusts a low-quality first OCR read
and never reaches for the standard preprocessing that would fix it.

### Changes

- processors/ocr_quality_advisor.py — new OcrQualityAdvisor MultiHookProcessor.
  on_before_tool records Bash calls invoking tesseract/pytesseract/
  image_to_string; on_after_tool, if that call result carries tesseract own
  low-resolution signal (Invalid resolution / Estimating resolution / Using N
  instead), appends a ONE-TIME generic advisory: upscale the image ~3-4x, pass
  explicit --dpi 300, grayscale+binarize with --psm 6, then diff the re-run and
  disambiguate ambiguous glyphs (I/l/1, 0/O, 5/S, 8/B, rn/m, spaces), verify
  against any provided sample before committing downstream code. Fires <=1x/
  task, only on real OCR calls with the low-res signal. Contract-safe: mutates
  only the tool-result string, no message insertion. _order=32 (after
  CustomEditToolProcessor 30, before CustomSelfVerifyProcessor 90).
- config.yaml — register OcrQualityAdvisor via absolute file:// path; also
  wrote the required sibling system_prompt.txt (byte-identical to R0 baseline,
  read by SiblingSystemPromptBuilder).

### Evidence

- task_000015 result.json: initial_pytest passed; final_pytest accuracy 0.6664
  below the 0.98 threshold; reward=0.
- task_000015 messages: step 2 garbled OCR; steps 9-27 repeated preprocessing
  with NO upscale/--dpi; step 31 committed inferred ROUTES table.
- task_000505 result.json: final_pytest `2 of 2 evil bypassed`; reward=0.
  messages step 3: Invalid resolution 0 dpi + misread SSH key.
- Validators: canonicalize ok (0 templates), dry_fire likely_bugs=0, contract
  violations=0, literals findings=0.

### Uncertainty

Two ways to know the bet is wrong: (1) the predicted tasks do not flip even
though the advisory fired — then either the agent still did not act on the
recipe (a following-instructions gap) or a clean OCR read alone is insufficient
(e.g. task_000015 also has a genuine schema-interpretation ambiguity beyond the
garble), which reclassifies the residual as a capability gap to log; (2) an OCR
task where the first read was already correct gets the advisory and the agent
chases it into a worse read — the rollback trigger. The signal is narrow
(tesseract own DPI warning), so non-OCR regression risk is essentially nil.


## Round 2 (c2) — ensure requests for verifier (v2, broadened arming)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-29T02:00:00Z
hypothesis_id: h_verifier_dep_requests_v2
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000106_23215092, task_000958_4bb2b05d, task_001857_24daeef3, task_002063_8c8adcfe]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Unblocks a 5-task HTTP-service cluster (5 domains) whose verifier pytest aborts at collection on import requests -- currently guaranteed reward=0 for a purely infrastructural reason the agent cannot anticipate."
regression_risk: "Very low: guard is || true-terminated and runs the original command unchanged after ;; no-op when requests already imports; fires <=1x/task, only on armed HTTP-service tasks. The 3 armed already-passing tasks (task_000011/000683/001382) are untouched. If pip is ever blocked the guard degrades to today state (no new failure)."
cost_shift: "Negligible: one import probe (+ at most one quiet pip install of a small pure-Python wheel) prepended to a single Bash call on armed tasks only; no extra model turns."
rollback_trigger: "Revert if any previously-passing HTTP-service task (esp. task_000011/000683/001382) regresses to F attributable to the prepended guard, or replay fails on the guard."
retry_rationale: "R1-c3 proposed h_verifier_dep_requests_v1 (same lever/mechanism) but the R1 batch promoted the LoopDetection candidate (c5) instead, so NO requests fix ever landed in the R0 lineage I edit from. v1 was accepted-not-reverted, so novelty permits a v2. NEW evidence: the v1 SERVICE regex failed to arm task_001857 (a multi-protocol service that aborts on the identical import requests error) -- v2 broadens the detector so all 5 collection-abort tasks arm."
-->

### Why

Assigned focus task_000028_7fe033ac (Nginx reverse proxy + C++ HTTP socket
backend on 127.0.0.1:8080) built a correct solution and exited done at 23 steps
with initial_pytest passed, yet scored reward=0. The failure is entirely in the
verifier phase: the injected test_final_state.py begins with import requests,
the base image lacks requests, so pytest aborts at collection
(ModuleNotFoundError: No module named requests -> Interrupted: 1 error during
collection) and every test errors at once regardless of solution correctness.
The agent cannot anticipate this -- the test file does not exist during the
agent phase (TB2 sandbox topology) and testing with urllib/curl is valid. A
scan of all 50 result.json shows the identical requests collection abort on 5
tasks -- task_000028, task_000106, task_000958, task_001857, task_002063 --
every one an HTTP server / microservice task, none of which used or installed
requests. Harness deficiency, not a model capability gap. This mechanism was
proposed in R1-c3 but never promoted (the R1 batch promoted LoopDetection
instead), so the current R0-lineage config still has no fix for this cluster.

### Changes

- processors/verifier_dep_ensurer.py -- VerifierDepEnsurer MultiHookProcessor.
  on_task_start arms tasks whose description names a network SERVICE surface AND
  a concrete LISTEN surface (endpoint/port/HTTP verb/response). On the first
  approved Bash call of an armed task, prefixes the command with an idempotent,
  silent, best-effort import-requests-or-pip-install guard that is || true
  terminated (original command runs unchanged after the ;). Fires <=1x/task;
  only rewrites tool_input (never mutates message history). v2 broadens the
  SERVICE regex over the R1-c3 draft so task_001857 (a multi-protocol service)
  arms.
- config.yaml -- R0 config + register VerifierDepEnsurer via absolute file://
  path, placed after TaskTimeReminderProcessor and before length-recovery;
  _order=10 runs it early among before-tool processors.

### Evidence

- task_000028_7fe033ac result.json: initial_pytest.passed=true; final_pytest
  passed=false rc=2; output_tail shows import requests ->
  ModuleNotFoundError: No module named requests -> Interrupted: 1 error during
  collection. Transcript: agent never used or installed requests.
- task_000106/000958/001857/002063 result.json: identical requests collection
  abort; each is an HTTP service with a listen surface; none installed requests.
- task_000106 messages step ~34/44: pip3 install numpy -> Successfully installed
  numpy-2.2.6 (16.8 MB), scipy-1.15.3 -- proves runtime pip works in this eval.
- Arming precision: across 50 tasks the detector arms 13 -- 5 requests-collection
  beneficiaries, 5 other-cause failures (guard is a no-op), 3 already-passing
  (guard short-circuits, behavior unchanged). Zero regression risk on passing.
- Validation: canonicalize ok (checked_templates 0); dry_fire likely_bugs 0;
  contract violations 0; literals findings 0.

### Uncertainty

Two ways the bet is wrong next round: (1) the 5 predicted tasks do not flip even
though collection now runs -- then the agents solutions were also wrong
(assertions fail), the guard did its job, and the residual gap is model
capability to log separately; (2) an offline image makes pip install fail -- the
guard is || true, so the task is exactly as today (no new failure). Regression
on a previously-passing HTTP-service task attributable to the guard is the
rollback trigger.

## Round 2 (c0) — cumulative-truncation loop breaker

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-29T00:00:00Z
hypothesis_id: h_cumulative_length_trunc_recovery_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_001032_1adaccb9, task_000939_1592be48, task_001857_24daeef3]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Reclaims the ~50% of budget spent on repeated full-4096-token re-narration across the chronic length-truncation cluster (>=4 tasks this round) and stops collapsed 'I'm stuck' narration from re-priming the loop; plausibly flips the budget_exceeded members (task_000010, task_001857) whose only remaining blocker was running out of budget before writing the required output file."
regression_risk: "Low: never force-exits, never removes messages (contract-clean). Only passing task that truncates a lot (task_001832, 7 truncations) front-loads them then produces 40+ productive msgs; change only shrinks its late collapsed turns and may show it a benign 'emit one command' directive. task_000011 (3 truncations) stays under chronic_threshold=4."
cost_shift: "Net negative (lower): chronic-loop tasks stop emitting repeated 4096-token re-narration turns; stub collapse shrinks forwarded context. No forced extra model turns."
rollback_trigger: "Revert if next-round pass_rate drops, OR task_001832/task_000011 (or any previously-passing truncating task) regresses to F attributable to the escalated directive, OR replay fails on the processor."
-->

### Why

Assigned focus task_000010_644ab1c2 (write /home/user/operator.py: backup +
socat port-forward 9090->8080 + pexpect CLI automation) died budget_exceeded at
80 steps and never created /home/user/operator.py (final_pytest:
test_operator_script_exists AssertionError "... does not exist"; it wrote the
script to the WRONG path /home/user/k8s_operator.py). The proximate stall was a
semantic misread of raw /proc/net/tcp hex (the agent kept reading its own socat
listener as "port in use"), but the harness-shaped deficiency is the truncation
loop that consumed the budget: 11 finish_reason=length truncations spread across
the whole run (msg indices 3,5,24,28,...,65). The stock
LengthTruncationRecoveryProcessor collapses each runaway turn (11 collapse
markers, each 1964 chars) and nudges, but its hard-escalation is keyed on the
*consecutive* truncation run — and the pattern here is *alternating*
truncate->nudge->one-command->truncate (TCTCTC), so the consecutive counter
resets to <=2 every time and the hard nudge never fires. Meanwhile every
collapsed "I've been stuck in a loop trying to free port 9090" turn stays in
context and re-primes the next runaway generation. Same shape recurs on
task_001032 (27 truncations), task_000939 (10), task_001857 (5). This is a
Control-lever mechanism gap, not a knob and not a prompt rule (the corrective
nudge already reaches the model each turn and is ignored).

### Changes

- processors/length_recovery_cumulative.py — new
  CumulativeLengthTruncationRecovery MultiHookProcessor. Same _singleton_group
  ("tmax_length_recovery") and _order (5) as the stock processor so it replaces
  it. Tracks CUMULATIVE truncations per task (not just consecutive): a tool call
  resets the consecutive run but not the cumulative count. Once cumulative >=
  chronic_threshold (default 4) it (a) escalates to a terminal "STOP narrating,
  ONE minimal command, fix the required output PATH" directive and (b) collapses
  the offending assistant turn to a short stub instead of head+tail so the
  repeated narration stops re-priming the loop. Below chronic it behaves like the
  stock processor (head+tail collapse + escalating first/repeat nudge).
  Contract-safe: on_after_model rewrites only its own event content;
  on_before_model rewrites only the trailing user message (net length 0).
- config.yaml — swap the LengthTruncationRecoveryProcessor entry for the new
  processor via absolute file:// path (repeat_threshold=2, chronic_threshold=4,
  head_chars=1200, tail_chars=600). Everything else byte-identical to R0.

### Evidence

- task_000010 messages.json: collapse marker count = 11; truncation user-msgs at
  indices [3,5,24,28,32,36,40,44,48,52,65]; assistant turns 2/4/23/... each 1964
  chars re-narrating "stuck in a loop ... free port 9090"; final operator.py
  absent; exit budget_exceeded at step 80.
- task_001032 truncation indices [7,11,19,...,96,98] (27); task_000939
  [38,40,44,...,72] (10); task_001857 5 truncations, budget_exceeded.
- Regression probe: task_001832 (PASS) truncations [9,11,29,31,35,39,66] —
  front-loaded, then 40+ productive messages to index 88; task_000011 (PASS)
  only 3 truncations (< chronic_threshold).
- Validators: canonicalize {"ok": true, "checked_templates": 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Two ways to know the bet is wrong: (1) task_000010/task_001857 stay F even though
truncation-budget was reclaimed — then the remaining blocker is model capability
(semantic /proc/net/tcp misread; wrong output path chosen), logged as capability
not harness; (2) a previously-passing truncating task (task_001832, task_000011)
regresses attributable to the chronic directive — the rollback trigger, though
the directive only says "emit one command" and never force-exits, so this is
unlikely. Honest: the change is primarily budget reclamation + de-priming with a
plausible-but-not-guaranteed flip on the budget_exceeded members.

## Round 2 (c7) — no-op: task_000264 dual failure both outside harness remit

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-29T04:00:00Z
hypothesis_id: h_noop_task_000264_sql_dualfail
levers: []
predicted_affected: []
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "None claimed. Assigned task_000264 fails on two independent, both-required grounds - a model recursion-semantics gap and a single-task verifier substring artifact - neither harness-fixable and neither part of a multi-task cluster."
regression_risk: "None - byte-for-byte copy of R0 config."
cost_shift: "Zero - no config change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus task_000264_ab8c7253 (data_querying). Independent diagnosis
(this proposal's config lineage is R0) reproduces the earlier R2 c-batch
finding: the task fails on two independent, both-must-pass grounds, both
outside harness control.

1. CSV off-by-one is a MODEL REASONING GAP. Agent's recursive-CTE base case
   `SELECT e.id AS manager_id, e.id AS subordinate_id` counts each node as its
   own subordinate. Output `Alice,12 / Bob,5 / Charlie,5 / David,4 / Grace,4`
   vs oracle `Alice,11 / Bob,5 / Charlie,5 / David,4 / Grace,3`. The delta is
   NOT uniform -1 (Bob/Charlie/David match at 5/5/4), so the oracle's exact
   "subordinate" semantics are subtle and the model modeled the recursion
   wrong. No generalizable harness mechanism can inject the correct semantics
   without hardcoding this task's answer.

2. `USING INDEX` verifier brittleness is a VERIFIER ARTIFACT. The agent did
   everything correctly - created `idx_employees_manager_id`, verified the
   plan changed to `SCAN e USING COVERING INDEX idx_employees_manager_id`.
   The verifier greps the literal substring `"USING INDEX"`; SQLite emitted
   `USING COVERING INDEX`, which does not contain it. The index IS created and
   used. The test file is invisible during the agent phase (TB2 topology), so
   the agent cannot adapt. Single-task only.

Because both tests must pass and Failure 1 is an unfixable reasoning gap, NO
legitimate harness change can flip this task. A cross-task scan of all 50
result.json shows `USING INDEX`/`COVERING INDEX` on ONLY task_000264 - the
query-plan-substring mechanism has no cluster (idiosyncratic-filter of two-or-
more tasks not met). Considered and rejected a generic "verify computed results
independently" prompt: its retroactive check fails (recomputing with the same
wrong "subordinate" mental model would not catch the off-by-one), it risks cost
inflation / regressions across the 19 passing tasks, and it duplicates a live
sibling bet (h_numeric_crosscheck_v1) - re-proposing would drift onto another
proposal's territory, which the brief forbids.

### Changes

- config.yaml - byte-for-byte copy of R0 config (explicit no-op).
  Canonicalizes: `{"ok": true, "checked_templates": 0}`.

### Evidence

- task_000264 final_pytest: `test_csv_output` AssertionError
  `'Alice (CEO),12' != 'Alice (CEO),11'`; `test_query_plan_output`
  `assert 'USING INDEX' in '...SCAN E USING COVERING INDEX IDX_EMPLOYEES_MANAGER...'`.
- task_000264 messages step 4 (assistant): base case `SELECT e.id as manager_id,
  e.id as subordinate_id` (counts self). Later tool result: optimized plan
  `SCAN e USING COVERING INDEX idx_employees_manager_id` (index created & used).
- Cross-task scan: grep of USING INDEX / COVERING INDEX over 50 result.json
  returns only task_000264; no second task shares either mechanism.

### Uncertainty

If a future round surfaces a real multi-task cluster of "agent ships
plausible-but-wrong computed output after a superficial file-existence
self-verify" whose independent-recheck retroactive test returns yes, the
general lever would be a stronger recomputation discipline - but that needs a
retroactive check this focus fails and a cluster this round lacks. Skipped per
capability-gap rule.

NEEDS_FROM_HUMAN: task_000264 requires (a) the exact oracle recursive-CTE
"subordinate" counting semantics (model reasoning gap) and (b) verifier
tolerance of `USING COVERING INDEX` where it demands literal `USING INDEX`
(verifier-brittleness artifact) - both outside harness control; no harness
fix - skip.

## Round 2 (c6) — two-sided self-verify teardown item

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-29T04:00:00Z
hypothesis_id: h_self_verify_teardown_balance_v1
levers: [control]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the lingering-process task and de-biases the once-per-task self-verify checkpoint (currently keep-alive-only) so the whole service/lifecycle/CI cluster is no longer silently steered against any clean-teardown criterion; generalizes to unseen lifecycle tasks with zero task-specific literals."
regression_risk: "Low and two-sided: the added item first tells the agent to KEEP a service alive when the verifier connects to it (mirrors the existing correct nudge), so it cannot push keep-alive tasks toward wrongly killing a needed service. Edits only the last user message and only when it is the self-verify checklist (sentinel-gated), inserts no message (contract-clean), fires <=1x/task."
cost_shift: "Negligible: ~120 tokens appended to a single already-injected checklist message on tasks that reach the exit checkpoint; may prompt 1-2 short teardown/keep-alive verification Bash calls on genuine lifecycle tasks. Zero on tasks that never self-verify."
rollback_trigger: "Revert if any previously-passing service/server task regresses to F attributable to the agent killing a service the verifier needed alive, or if synthetic replay fails on SelfVerifyTeardownBalance."
-->

### Why

Assigned focus task_000140_01c78b42 (Go provisioning service: fix main.go, fix
the init/start_service.sh supervisor, author a CI/CD test_pipeline.sh that
starts -> exercises -> gracefully stops the service). The solution was
functionally correct: initial_pytest passed, 2/3 final tests pass, and the log
held the expected provisioning line. Only test_no_lingering_service_processes
failed: pgrep found stray vm_service PIDs (['340','592'] / ['340','592','787']).
The agent ran test_pipeline.sh during its own agent phase (step 15, curl 200)
which starts a vm_service; the pipeline's `kill -TERM $PID` is issued without a
wait/re-check, and after the stock self-verify fired (step 21) the agent
re-read the task and re-ls'd files but issued NO process check and NO teardown,
exiting with strays alive. Root harness deficiency: the stock self-verify
checklist (read-only harness.py `_SELF_VERIFY_MSG` item 5, and system-prompt
line 381) is one-sided — it only ever nudges "confirm services are still alive
and reachable", never "tear down what you started". That steering is actively
wrong for lifecycle/init-script/CI tasks whose verifier requires a clean
shutdown. This is a harness mechanism deficiency, not a pure model gap: the
agent has full Bash pgrep/pkill/wait capability but is steered away from using
it at exit.

### Changes

- processors/self_verify_teardown_balance.py — new SelfVerifyTeardownBalance
  MultiHookProcessor. Hooks on_before_model at _order=91 (after
  CustomSelfVerifyProcessor's 90). When the last message is the self-verify
  checklist (gated on the stable sentinel "run through this checklist"), it
  appends a two-sided end-state item to that same last-user message content
  (the sole message mutation on_before_model permits for last=user), idempotent
  via an inserted marker. The item: decide whether the verifier expects the
  service alive (confirm still listening) or torn down (stop AND verify none
  survive: list matching processes, re-check after a stop signal). No task ids,
  ports, process names, or paths. Fires <=1x/task, only when the checklist is
  present.
- config.yaml — R0 config + register SelfVerifyTeardownBalance via absolute
  file:// path, placed last (after CustomSelfVerifyProcessor).

### Evidence

- task_000140 result.json: initial_pytest.passed=true; final_pytest fails ONLY
  test_no_lingering_service_processes with `Lingering vm_service processes
  found: ['340','592']` / `['340','592','787']`; other 2 tests pass; reward=0.
- task_000140 message 0: "**Construct a CI/CD Test Pipeline** … it must
  **gracefully stop** the Go service by reading /home/user/service.pid and
  sending a SIGTERM." — explicit teardown criterion.
- task_000140 step 15: agent runs test_pipeline.sh in the agent phase (curl
  200), starting a vm_service; step 21 self-verify fired; steps 22-24 the agent
  re-verified files but ran NO process check / teardown.
- harness.py:96 (`_SELF_VERIFY_MSG` item 5) and harness.py:381 confirm the
  read-only checklist/system prompt are keep-alive-only.
- Validators: canonicalize ok (0 templates); dry_fire likely_bugs=0; contract
  violations=0; literals findings=0.

### Uncertainty

Two ways to know the bet is wrong: (1) task_000140 stays F even though the
augmented checklist fired — then the residual is a model solution-quality gap
(agent read the two-sided item but still failed to reap), logged as capability;
(2) a previously-passing service task where the verifier needs the service
alive regresses because the agent over-eagerly kills it — the rollback trigger,
though the item's explicit "must stay up -> confirm still listening" branch is
designed to prevent exactly that. Only 1 literal lingering-process task exists
in R0, so the measurable corrective flip is narrow (1); the broader, durable
value is de-biasing the universal self-verify checkpoint for the service/
lifecycle cluster. This differs from the batch sibling's launch-regex-triggered
reminder by attaching to the exit checkpoint itself, so it fires regardless of
how the process was started (task_000140 leaks via a plain `bash script.sh`
invocation with no launch-pattern match).

## Round 2 (c4) — compute cross-check at self-verify

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-29T00:00:00Z
hypothesis_id: h_compute_crosscheck_selfverify_v1
levers: [control]
predicted_affected: [task_000111_cbada64a, task_001653_c4cafa73, task_001937_ac874115, task_001035_26564093, task_001330_f5aff1f5]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the recurring done-but-numerically-wrong compute cluster (2 strong tasks 000111/001653; up to 5 in the scientific_computing+data_science family) by encoding an independent-recompute discipline at the exact exit moment."
regression_risk: "Low: appends one string to the self-verify ACK, fires once per task, only on armed compute tasks; on already-passing compute tasks the recompute agrees (confirmatory no-op). Only real risk is a few extra tool calls pushing a near-budget compute task over the limit - the 8 passing compute tasks span 8-67 steps with headroom."
cost_shift: "Plus one to four short tool calls on compute tasks that reach exit; negligible on non-compute tasks (not armed). Net positive if it recovers even one pass."
rollback_trigger: "Next-round compute-cluster pass_rate flat/down, OR a previously-passing compute task (task_000602/task_000710/task_001832) regresses to budget_exceeded/max_steps from added recompute steps, OR the nudge fires on non-compute tasks."
-->

### Why

Assigned focus task_000111_cbada64a (C++ OLS + bootstrap CI) exited done at 7
steps, reward=0: committed slope m=2.5056 vs oracle 2.5997. OLS is
deterministic, so a second correct computation on the same CSV necessarily
disagrees and the bug was discoverable; but the agent verification (step 7, and
again at step 11 after the _tb2_self_verify ACK) was a format/requirements
checklist plus "the values make sense". Sweeping all 50 result.json surfaced the
same mechanism across the compute family: agents exit done at very low step
counts (7,7,8,13,20) on scientific_computing / data_science tasks and fail a
single numeric assertion - task_001653 (centroid 36.37 vs 42.01), task_001937
(Optimal Grid 60 vs 50), task_001035 (DP matrix 3.0 vs 4.0), task_001330
(Monte-Carlo 0.048 vs 0.050). initial_pytest passes on all (infra fine); the gap
is verification discipline, not capability (Bash to Python/numpy/C++ available).
A sibling R1 bet the Instruction lever (h_numeric_crosscheck_v1, pending) with a
task-start system prompt section; task_000111 shows that guidance decays by
exit. This round targets the SAME cluster at the Control lever, injected at the
self-verify exit moment - a materially different, temporally-targeted shape.

### Changes

- processors/compute_crosscheck_reminder.py - new ComputeCrossCheckReminder
  MultiHookProcessor. on_task_start arms iff the task description carries a
  generic numeric-computation term (regression/ols/bootstrap/percentile/
  centroid/cluster/matrix/optimi/gradient/monte-carlo/etc) AND at least one
  result term (round/decimal/result/value/mean/etc). on_after_tool, when the
  _tb2_self_verify synthetic ACK result flows through on an armed task, APPENDS
  one cross-validation directive (re-derive the key value with an INDEPENDENT
  method; a rerun of the same code is not independent; on disagreement probe
  off-by-one/parsing/seed-draw-order/precision/search-boundaries; sanity-check
  record counts) and disarms. Mutates only the tool-result string (contract-
  clean, same shape as CustomEditToolProcessor), inserts no messages, no system
  prompt edit, fires once per task. No task-specific literals.
- config.yaml - register ComputeCrossCheckReminder via absolute file:// path at
  _order=95, immediately after CustomSelfVerifyProcessor (_order=90) so the ACK
  result exists to append to.

### Evidence

- task_000111 result.json: reward 0, exit done, 7 steps; final_pytest
  "Expected m to be approx 2.5997, got 2.5056". Body step 7: "the values make
  sense ... This is in the format ... as required"; step 11 (post self-verify
  ACK) re-checks the same requirement checkboxes, never recomputes the slope.
- task_001653 final_pytest: Centroid 36.3687 / Distance 18.6199 vs expected
  42.0095 / 11.3444.
- task_001937 final_pytest: Expected Optimal Grid to be 50, but got 60.
- task_001035 final_pytest: Mismatch at 0,0: expected 4.0, got 3.0.
- task_001330 final_pytest: Expected m=0.050, but found m=0.048.
- Passing compute tasks (regression probe): task_000602 (9 steps),
  task_000710 (8), task_001832 (67) - all with budget headroom; a confirmatory
  recompute agrees and they still exit.

### Uncertainty

Honest split within the cluster: the retroactive check is a strong yes for the
deterministic cases (task_000111 OLS, task_001653 centroid) where an
independent method necessarily disagrees with the buggy value. It is weaker for
same-mental-model bugs (task_001330 seed draw-order, task_001035 DP recurrence)
where re-deriving with the same wrong notion reproduces the wrong number - the
nudge explicitly says "a rerun of the SAME code is not independent" and names
draw-order/indexing to counter that, but there it is a nudge, not a guarantee.
Two ways to know the bet is wrong next round: (1) the strong pair (000111/
001653) do not flip even though the nudge fired - then the remaining gap is a
model capability issue (cannot author a correct independent check), logged as
capability; (2) a previously-passing compute task regresses to budget from the
added recompute steps - the rollback trigger.

## Round 2 (c5) — clean-slate self-test hygiene reminder

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-29T04:00:00Z
hypothesis_id: h_clean_slate_self_test_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_000313_1dce9844, task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes a background-service/daemon self-test-hygiene cluster (>=3 distinct system_administration tasks, different subdomains: disk-quota monitor, k8s operator port-forward, proxy+health-monitor) whose common root cause is a self-test environment made dirty by accumulated background processes/held ports, unlike the verifier cold clean start. Generalizes to any unseen daemon/service task; advisory carries zero task-specific literals."
regression_risk: "Very low: fires <=1x/task and ONLY after the agent launches a background process (trailing & / nohup / setsid / disown / socat / uvicorn / gunicorn / http.server / start_service|server|daemon|monitor), so the many non-service tasks never see it. Appends only to a tool-result string (same contract as CustomEditToolProcessor) — no message insert/drop/reorder; contract=0 violations. Worst case (verifier wants the service alive) is covered: the reminder text explicitly says make sure it survives and keeps running, so it does not push toward wrongly killing a needed service."
cost_shift: "Negligible-to-positive: one appended string on a single armed tool result per task; may prompt 1-2 short cleanup commands that REPLACE the far costlier port-collision / defunct-process thrash loops observed (task_000010 and task_000313 burned toward 76-80 steps). No forced extra model turns."
rollback_trigger: "Revert if any previously-passing service/daemon task regresses to F attributable to the reminder (agent kills a service the verifier needed alive), if it fires on non-background commands, or if synthetic replay fails on CleanSlateSelfTestReminder."
-->

### Why

Assigned focus task_000118_3043e92d (write a disk-quota monitor daemon) failed
reward=0 with peak log dir 179306496 bytes far over the 45000000-byte threshold.
The monitor script has a startup bug: a worker-check-then-break sits at the TOP
of the loop, so a cold-started monitor exits the instant it sees no workers yet
— and the verifier starts the monitor, sleeps 0.5s, THEN starts the deployment.
The reason the agent shipped this broken script is the harness-observable
deficiency this round targets: the agent OWN self-tests reported success because
STALE background monitors from prior heredoc iterations (step 33 shows PID 603
and 3242 still alive) kept truncating the logs — a FALSE POSITIVE. The agent
never cleaned prior background processes before testing, so its self-test never
reproduced the verifier cold-start ordering and it never saw the immediate-exit
bug. This same "dirty self-test environment" mechanism recurs across the
background-service/daemon cluster: task_000010 (k8s operator port-forward) hits
repeated Address-already-in-use on port 9090 because stale socat processes hold
the port, and task_000313 (proxy + health monitor) accumulates piles of defunct
python/socat processes — both thrash toward 80/76 steps. The gap is not
capability (Bash can pkill/free ports/reset dirs) but that nothing tells the
agent its self-test env is dirty and unlike the verifier cold start.

### Changes

- processors/clean_slate_self_test.py — new CleanSlateSelfTestReminder
  MultiHookProcessor. on_before_tool arms the first Bash command that launches a
  background process (trailing bare &, or nohup/setsid/disown/socat/uvicorn/
  gunicorn/hypercorn/http.server/framework-run/start_service|server|daemon|
  monitor). on_after_tool appends a ONE-TIME generic clean-slate self-test
  advisory to that call result and disarms (fires <=1x/task). Contract-safe:
  mutates only event.result (same shape as CustomEditToolProcessor); no message
  insertion. _order=33 (after CustomEditToolProcessor 30, before
  CustomSelfVerifyProcessor 90). No task ids, ports, paths, thresholds, or code
  in the advisory — strategy only.
- config.yaml — R0 config + register CleanSlateSelfTestReminder via absolute
  file:// path, placed after CustomEditToolProcessor and before
  CustomSelfVerifyProcessor.

### Evidence

- task_000118 step 33 tool result: two stale python3 monitors (PID 603, 3242)
  from prior heredoc runs still alive; the agent later self-tests (steps
  34/42/50) report logs truncated to 4.0K — FALSE POSITIVE — and it commits the
  broken script at steps 36/44. Verifier cold-start: peak 179306496 bytes.
- task_000010 steps 20/22/26/38 tool results: repeated socat bind
  0.0.0.0:9090 Address already in use and OSError Errno 98 Address already in
  use / Port 9090 still in use from stale socat holding the forward port; run
  budget_exceeded at 80 steps.
- task_000313 steps 30/36 tool results: piles of [python3] defunct and [socat]
  defunct processes across self-test iterations; run loop_detected at 76 steps.
- Validators: canonicalize ok (0 templates); dry_fire likely_bugs=0; contract
  violations=0; literals findings=0. Detector unit-checked: arms on bg launches
  (trailing &, nohup, socat, uvicorn) and does NOT arm on ls/cat/grep/logical-
  and/pipes/import-guards.

### Uncertainty

Two ways to know the bet is wrong: (1) task_000118 stays F even though the
reminder fired — then the residual is the model failing to convert the now-
visible cold-start bug into a working startup grace period (a capability gap to
log), while the reminder still did its de-contamination job on task_000010/
task_000313; (2) a previously-passing service task where the verifier needs the
service alive regresses because the agent over-eagerly killed it — the rollback
trigger, though the reminder own "make sure it survives and keeps running"
branch is designed to prevent exactly that. An earlier c5 R1 correctly noted
task_000118 terminal blocker is partly a capability gap and shipped a loop-
detection nudge that did not flip it; this round targets the DIFFERENT, upstream,
harness-observable false-positive mechanism, backed by a >=3-task cross-subdomain
cluster.

## Round 4 (c3) — ensure requests for verifier (R0/R1 lineage, 6-task cluster)

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_verifier_dep_requests_v2
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000106_23215092, task_000939_1592be48, task_000958_4bb2b05d, task_001857_24daeef3, task_002063_8c8adcfe]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=19/50; +4/-2 gained=task_000011_d089ef35,task_000133_20c45b39,task_000187_30465ebf,task_000477_de422e4d lost=task_000313_1dce9844,task_001832_dd672877; score 0.3800 >= incumbent(mean) 0.3700 - tol 0.0400
expected_global_gain: "Unblocks a 6-task HTTP-service cluster spanning >=3 domains whose verifier pytest aborts at collection on import requests -- currently guaranteed reward=0 for a purely infrastructural reason independent of solution quality. Generalizes to any unseen HTTP-service task with the same verifier pattern."
regression_risk: "Very low: guard is idempotent, silent, || true-terminated and runs the original command unchanged after the ;; no-op when requests already imports; fires <=1x/task, only on armed service+listen tasks. Measured across all 50 trajectories, exactly 1 passing task arms (task_001382_6c9d34ea) and the guard is behavior-preserving there. Offline image -> pip fails silently -> status quo. Never mutates message history (contract-clean)."
cost_shift: "Negligible: one fast import probe (+ at most one quiet pip install of a small pure-Python wheel) prepended to a single Bash call on armed tasks only; no extra model turns."
rollback_trigger: "Revert if any previously-passing service task (esp. task_001382_6c9d34ea) regresses to F attributable to the guard, or if synthetic replay fails on the VerifierDepEnsurer processor."
retry_rationale: "The current_config I evolve from (R1/config.yaml) is R0-lineage with NO VerifierDepEnsurer registered, so this mechanism has never landed in this lineage -- the focus task and its 5 siblings are still guaranteed reward=0. h_verifier_dep_requests_v2 was accepted (not reverted) in sibling lineages; novelty permits re-establishing it on the lineage that still lacks it. NEW evidence this round: the cluster grew from 5 to 6 tasks (task_000939_1592be48 now exhibits the identical import requests collection abort)."
-->

### Why

Assigned focus task_000028_7fe033ac (system_administration: Nginx reverse proxy
+ C++ HTTP socket backend on 127.0.0.1:8080) built a functionally complete
solution and exited done at 26 steps with initial_pytest.passed=true, yet scored
reward=0. The failure is entirely in the verifier phase: the injected
test_final_state.py begins import requests, the base image lacks requests, so
pytest aborts at COLLECTION (ModuleNotFoundError: No module named 'requests' ->
Interrupted: 1 error during collection) and every assertion errors at once
regardless of solution correctness. The agent cannot anticipate this -- the test
file is invisible during the agent phase (TB2 sandbox topology) and testing with
urllib/curl is valid. A scan of all 50 result.json returns 6 tasks --
task_000028, task_000106, task_000939, task_000958, task_001857, task_002063 --
every one an HTTP-service task with the identical collection abort, none of which
used or installed requests. Harness deficiency, not a model capability gap.
Lineage note: the current_config (R1/config.yaml) is R0-lineage and has no
VerifierDepEnsurer, so no requests fix has ever landed here.

### Changes

- processors/verifier_dep_ensurer.py -- VerifierDepEnsurer MultiHookProcessor
  (reused proven, contract-clean, dry-fire-clean implementation). on_task_start
  arms tasks whose description names a network SERVICE surface AND a concrete
  LISTEN surface. On the first approved Bash call of an armed task, prefixes the
  command with an idempotent, silent, best-effort import-requests-or-pip-install
  guard, || true-terminated (original command runs unchanged after the ;). Fires
  <=1x/task; only rewrites tool_input, never mutates message history.
- config.yaml -- R1 config + register VerifierDepEnsurer via absolute file://
  path, placed after ToolCallCorrectionLayer and before TaskTimeReminderProcessor
  (_order=10). Everything else byte-identical to R1.

### Evidence

- task_000028 result.json: initial_pytest.passed=true; final_pytest passed=false
  rc=2; output_tail shows import requests -> ModuleNotFoundError -> Interrupted.
  Transcript: sole "requests" token is the English word in the prompt; grep -c
  "pip install requests" = 0.
- task_000106/000939/000958/001857/002063 result.json: identical requests
  collection abort; each an HTTP-service task; none installed requests.
- pip works in-run: task_000106/000378/000790 show "Successfully installed".
- Arming precision (all 50 trajectories): all 6 collection-abort tasks arm;
  exactly 1 passing task arms (task_001382) where the guard is behavior-
  preserving; other armed-failing tasks fail for unrelated reasons (no-op guard).
- Validators: canonicalize {"ok": true, "checked_templates": 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Two ways to know the bet is wrong: (1) the 6 predicted tasks do not flip even
though collection now runs -- their solutions were also wrong (assertions fail),
the guard did its job, and the residual is a model capability gap to log; (2) an
offline image makes pip install fail -- the guard is || true, so status quo, no
new failure. Regression on a previously-passing service task (esp. task_001382)
attributable to the guard is the rollback trigger.

## Round 4 (c2) — low-DPI OCR quality advisor (re-establish in R1 lineage)

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_ocr_lowdpi_advisor_v2
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000505_50b5162d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Unblocks the OCR-input cluster (>=2 distinct-domain tasks: task_000015 URL-migration schema image, task_000505 SSH-key evidence image) whose reward=0 is caused by garbled tesseract output on small/no-DPI images, not by reasoning errors. Generalizes to any unseen image-to-text task; the injected recipe (upscale + explicit --dpi + binarize + cross-check) is standard tesseract practice with zero task-specific literals."
regression_risk: "Very low: fires <=1x/task and ONLY when a real tesseract/pytesseract Bash call emits the low-resolution signal (Invalid resolution / Estimating resolution), so the ~47 non-OCR tasks never see it. Appends only to a tool-result string (same contract as CustomEditToolProcessor); no message insert/drop/reorder. contract=0 violations."
cost_shift: "Negligible-to-slightly-positive on OCR tasks (may prompt 1-2 corrective re-runs, replacing wasted near-identical retries); exactly zero on non-OCR tasks. No forced extra model turns."
rollback_trigger: "Revert if any previously-passing task regresses to F attributable to the advisory, or if synthetic replay fails on the OcrQualityAdvisor processor."
retry_rationale: "h_ocr_lowdpi_advisor_v1 was ACCEPTED (not reverted) in R3 with the same predicted_affected. But the current_config for this proposal (R1/config.yaml, byte-identical to R3/config.yaml) does NOT wire OcrQualityAdvisor -- the promoted R3 baseline is the stock 12-entry R1 pipeline with no OCR processor. The mechanism is therefore ABSENT from the lineage I evolve and task_000015 is still broken. Novelty permits re-establishing an accepted-not-reverted hypothesis; this round wires it in."
-->

### Why

Assigned focus task_000015_89886d8d (build a URL-migration utility from a
schema image) failed reward=0 at 28 steps exit=done: the verifier ran the
agent migrate.py over 2000 hidden URLs and required accuracy >= 0.98; the
agent got 0.6556. Root cause is upstream of the code: EVERY tesseract call
(messages steps 2,4,10,12,14,20,24,28) returned garbled text prefixed by
tesseract own "Warning: Invalid resolution 0 dpi. Using 70 instead.
Estimating resolution as 111". The OCR read atalogyitem, depariment,
trafic source, feccount, sortorder, notity. The agent then INFERRED the
ROUTES key names from the garble + sample URLs (steps 47/49: "despite the
garbled output, I was able to infer the routing rules") and shipped it --
misspelled/guessed key names make ~1/3 of records mismatch the golden output.
Across steps 2-28 the agent re-ran contrast/threshold/PSM/OEM/whitelist
variants on the ORIGINAL resolution but NEVER upscaled the image or set an
explicit --dpi -- the two highest-yield fixes for small/low-DPI OCR. A
cross-task scan (grep "Invalid resolution" *.messages.json) found the SAME
root cause on task_000505_50b5162d (security): tesseract on the SSH-key
evidence image hit the identical low-res warning; character confusions
(I/l/1, 5/S, spurious spaces) made the exact-string trojan detector miss
every adversarial sample (2 of 2 evil bypassed). Two distinct domains, one
harness-fixable mechanism: the agent trusts a low-quality first OCR read and
never reaches for the standard preprocessing. (A third task, task_002063,
emits the tesseract signal but its terminal blocker is the import requests
collection abort -- a different cluster; the advisor is a no-op there and is
not claimed.)

This is a harness deficiency (missing dynamic guidance at the failure point),
not a model capability gap: the agent has full Bash/PIL/tesseract capability;
what is missing is the just-in-time nudge to apply upscale+--dpi when tesseract
itself flags a guessed resolution.

### Changes

- processors/ocr_quality_advisor.py -- OcrQualityAdvisor MultiHookProcessor.
  on_before_tool records Bash calls invoking tesseract/pytesseract/
  image_to_string; on_after_tool, if that call result carries tesseract own
  low-resolution signal (Invalid resolution / Estimating resolution / Using N
  instead), appends a ONE-TIME generic advisory: upscale ~3-4x, pass explicit
  --dpi 300, grayscale+binarize with --psm 6, diff the re-run, disambiguate
  ambiguous glyphs (I/l/1, 0/O, 5/S, 8/B, rn/m, spaces), verify against any
  provided sample before committing downstream code. Fires <=1x/task, only on
  real OCR calls with the low-res signal. Contract-safe: mutates only the
  tool-result string, no message insertion. _order=32.
- config.yaml -- R1 pipeline + register OcrQualityAdvisor via absolute file://
  path (inserted between CustomEditToolProcessor and CustomSelfVerifyProcessor).
- system_prompt.txt -- sibling read by SiblingSystemPromptBuilder, byte-identical
  to R1 baseline.

### Evidence

- task_000015 result.json: reward=0, exit=done, 28 steps; final_pytest
  "Accuracy metric 0.6556 ... below the 0.98 threshold".
- task_000015 messages steps 2/4/10/12/14/20/24/28: repeated garbled tesseract
  output, each with Invalid resolution 0 dpi; steps 47/49 committed inferred
  ROUTES; NO upscale/--dpi anywhere.
- task_000505 result.json: final_pytest "2 of 2 evil bypassed"; reward=0.
- Cross-scan: exactly 3 tasks emit the tesseract low-res signal; 2
  (task_000015, task_000505) have OCR as terminal root cause, 1 (task_002063)
  blocked by a different cluster (requests-collection).
- Validators: canonicalize ok; dry_fire likely_bugs 0/0; contract violations 0;
  literals findings 0.

### Uncertainty

Two ways to know the bet is wrong: (1) the predicted tasks do not flip even
though the advisory fired -- then either the agent still did not act on the
recipe (following-instructions gap) or a clean OCR read alone is insufficient
(schema-interpretation ambiguity beyond the garble), reclassifying the residual
as a capability gap; (2) an OCR task whose first read was already correct gets
the advisory and the agent chases it into a worse read -- the rollback trigger.
The signal is narrow (tesseract own DPI warning), so non-OCR regression is nil.

## Round 4 (c1) — independent-derivation item at self-verify

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_independent_derivation_selfverify_v1
levers: [control]
predicted_affected: [task_000011_d089ef35, task_000111_cbada64a, task_001653_c4cafa73, task_001937_ac874115, task_001035_26564093]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips members of the recurring done-but-numerically-wrong deterministic-compute cluster (>=2 strong: task_000011 mesh-MSE, task_000111 OLS; up to 5) whose sole blocker is a CIRCULAR self-verify. Generalizes to any unseen task whose expected output is derivable from the description; zero task-specific literals."
regression_risk: "Low. Net +0 messages (mutates the trailing self-verify checklist message content, not an insert/drop/reorder), contract-clean (violations=0), fires <=1x/task. The injected item is self-scoping ('if the result is fully determined by the task description'), so it is a model-judged no-op on non-deterministic / already-correct tasks. Residual: 1-2 extra verification tool calls could push a near-budget compute task over; cited passing compute tasks had step headroom."
cost_shift: "+0 to a few short verification tool calls on deterministic-compute tasks that reach exit; ~zero on the ~45 non-compute tasks beyond one appended checklist item. Net positive if it recovers >=1 pass."
rollback_trigger: "Next-round compute-cluster pass-rate flat/down, OR a previously-passing task regresses to budget_exceeded/max_steps attributable to added verification steps, OR replay fails on IndependentDerivationVerify."
retry_rationale: "Signature (control + this compute cluster) overlaps the PENDING sibling h_compute_crosscheck_selfverify_v1 (R2/c4), which is not reverted so novelty permits it. NEW evidence + different shape: (1) current_config is R1, which contains NEITHER crosscheck processor -- the cluster has zero fix in the lineage I evolve; (2) assigned focus task_000011 is a NEW cluster member (a socket/service task that keyword-gated arming would risk missing) and the cleanest deterministic case; (3) different injection point (checklist user message vs the low-salience synthetic-ACK tool-result string) and universal-but-self-scoping arming instead of compute-keyword gating."
-->

### Why

Assigned focus task_000011_d089ef35 (scientific_computing): write a C TCP
server that, per quadrant digit, extracts a 2x2 sub-grid, nearest-neighbour
refines it to 4x4, and returns the MSE vs a hardcoded reference. exit=done,
24 steps, reward=0. Root cause is a pure indexing bug the agent half-fixed:
after correcting start_col to (quadrant%2)*2 it left the symmetric row bug
start_row = quadrant/2 (must be (quadrant/2)*2), so quadrants 2 & 3 read rows
1-2 instead of 2-3. Q0/Q1 pass; Q2 returns 38.25 (expected 68.25), Q3 55.25
(expected 93.25). The HARNESS-shaped deficiency is not the bug itself (that is a
model reasoning slip) but the CIRCULAR self-verification that let it ship: at
the exit self-verify checkpoint the agent "verified" each quadrant by
hand-deriving the expected MSE FROM ITS OWN WRONG refined grid and matching the
two, writing "38.25 checkmark / 55.25 checkmark". It never derived the expected
value independently from the spec's literal quadrant definition (quadrant 2 =
rows 2-3 = 9,10,13,14). The stock self-verify checklist asks the agent to
"validate your verification method" but never says "recompute the expected
result along an independent path from the specification and compare" -- the one
discipline that breaks the circularity. This same done-but-numerically-wrong-
after-superficial-self-verify shape recurs across the compute family:
task_000111 (OLS slope 2.5056 vs 2.5997), task_001653 (centroid), task_001937
(grid 60 vs 50), task_001035 (DP/primer). All initial_pytest-pass (infra fine),
all exit=done.

### Changes

- processors/independent_derivation_verify.py -- new IndependentDerivationVerify
  MultiHookProcessor (_singleton_group tb2_independent_derivation_verify,
  _order=95, runs AFTER CustomSelfVerifyProcessor _order=90). on_before_model:
  when the trailing user message is the self-verify checklist (detected via a
  stable sentinel substring from that message) and the added marker is absent,
  it rewrites that message's content to append ONE extra numbered item: for any
  result deterministically derivable from the task description, compute the
  expected value a SECOND independent way straight from the spec (hand-work a
  small case, re-implement the core step in a different tool, or trace the spec
  element by element) and compare; on disagreement the solution is wrong even if
  it ran cleanly -- fix the discrepancy (indexing/off-by-one, axis/row-col order,
  boundaries, rounding, formula terms) before finishing. Net +0 messages
  (contract-clean), fires <=1x/task, no task-specific literals.
- config.yaml -- R1 config + register IndependentDerivationVerify via absolute
  file:// path at the end of the processor list (_order=95). Sibling
  system_prompt.txt written byte-identical to R1 baseline (read by
  SiblingSystemPromptBuilder). Everything else byte-identical to R1.

### Evidence

- task_000011 result.json: initial_pytest.passed=true; final_pytest
  "Expected '68.25' for quadrant 2 ... got '38.25'"; "Expected '93.25' ... got
  '55.25'"; 3 failed, 2 passed; reward=0, exit=done.
- task_000011 messages: step ~14 fixes start_col only, leaves start_row =
  quadrant/2; server test returns Q2=38.25/Q3=55.25; self-verify turn hand-
  derives MSE from its OWN wrong grid and stamps both correct (circular).
- task_000111 final_pytest "abs((2.5056 - 2.5997))"; body verification = "the
  values make sense ... in the format". task_001653/001937/001035: single
  deterministic numeric-assertion failures, all exit=done reward=0.
- Validators: canonicalize {"ok": true, "checked_templates": 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Retroactive check is a strong yes for the deterministic cases (task_000011
indexing, task_000111 OLS, task_001653 centroid) where an independent derivation
necessarily disagrees with the buggy value. It is weaker for same-mental-model
bugs (seed draw-order) where re-deriving with the same wrong notion reproduces
the wrong number -- the item counters this by naming "a rerun of the same code
is not independent" and "hand-work a small case", but there it is a nudge, not a
guarantee. Two ways to know the bet is wrong: (1) the predicted tasks stay F
even though the item fired -- then the residual is a model reasoning gap (agent
cannot execute the independent derivation), logged as capability; (2) a
previously-passing compute task regresses to budget_exceeded from the added
verification steps -- the rollback trigger.

## Round 4 (c0) — stdlib module-shadow advisor

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_module_shadow_advisor_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes a general Python footgun — a required-name script whose basename shadows a stdlib module (operator/queue/types/select/socket/json/csv/io/...) crashes with a circular-import error on python3 script.py, and the agent's instinctive rename directly violates the mandated filename. Advisory carries no task-specific literals and generalizes to any unseen collision of this shape."
regression_risk: "~Nil: fires <=1x/task and ONLY on the two-part shadowing signature (circular-import error AND a user-owned traceback frame re-entered through a stdlib frame). Ordinary intra-project circular imports (user-only frames) and all non-crashing runs never match (unit-tested). Mutates only the tool-result string (same contract as CustomEditToolProcessor/OcrQualityAdvisor); no message insert/drop/reorder."
cost_shift: "Negligible-to-slightly-positive: may prompt 1-2 corrective re-runs replacing wasted rename/re-debug churn on a matching task; exactly zero on the ~49 non-matching tasks. No forced extra model turns."
rollback_trigger: "Revert if any previously-passing task regresses to F attributable to the advisory, or if synthetic replay fails on the ModuleShadowAdvisor processor."
-->

### Why

Assigned focus task_000010_644ab1c2 (write the required operator script: backup +
socat 9090->8080 port-forward + pexpect CLI automation) died budget_exceeded at
80 steps, reward=0. final_pytest shows two failing tests, but the CLEAN
harness-fixable one is test_operator_script_exists: the required script path does
not exist. Root cause verified in the transcript: the agent named the required
script with a basename that shadows Python's stdlib operator module. Running the
script by path from its own directory puts that dir first on sys.path, so the
stdlib's from-operator-import (reached transitively via import subprocess ->
collections) imported the agent's OWN file, crashing with
"cannot import name deque from partially initialized module collections
(most likely due to a circular import)". The agent CORRECTLY read the message as
a naming conflict but then applied the WRONG fix — renamed the script to a
non-shadowing name — which made the script run but left the mandated path empty,
guaranteeing reward=0 on the file-existence check no matter how good the logic.
This is a general, well-known Python module-shadowing footgun; the harness can
recognise the exact runtime signature and steer to the correct fix (keep the
name, invoke without the script dir on sys.path) rather than the rename trap.

### Changes

- processors/module_shadow_advisor.py — new ModuleShadowAdvisor
  MultiHookProcessor. on_before_tool records Bash calls that run a Python script
  by path (python[3] ... name.py; not -c / -m); on_after_tool, if that call's
  result carries the module-shadowing signature (a circular-import /
  partially-initialized-module error whose traceback contains BOTH a user-owned
  .py frame and a stdlib frame — i.e. control re-entered a user file through a
  stdlib import), appends a ONE-TIME generic advisory: this is basename shadowing
  (lists classic colliders), do NOT rename if the filename is task-required,
  instead invoke without the script dir on sys.path (run from another dir, or
  PYTHONSAFEPATH=1 / python3 -P on 3.11+), then re-confirm the required output
  path still exists. Fires <=1x/task, only on the exact signature. Contract-safe:
  mutates only the tool-result string, no message insertion. _order=33 (after
  CustomEditToolProcessor 30 / OcrQualityAdvisor 32, before
  CustomSelfVerifyProcessor 90).
- config.yaml — R1 lineage + register ModuleShadowAdvisor via absolute file://
  path immediately before CustomSelfVerifyProcessor. Everything else
  byte-identical to R1.

### Evidence

- task_000010 result.json: exit_reason=budget_exceeded, reward=0; final_pytest
  test_operator_script_exists AssertionError the required script path does not
  exist.
- task_000010 messages step 62: writes the required script name (heredoc); step
  64: runs it by path; step 65 tool result: traceback re-entering the user
  script through /usr/lib/python3.10/collections/__init__.py at
  "from operator import eq as _eq", ending
  "ImportError: cannot import name deque from partially initialized module
  collections (most likely due to a circular import)"; step 66: renames the
  script (the anti-pattern that empties the mandated path).
- Detector unit-tested: matches the real traceback + the run-by-path command;
  rejects python3 -c, python3 -m pytest, a user-only intra-project circular
  import, and a plain SyntaxError.
- Validators: canonicalize {"ok": true, "checked_templates": 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Only task_000010 shows this exact signature in the R3 set (idiosyncratic-filter
note in candidates.md): shipped as the assigned-focus exception because the
mechanism is a general Python footgun with zero literals and a ~zero regression
surface. Two ways to know the bet is wrong: (1) task_000010 stays F even though
the advisory fired — expected in part, since its SECOND failing test
(test_api_success_log) needs the port forwarding to actually work, which the
agent never got right (a model capability gap, logged separately); the advisory
only removes the guaranteed-zero rename trap and flips test_operator_script_exists.
(2) a previously-passing task regresses attributable to the advisory — unlikely
given the two-part signature gate, but the rollback trigger.

NEEDS_FROM_HUMAN: task_000010's second blocker (manifests never applied because
the socat/python port-forward 9090->8080 was never made to work reliably) is a
model capability gap in wiring up a persistent port forwarder, not harness-fixable
without task-specific injection — skip.

## Round 4 (c4) — ensure requests client (unconditional)

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_verifier_client_dep_unconditional_v1
levers: [control]
predicted_affected: [task_000106_23215092, task_000028_7fe033ac, task_000939_1592be48, task_000958_4bb2b05d, task_001857_24daeef3, task_002063_8c8adcfe]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Unblocks a 6-task, 4-domain cluster (data_querying, system_administration, software_engineering, debugging) whose reward=0 is purely infrastructural: the injected verifier test_final_state.py aborts pytest at collection on `import requests` (base image lacks it), erroring every assertion at once regardless of solution quality. Generalizes to any unseen task whose verifier imports requests; zero task-specific literals."
regression_risk: "Very low: guard is `|| true`-terminated so the agent's original command always runs unchanged after the `;`; no-op when requests already imports (short-circuits before install); fires <=1x/task; contract-clean (rewrites only tool_input of an approved ToolCallEvent, never message history). Offline/pip-blocked image -> silent install failure -> exactly today's behaviour (no new failure). Fires on ALL tasks now, but the probe is a near-instant import check, so non-service passing tasks see negligible cost and no behavioural change."
cost_shift: "Negligible: one `python3 -c 'import requests'` probe (+ at most one quiet pip install of a small pure-Python wheel) prepended to a single Bash call per task; no extra model turns."
rollback_trigger: "Revert if any previously-passing task regresses to F attributable to the prepended guard, or if synthetic replay fails on the VerifierClientDepEnsurer processor."
retry_rationale: "Prior h_verifier_dep_requests_v1 (accepted) / v2 (pending) targeted the same verifier-dep cluster at the control lever but ARMED via description keyword regex. NEW evidence this round: the cluster grew 5 -> 6 (task_000939, software_engineering, joined the identical `import requests` collection-abort), confirming keyword arming is a brittle proxy for the verifier's imports (which the agent never sees). This is a DIFFERENT shape: the arming heuristic is removed entirely and the idempotent+silent+`|| true` guard fires unconditionally on the first substantive Bash call of every task, eliminating the arming-miss failure mode. Distinct hypothesis_id and a 6-task predicted set."
-->

### Why

Assigned focus task_000106_23215092 (data_querying) built a functionally
complete co-authorship-graph Flask API on 127.0.0.1:8000, verified all endpoints
via `urllib.request`, and exited `done` at 35 steps with initial_pytest passed —
yet scored reward=0. The failure is 100% in the verifier phase: the injected
`test_final_state.py` begins `import requests`, the base image lacks it, so
pytest aborts at *collection* (`ModuleNotFoundError: No module named 'requests'`
-> `Interrupted: 1 error during collection`) and every assertion errors at once
regardless of correctness. The agent cannot anticipate this — the test file does
not exist during the agent phase (TB2 sandbox topology) and testing with
urllib/curl is entirely valid, so it has no reason to install requests.
`grep -l "No module named 'requests'" *.result.json` returns exactly 6 tasks
this round (task_000106, task_000028, task_000939, task_000958, task_001857,
task_002063) across four domains, every one with initial_pytest passed and the
identical collection abort, none of which installed requests. Harness deficiency,
not a capability gap. The prior fix armed on description keywords; the cluster
grew from 5 to 6 because task_000939 joined it, so I removed the arming heuristic
and fire the (idempotent, silent, `|| true`) guard on every task.

### Changes

- `processors/verifier_client_dep_ensurer.py` — new VerifierClientDepEnsurer
  MultiHookProcessor. On the first substantive approved Bash call of EVERY task,
  prefixes the command with an idempotent, silent, best-effort guard:
  `python3 -c 'import requests' 2>/dev/null || pip install -q requests >/dev/null 2>&1 || true ; <original>`.
  No keyword arming. Fires <=1x/task; never mutates message history (contract
  violations=0). `_singleton_group="verifier_client_dep_ensurer"`, `_order=10`.
- `config.yaml` — R1 config + register VerifierClientDepEnsurer via absolute
  file:// path, placed after ToolCallCorrectionLayer and before
  TaskTimeReminderProcessor.
- `system_prompt.txt` — byte-identical copy of the R1 sibling prompt (read by
  SiblingSystemPromptBuilder).

### Evidence

- task_000106 result.json: initial_pytest.passed=true; final_pytest passed=false
  rc=2, tail `test_final_state.py:4: import requests -> ModuleNotFoundError ->
  Interrupted: 1 error during collection`. Agent exited done at 35 steps.
- task_000106 messages: `pip3 install numpy` -> `Successfully installed
  numpy-2.2.6`; `pip3 install scipy` -> `Successfully installed scipy-1.15.3`
  (proves runtime pip works in this eval); requests never installed; API tested
  via urllib.
- task_000028 / task_000939 / task_000958 / task_001857 / task_002063
  result.json: identical `import requests` collection abort; all initial_pytest
  passed, reward=0; each issues 50+ Bash calls (fire-once guard reliably fires).
- Validators: canonicalize {"ok": true, "checked_templates": 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Two ways to know the bet is wrong: (1) the predicted tasks don't flip even
though collection now runs — then their solutions were also wrong (assertions
fail), the guard did its job, and the residual reclassifies to a model
capability gap to log; (2) an offline image makes pip fail — the guard is
`|| true`, so status quo, no new failure. Firing on all tasks (not just armed
ones) is the deliberate change from prior versions; the near-instant import
probe makes the non-service blast radius negligible. Regression on a
previously-passing task attributable to the guard is the rollback trigger.

## Round 4 (c5) — no-op: task_000111 OLS slope is a data/spec gap

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_noop_task_000111_ols_slope
levers: []
predicted_affected: []
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "None claimed. Assigned task_000111 fails on a single numeric assertion (OLS slope) whose error is not attributable to any harness-observable defect; the surrounding compute cluster is heterogeneous (RNG-seed, loop-logic, deterministic-recompute) and fails the recompute retroactive check, and is already targeted by the pending sibling h_numeric_crosscheck_v1."
regression_risk: "None - byte-for-byte copy of the R1 (current_config) config."
cost_shift: "Zero - no config change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus task_000111_cbada64a (scientific_computing): write a C++ OLS +
bootstrap-CI analyzer for /home/user/noisy_data.csv, emit m,c,ci_lower,ci_upper
to /home/user/result.txt. The agent exited done at 7 steps / 26.7s with
initial_pytest passing; final_pytest failed on ONE assertion:
Expected m to be approx 2.5997, got 2.5056 (abs delta 0.0941, far beyond float
noise). c and both CI bounds tests passed (2 passed, 1 failed).

Root-cause diagnosis: the agent slope formula is textbook-exact simple OLS
m = (n*Sxy - Sx*Sy)/(n*Sx2 - Sx*Sx) and its CSV parse loop is robust
(getline + find comma + stod); head -20 looked clean and wc -l returned
100, so all rows parsed. For a DETERMINISTIC simple-OLS on the same 100 points,
a faithful implementation must match the oracle to float precision. A 0.094
discrepancy therefore means either (a) the agent parsed data differs from the
oracle, or (b) the oracle is not computing the plain OLS the task text names -
BOTH outside harness control and neither harness-observable during the agent
phase. This is a model/data/spec gap, not a harness deficiency.

Retroactive check for the obvious candidate (a self-verify independently-
recompute the numeric result nudge, Control lever): FAILS on the focus task.
Recomputing OLS a second way (numpy.polyfit) yields the SAME 2.5056 if the
C++ is faithful, so it would not surface the error - the delta is not a
transcription bug the agent could catch by re-deriving with the same definition.

Cluster check: the round other done-but-wrong compute tasks are heterogeneous
in mechanism - task_001330 (verifier hints numpy seed 42 / draw-order),
task_001937 (bash gradient-descent loop-logic: Grid 60 vs 50),
task_001653 (deterministic ETL centroid/distance), task_001035 (primer search).
A single generic recompute-independently discipline passes the retroactive
check for at most the seed case and NOT for the deterministic-recompute members
(000111, 001653) or the loop-logic member (001937). It would also force extra
recompute turns across the ~19 already-passing compute-adjacent tasks
(cost + regression risk) and it DUPLICATES the still-pending sibling bet
h_numeric_crosscheck_v1 (instruction lever), which already targets this exact
cluster including task_000111. Per the brief anti-drift rule and the
capability-gap rule, the smallest defensible action is an explicit no-op.

### Changes

- config.yaml - byte-for-byte copy of the current_config (R1/config.yaml).
  Canonicalizes: ok true, checked_templates 0.

### Evidence

- task_000111 result.json: reward=0, exit_reason=done, steps=7, elapsed_s=26.7;
  final_pytest AssertionError Expected m to be approx 2.5997, got 2.5056;
  the other two tests (c, CI) passed.
- task_000111 messages step 2 (assistant): OLS formula
  m = (n*sum_xy - sum_x*sum_y)/(n*sum_x2 - sum_x*sum_x) - textbook-correct.
- task_000111 messages step 1 tool result: head -20 clean x,y rows;
  wc -l = 100 - all rows present; parse loop getline + find comma + stod.
- task_000111 messages step 8-12: after _tb2_self_verify fired, the agent only
  re-read requirements and ran ls -lh - it never independently recomputed m.
- Cluster heterogeneity: task_001330 final_pytest hints numpy.random.seed(42);
  task_001937 Grid 60 vs 50 (bash GD loop); task_001653 centroid/distance
  (deterministic); each a distinct mechanism, defeating a single recompute nudge.

### Uncertainty

If a future round surfaces a real multi-task cluster where the numeric error IS
a transcription/implementation bug that an independent recompute would catch
(retroactive check returns yes on 2+ members), a Control-lever recompute-and-
reconcile nudge on the self-verify checkpoint becomes defensible. This round
cluster does not meet that bar, and the pending h_numeric_crosscheck_v1 already
occupies the instruction lever for it.

NEEDS_FROM_HUMAN: task_000111 requires the oracle exact data/definition
of the OLS slope (agent used textbook simple OLS and got 2.5056 vs oracle 2.5997
on a deterministic fit) - a model/data/spec gap not observable to the harness
during the agent phase; no harness fix - skip.

## Round 4 (c7) — cumulative-truncation loop breaker (v2, C-libcsv focus)

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_cumulative_length_trunc_recovery_v2
levers: [control]
predicted_affected: [task_000133_20c45b39, task_001032_1adaccb9, task_000958_4bb2b05d, task_000683_7c966a71, task_000747_424c178b]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Reclaims the ~half-budget spent on repeated 4096-token re-narration across a 5-task budget_exceeded cluster whose shared mechanism is an ALTERNATING truncate->act->truncate loop the stock consecutive-only length recovery cannot escalate on. Generalizes to any unseen chronic-re-narration task; zero task-specific literals."
regression_risk: "Low: never force-exits, never removes messages (contract-clean). Tasks that truncate a few times then recover stay under chronic_threshold=4; above it the only effect is a shorter collapsed turn plus a 'do one concrete command' directive. The only heavy-truncating PASS probe (task_001832, 7 truncs front-loaded) keeps 40+ productive turns; task_000011 (3 truncs) stays under threshold."
cost_shift: "Net negative (lower): chronic-loop tasks stop emitting repeated 4096-token re-narration turns; stub collapse shrinks forwarded context. No forced extra model turns."
rollback_trigger: "Revert if next-round pass_rate drops, OR task_001832/task_000011 (or any previously-passing truncating task) regresses to F attributable to the chronic directive, OR synthetic replay fails on the processor."
retry_rationale: "R2-c0 h_cumulative_length_trunc_recovery_v1 (same lever/mechanism) is still pending (never promoted into the R1 lineage I edit from — R1 config has only the stock consecutive processor), so no cumulative fix has landed here. NEW evidence this focus: task_000133_20c45b39 exhibits the identical alternating-truncation loop (13 cumulative truncations, max-consecutive 4, budget_exceeded) and was NOT in the v1 predicted set; a fresh cross-task scan of this round finds a 5-task cluster (000133/001032/000958/000683/000747) with cumulative>=4 but max-consecutive<=4, so the stock repeat_threshold=2 consecutive escalator is defeated on all five."
-->

### Why

Assigned focus task_000133_20c45b39 (fix a vendored libcsv Makefile, then write
a C anomaly detector) died budget_exceeded at 80 steps, reward=0 (both clean and
evil corpus tests fail — the detector never became correct). The proximate
blocker is a genuine parse bug the agent never cracked (`15,1,0,1,0` misparsed as
`15,15,05,15,05`). The harness-shaped deficiency that consumed the budget is a
chronic length-truncation loop: 13 finish_reason=length truncations at msg
indices [7,10,20,22,24,26,34,36,38,40,50,54,67], ALTERNATING with tool calls
(turn sequence CCTTCCCCTTTTCCCTTTTCCCCTCTCCCCCTC), the assistant re-narrating the
identical "The CSV parser is reading the file incorrectly … 15,15,05,15,05"
sentence at msgs 17/21/33/35/53/62/64/68. The stock
LengthTruncationRecoveryProcessor (repeat_threshold=2) escalates only on
*consecutive* truncations; a tool call resets the counter, so on this alternating
shape the hard escalation never fires and the collapsed narration keeps
re-priming the loop. A cross-task scan found the same shape on task_001032 (13),
task_000958 (7), task_000683 (5), task_000747 (4) — all budget_exceeded, all
with max-consecutive-truncation <=4 so the stock escalator is defeated on all
five. Control-lever mechanism gap, not a knob and not a prompt rule (the
corrective nudge already reaches the model and is ignored).

### Changes

- processors/length_recovery_cumulative.py — CumulativeLengthTruncationRecovery
  MultiHookProcessor. Same _singleton_group ("tmax_length_recovery") and _order
  (5) as the stock processor, so it replaces it. Tracks CUMULATIVE truncations
  per task (a tool call resets the consecutive run but not the cumulative count).
  Once cumulative >= chronic_threshold (4) it (a) escalates to a terminal "STOP
  narrating, ONE minimal command, do something different / write required output
  at the exact path" directive and (b) collapses the runaway assistant turn to a
  short stub instead of head+tail so the repeated narration stops re-priming the
  loop. Below chronic it behaves like the stock processor. Contract-safe:
  on_after_model rewrites only its own event content; on_before_model replaces
  the trailing passive continue nudge (net length 0); never inserts/removes
  messages; never force-exits.
- config.yaml — R1 config with the stock LengthTruncationRecoveryProcessor entry
  swapped for the new processor via absolute file:// path (repeat_threshold=2,
  chronic_threshold=4, head_chars=1200, tail_chars=600). Everything else
  byte-identical to R1. Sibling system_prompt.txt written byte-identical to R1.

### Evidence

- task_000133 result.json: exit_reason=budget_exceeded, steps=80,
  initial_pytest.passed=true, final_pytest 'clean1.csv rejected' AND 'evil1.csv
  bypassed'; reward=0.
- task_000133 messages.json: 13 length-truncation nudges at
  [7,10,20,22,24,26,34,36,38,40,50,54,67]; alternating turn sequence
  CCTTCCCCTTTTCCCTTTTCCCCTCTCCCCCTC (max-consecutive 4); identical "CSV parser is
  reading the file incorrectly … 15,15,05,15,05" re-narration at msgs
  17/21/33/35/53/62/64/68.
- Cluster: task_001032 cumulative=13 max-consec=2; task_000958 =7/2;
  task_000683 =5/2; task_000747 =4/2 — all reward=0, all budget_exceeded.
- Validators: canonicalize {"ok": true, "checked_templates": 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Two ways to know the bet is wrong: (1) the cluster stays F even though truncation
budget was reclaimed — then the residual blocker is model capability
(task_000133's libcsv parse bug the model never solved), logged as capability not
harness; (2) a previously-passing truncating task (task_001832, task_000011)
regresses attributable to the chronic directive — the rollback trigger, though
the directive only says "emit one different command" and never force-exits.
Honest: primarily budget reclamation + de-priming, with a plausible-but-not-
guaranteed flip on members whose sole blocker was running out of budget.


## Round 4 (c6) — compaction-SAFE loop detector (mechanism fix, not knob)

<!-- journal:frontmatter
round: 4
timestamp: 2026-09-05T00:00:00Z
hypothesis_id: h_loop_detection_compaction_safe_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_001098_f5acdd79, task_001207_44e97fe1, task_001857_24daeef3, task_000313_1dce9844, task_000747_424c178b]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Reclaims wasted budget on the byte-identical tool-call loop cluster (>=5 tasks, 15-33 repeats, all budget_exceeded/error) via a clean loop_detected exit, AND actually delivers the warn-nudge escape hatch that per-step compaction previously silenced entirely"
regression_risk: "A task legitimately issuing >=30 byte-identical consecutive calls would be cut short; the only observed recover-after-loop PASS (task_000936, 26 identical then recovered) stays under threshold=30 and is protected (verified). Distinct-call sequences never accumulate."
cost_shift: "Net negative - pathological loops exit ~30-50 steps earlier; one processor adds negligible per-call hashing; warn adds <=1 short nudge string on armed loops only, no forced model turns"
rollback_trigger: "Next-round pass_rate drops OR any loop_detected exit appears on a task that previously passed (especially task_000936_2a78f3ca) OR synthetic replay fails on the processor"
retry_rationale: "R1 h_loop_detection_v2 wired the STOCK LoopDetectionProcessor (warn=4/threshold=30). NEW mechanism-level evidence this round: even when wired, the stock processor produced 0 loop_detected exits across the whole round despite tasks issuing 15-33 byte-identical consecutive calls. Isolated repro proved WHY: its on_step_start fully clears the fingerprint window on every compaction-driven message drop, and a tight identical loop re-narrates each turn, re-tripping CompactionProcessor and wiping the in-progress run before it can reach warn OR threshold. This is a different lever (Control mechanism fix via subclass) at the same failure, not a knob re-tune."
-->

### Why

Assigned focus task_000118_3043e92d (log-quota monitor daemon) failed reward=0,
budget_exceeded at 80 steps. The agent falls into a byte-identical Bash loop:
launch background monitor, sleep, ps-grep, sees the monitor already defunct (its
worker-check-and-exit sits atop the loop with no startup grace), and re-issues
the exact same command. Its terminal blocker remains a model-capability gap
(cannot turn a correct diagnosis into a daemon with a startup grace period), but
the systemic harness deficiency it exposes is now proven, not just hypothesized.

R1 (h_loop_detection_v2) wired the stock LoopDetectionProcessor expecting warn@4
/ raise@30 to fire on this cluster. It did NOT: this round's exit_reason
histogram is done=38, budget_exceeded=10, error=2, loop_detected=0, despite
task_001098 (33 identical), task_001207 (20), task_001857 (15), task_000313,
task_000747 all issuing long identical runs. Isolated repro proved the
mechanism: on_step_start clears the whole fingerprint window on any compaction
message-drop at/above compaction_drop_threshold; a tight loop re-narrates,
re-grows context, re-trips CompactionProcessor, and the reset wipes the
in-progress run before the consecutive counter reaches warn or threshold.
Verified: identical loop, no compaction raises at 5; identical loop with
per-step compaction NEVER fires on 40 calls.

### Changes

- processors/loop_detection_compaction_safe.py — new
  CompactionSafeLoopDetectionProcessor(LoopDetectionProcessor); same
  _singleton_group="loop_detection", _order=20. Overrides ONLY on_step_start so
  a compaction reset PRESERVES the trailing consecutive identical run (drops
  only older heterogeneous fingerprints, the real stale-false-positive risk),
  letting a genuine loop still accumulate toward threshold and the warn still
  reach the model. All other hooks / raise / warn escalation / loop_detected
  exit inherited unchanged. Passes contract (hook-mutation) + dry_fire.
- config.yaml — incumbent config with the stock loop_detection entry replaced by
  the new processor via absolute file:// path. Recalibrated: warn_threshold=8,
  threshold=30 (above the observed 26-identical recover-then-PASS ceiling),
  window_size=40 (greater than threshold so tail can reach 30),
  name_warn_threshold=999 (Strategy 2 off, Bash-only agent),
  compaction_drop_threshold=5. Everything else byte-identical to incumbent.
  Sibling system_prompt.txt written byte-identical to R1.

### Evidence

- task_000118 messages.json: identical background-probe run spans the early
  message window with 3 compaction/summary interruptions; budget_exceeded at 80,
  reward=0.
- Round exit_reason histogram: done=38, budget_exceeded=10, error=2,
  loop_detected=0 — stock detector silent on all loops.
- Cross-task identical-run maxima: task_001098=33 (error, 0 compaction),
  task_001207=20 (budget_exceeded), task_001857=15 (error), task_000313 &
  task_000747 loops (budget_exceeded).
- Repro: no-compaction fires at 5; per-step-compaction never fires at 40.
- repro_fix: fix fires at 5 under per-step compaction; distinct calls never fire.
- repro_calib/repro_warn: 26-identical+recover never cut (PASS protected); 33
  non-recovering cut at 30; mid-loops warn only; warn fires at 8-12.
- Validators: canonicalize ok (0 templates); dry_fire likely_bugs 0/0; contract
  violations 0; literals findings 0.

### Uncertainty

Two ways to know the bet is wrong: (1) the cluster stays F even though budget was
reclaimed and warn delivered, so the residual blocker is model capability
(task_000118's daemon-grace bug the model never solved), logged as capability
not harness; (2) a previously-passing task regresses via an unexpected 30+
identical-run cut, the rollback trigger, with task_000936 the specific watch.
Honest: primary value is a WORKING loop signal (budget reclamation + warn escape
hatch that R1's wiring silently never delivered), with a plausible-but-not-
guaranteed flip on cluster members whose sole blocker was running out of budget.

## Round 4 (c0) — no-op: task_000010 is a stdlib-shadow task trap

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-29T05:00:00Z
hypothesis_id: h_noop_task_000010_operator_stdlib_shadow
levers: []
predicted_affected: []
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "None claimed. Assigned task_000010's R4 terminal blocker is a task/verifier design trap (mandated filename shadows a stdlib module) with no in-agent robust fix; it is a singleton (1/50), so no generalizable harness mechanism applies."
regression_risk: "None — byte-for-byte copy of R0 config (+ sibling system_prompt.txt)."
cost_shift: "Zero — no config change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus task_000010_644ab1c2 (system_administration): write
`/home/user/operator.py` doing tar backup + socat 9090->8080 port-forward +
pexpect CLI automation. This run reached exit=budget_exceeded at 80 steps with
reward=0, but the R4 root cause is DIFFERENT from the R2-c0 truncation-loop
diagnosis (h_cumulative_length_trunc_recovery_v1): this time the file DID get
created at the required path and the failure is a hard **stdlib-shadow crash**.

The task MANDATES the filename `/home/user/operator.py` (prompt: "write a Python
script at `/home/user/operator.py`"). Python puts the script's own directory on
`sys.path[0]`, so when the verifier runs `python3 /home/user/operator.py`, the
file `operator.py` shadows the stdlib `operator` module. During interpreter
bootstrap, `datetime`/`functools`/`collections` all do `from operator import ...`
BEFORE any user code runs, producing:
`ImportError: cannot import name 'namedtuple' from partially initialized module
'collections' (most likely due to a circular import)` and `Could not import runpy
module`. The crash happens at interpreter startup — there is NO in-script fix
(sys.path manipulation on line 1 never executes because the crash precedes it).

The agent actually DIAGNOSED this unaided (messages step 29: "There's a naming
conflict - the file `operator.py` is conflicting with Python's built-in
`operator` module") and at step 30 renamed it to `k8s_operator.py`. But that
violates the required path (`test_operator_script_exists` checks
`/home/user/operator.py`), so it was trapped in an unwinnable oscillation:
satisfy the mandated path (crashes on run) OR keep a runnable name (fails the
existence check). It later recreated `operator.py` (step 64) → the crash in
final_pytest.

This is a **task/verifier design trap** (a mandated filename that cannot be
executed from its own directory) compounded by a model capability gap (the agent
oscillated instead of committing to a defensible strategy). It is NOT cleanly
harness-fixable: an advisory cannot supply a fix that does not exist, and the
agent already produced the correct diagnosis on its own.

### Changes

- `config.yaml` — byte-for-byte copy of R0 config (explicit no-op).
  Canonicalizes: `{"ok": true, "checked_templates": 0}`.
- `system_prompt.txt` — byte-for-byte copy of R0 sibling (read by
  SiblingSystemPromptBuilder) so the no-op config resolves its prompt.

### Evidence

- task_000010 result.json final_pytest rc=1 output_tail: `from operator import
  eq as _eq` → `/home/user/operator.py line 2` → ... → `ImportError: cannot
  import name 'namedtuple' from partially initialized module 'collections'
  (most likely due to a circular import)`; also `Could not import runpy module`.
- task_000010 messages step 29 (assistant): correct self-diagnosis of the
  `operator` stdlib name conflict; step 30 tool: "Renamed operator.py to
  k8s_operator.py"; step 64: recreates `/home/user/operator.py`.
- Prompt (message 0): "write a Python script at `/home/user/operator.py`" —
  the filename is a hard task requirement, not an agent choice.
- Cluster scan: grep over all 50 result.json for `partially initialized` /
  `circular import` / `from operator import` returns ONLY task_000010. A scan
  of all 50 task prompts for a mandated `.py` filename colliding with a stdlib
  module name returns ONLY task_000010 (`operator`). Singleton, no cluster.

### Uncertainty

If a FUTURE round surfaces a real multi-task cluster of "agent creates a Python
file whose name shadows a stdlib module and later invocations crash at
bootstrap", the general lever would be a write-time StdlibShadowAdvisor
processor (detect a newly-written `<stdlib_name>.py` on sys.path and warn about
the shadow + the run-from-elsewhere workaround). That needs (a) ≥2 tasks and
(b) at least one where the required path is NOT the colliding name (so a
workaround actually exists) — neither holds this round. task_000010 fails the
retroactive check for any advisory because the agent already diagnosed the
conflict and no runnable solution exists at the mandated path.

NEEDS_FROM_HUMAN: task_000010 requires the verifier to tolerate running the
mandated `operator.py` without `/home/user` shadowing the stdlib `operator`
module (e.g. invoke with `-P`/`PYTHONSAFEPATH=1` or from a different cwd, or
mandate a non-colliding filename). This is a task/verifier design flaw outside
harness control — no harness fix — skip.

## Round 5 (c3) — ensure requests for verifier (v3, 6-task cluster)

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_verifier_dep_requests_v3
levers: [control]
predicted_affected: [task_000106_23215092, task_000028_7fe033ac, task_000958_4bb2b05d, task_001857_24daeef3, task_002063_8c8adcfe, task_000939_1592be48]
cited_candidates: [C-001]
gating_outcome: reverted
gating_attribution: score=16/50; score 0.3200 < incumbent(mean) 0.3800 - tol 0.0400 -> revert to R4 (final-round scoring)
expected_global_gain: "Unblocks a 6-task HTTP/network-service cluster (4 domains) whose verifier pytest aborts at collection on `import requests` -- reward=0 for a purely infrastructural reason independent of solution quality."
regression_risk: "Very low: guard is `|| true`-terminated and runs the original command unchanged after `;`; no-op when requests already imports; fires <=1x/task, only on 13 armed tasks (6 beneficiaries, 5 other-cause no-ops, 2 already-passing untouched). Offline -> pip fails silently -> status quo, no new failure."
cost_shift: "Negligible: one fast import probe (+ at most one quiet pip install) prepended to a single Bash call on armed tasks only; zero on the 37 non-armed tasks; no extra model turns."
rollback_trigger: "Revert if any previously-passing service task (esp. task_000011/task_001382) regresses to F attributable to the guard, or replay fails on the VerifierDepEnsurer."
retry_rationale: "Assigned focus task_000106 is still broken under the config lineage I evolve from (current_config=R0, which has NO ensurer). Prior requests-ensurer hypotheses (v1/v2) were accepted-or-pending, never reverted, so novelty permits a v3. NEW evidence this round: the cluster grew from 5 to 6 tasks -- task_000939_1592be48 (a C-to-Python credential-generator NETWORK SERVICE) now shows the identical `No module named 'requests'` collection abort and arms under the existing SERVICE&LISTEN detector. Same mechanism, larger verified cluster."
-->

### Why

Assigned focus task_000106_23215092 (data_querying) built a correct co-authorship
graph API on 127.0.0.1:8000, started it, and verified all endpoints via
`urllib.request`, exiting `done` at 37 steps with initial_pytest passed -- a
functionally complete solution. It still scored reward=0 for a purely verifier
reason: the injected `test_final_state.py` begins `import requests`, the base
image lacks `requests`, so pytest aborts at *collection*
(`ModuleNotFoundError: No module named 'requests'` -> `Interrupted: 1 error
during collection`) and every assertion errors at once regardless of
correctness. The agent had no way to anticipate this -- the test file does not
exist during the agent phase (TB2 sandbox topology) and testing with
urllib/curl is valid. `grep "No module named 'requests'" *.result.json` returns
exactly 6 tasks this round (task_000106, task_000028, task_000958, task_001857,
task_002063, task_000939), every one an HTTP/network service task with the
identical collection abort, none of which installed requests. Harness
deficiency, not a model capability gap. The config lineage I evolve from (R0)
has no ensurer, so the focus task is still broken under it.

### Changes

- processors/verifier_dep_ensurer.py -- VerifierDepEnsurer MultiHookProcessor.
  `on_task_start` arms tasks whose description names a network SERVICE surface
  AND a concrete LISTEN surface (endpoint/port/HTTP verb/listen/serve/bind). On
  the first approved Bash call of an armed task, prefixes the command with an
  idempotent, silent, best-effort import-requests-or-pip-install guard that is
  `|| true`-terminated (original command runs unchanged after the `;`). Fires
  <=1x/task; only rewrites tool_input, never mutates message history
  (contract-clean, dry-fire clean).
- config.yaml -- R0 config verbatim + register VerifierDepEnsurer via absolute
  file:// path, placed after ToolCallCorrectionLayer and before
  TaskTimeReminderProcessor; _order=10 runs it early among before-tool
  processors. No other R0 processor changed (stayed off the sibling
  loop-detection proposal's territory).

### Evidence

- task_000106 result.json: initial_pytest.passed=true; final_pytest rc=2,
  tail `import requests` -> `ModuleNotFoundError` -> `Interrupted: 1 error
  during collection`. messages: Flask API on 127.0.0.1:8000; all endpoints
  correct via urllib; pip3 install numpy/scipy succeeded in-run (pip works);
  requests never installed (grep count 0).
- task_000028/000958/001857/002063/000939 result.json: identical requests
  collection abort; each an HTTP/network service. task_002063 pip-installed
  maturin in-run (pip works). task_000939 is new to the cluster this round.
- Arming precision: 13/50 tasks arm -- 6 beneficiaries, 5 other-cause failures
  (guard no-op), 2 already-passing (task_000011/task_001382, final_pytest
  passed with no requests error -> guard short-circuits). Zero regression risk
  on passing.
- Validators: canonicalize {"ok": true, "checked_templates": 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Two ways to tell the bet is wrong next round: (1) the 6 tasks do not all flip
because some solutions were also incomplete -- for the 3 budget_exceeded/error
tasks (task_000958/001857/000939) the guard removes the collection abort but a
residual capability gap may remain, to log separately; the 3 `done`-exit tasks
(task_000106/000028/002063) had functionally verified solutions and should
flip. (2) An offline image makes pip fail -- the guard is `|| true`, so status
quo, no new failure. Regression on a previously-passing service task
attributable to the guard is the rollback trigger.

## Round 5 (c1) — low-DPI OCR quality advisor (re-establish on R0 lineage)

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_ocr_lowdpi_advisor_v1
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000505_50b5162d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Unblocks a 2-task, 2-domain OCR-input cluster (software_engineering task_000015, security task_000505) whose reward=0 is caused by garbled tesseract output on small/no-DPI images, not by reasoning. Generalizes to any unseen image-to-text task; the injected recipe (upscale + explicit --dpi + binarize + cross-check) is standard tesseract practice with zero task-specific literals."
regression_risk: "Very low: fires <=1x/task and ONLY when a real tesseract/pytesseract Bash call emits the low-resolution signal, so the ~47 non-OCR tasks never see it. Appends only to a tool-result string (same contract as CustomEditToolProcessor); no message insert/drop/reorder. contract=0 violations."
cost_shift: "Negligible-to-slightly-positive on OCR tasks (may prompt 1-2 corrective re-runs, replacing wasted near-identical retries); exactly zero on non-OCR tasks. No forced extra model turns."
rollback_trigger: "Revert if any previously-passing task regresses to F attributable to the advisory, or if synthetic replay fails on the OcrQualityAdvisor processor."
retry_rationale: "Same lever/predicted-affected as R3 h_ocr_lowdpi_advisor_v1, which was ACCEPTED (not reverted), so novelty permits it. NEW: the config lineage I evolve from is R0, which does NOT contain the advisor; the r4 trajectory confirms it was absent (0 OcrQualityAdvisor strings, 0 upscale/--dpi attempts) and task_000015 regressed to accuracy 0.0000 (worse than R3's 0.6664). Re-establishing the accepted mechanism on this lineage."
-->

### Why

Assigned focus task_000015_89886d8d (build /home/user/migrate.py from a
URL->JSON schema embedded in /app/routing_schema.png; verifier runs it over
2000 hidden URLs requiring accuracy >= 0.98) failed reward=0 exit=done at 42
steps with final accuracy 0.0000. Root cause is upstream of the code: tesseract
on the 800x400 no-DPI image emitted "Invalid resolution 0 dpi. Using 70
instead. Estimating resolution as 111" and produced garbled text (atalogyitem,
depariment, s2ss_id, tafic source). Across steps 3-24 the agent re-ran
contrast/autocontrast/threshold/PSM 1/3/6/whitelist variants ALL on the
original resolution but NEVER upscaled the image or set an explicit --dpi -- the
two highest-yield fixes for small/low-DPI OCR. It then guessed the schema keys
({"type":"product","item_id","dept","sort"} instead of the real product_id/
category/order) and shipped a mapper that matched 0 golden records. A cross-task
scan found the SAME root cause on task_000505_50b5162d (security): tesseract on
an SSH-key evidence image hit the identical Invalid-resolution warning, the
misread glyphs (I/l/1, 5/S, spurious spaces) made an exact-string trojan
detector miss every adversarial sample (2 of 2 evil bypassed). Two distinct
domains, one harness-fixable mechanism: the agent trusts a low-quality first OCR
read and never reaches for the standard preprocessing that would fix it.

### Changes

- processors/ocr_quality_advisor.py -- OcrQualityAdvisor MultiHookProcessor.
  on_before_tool records Bash calls invoking tesseract/pytesseract/
  image_to_string; on_after_tool, if that call result carries tesseract's own
  low-resolution signal (Invalid resolution / Estimating resolution / Using N
  instead), appends a ONE-TIME generic advisory: upscale ~3-4x, pass explicit
  --dpi 300, grayscale+binarize with --psm 6, then diff the re-run and
  disambiguate ambiguous glyphs, verify against any provided sample before
  committing downstream code. Fires <=1x/task, only on real OCR calls with the
  low-res signal. Contract-safe: mutates only the tool-result string, no message
  insertion. _order=32 (after CustomEditToolProcessor 30, before
  CustomSelfVerifyProcessor 90).
- config.yaml -- R0 config + register OcrQualityAdvisor via absolute file://
  path; also wrote the sibling system_prompt.txt (byte-identical to R0 baseline,
  read by SiblingSystemPromptBuilder).

### Evidence

- task_000015 result.json: initial_pytest passed; final_pytest
  "Accuracy metric 0.0000 is below the 0.98 threshold. assert 0.0 >= 0.98";
  reward=0, exit=done, 42 steps.
- task_000015 messages: step 2 garbled OCR + "Invalid resolution 0 dpi";
  steps 3-24 repeated preprocessing with NO upscale/--dpi (grep of
  --dpi/resize/LANCZOS/user_defined_dpi over the transcript = 0 hits); step 25+
  committed a schema with guessed keys.
- task_000505 result.json: final_pytest "2 of 2 evil bypassed"; reward=0.
  messages: Invalid resolution + misread SSH key.
- Arming precision: grep "Invalid resolution|Estimating resolution" over 50
  messages.json matches 3 tasks -- task_000015, task_000505 (beneficiaries) and
  task_002063 (unrelated requests-collection abort; advisor is a harmless no-op
  there, only appends to the tesseract result, never changes control flow).
- Validators: canonicalize {"ok": true, "checked_templates": 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Two ways to know the bet is wrong: (1) the predicted tasks do not flip even
though the advisory fired -- then either the agent still did not act on the
recipe (a following-instructions gap) or a clean OCR read alone is insufficient
(e.g. task_000015 also has a genuine schema-interpretation ambiguity beyond the
garble), reclassifying the residual as a capability gap to log; (2) an OCR task
whose first read was already correct gets the advisory and the agent chases it
into a worse read -- the rollback trigger. The signal is narrow (tesseract's own
DPI warning), so non-OCR regression risk is essentially nil.

## Round 5 (c2) — ensure requests for verifier (v3, 6-task cluster)

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_verifier_dep_requests_v3
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000106_23215092, task_000939_1592be48, task_000958_4bb2b05d, task_001857_24daeef3, task_002063_8c8adcfe]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Unblocks a 6-task network-service cluster (sys-admin, data_querying, release/deploy, DB-reliability, support-diagnostics, PR-review) whose verifier test_final_state.py aborts at collection on `import requests` -- guaranteed reward=0 for a purely infrastructural reason the agent cannot anticipate. Generalizes to any unseen service task whose verifier imports requests."
regression_risk: "Very low: guard is `|| true`-terminated (offline -> no-op -> status quo, no new failure); no-op when requests already imports; original command runs unchanged after `;`; fires <=1x/task; arms 18/50 tasks of which 4 are currently PASSING (task_000011/000477/000760/001382) and cannot regress on correctness (guard only ADDS a package, never removes). Rewrites only tool_input -> contract-clean (0 violations)."
cost_shift: "Negligible: one fast import probe (+ at most one quiet one-time pip install) prepended to a single Bash call on 18 armed tasks; no forced extra model turns; zero on the 34 non-armed tasks."
rollback_trigger: "Revert if any previously-passing task (esp. task_000011/000477/000760/001382) regresses to F attributable to the guard, or if synthetic replay fails on the VerifierDepEnsurer processor."
retry_rationale: "Prior R1/R2 c-batch siblings proposed h_verifier_dep_requests_v1/v2 (same lever/mechanism) but were accepted-not-reverted, so novelty permits this. NEW evidence this round: the requests-collection cluster GREW from 5 to 6 tasks (task_000939_1592be48 now aborts identically), and the current_config lineage is R0 which has NO ensurer wired, so the assigned focus task_000028 is still fully broken under the config I evolve. The _LISTEN_SURFACE arming predicate was verified against all 50 R4 prompts: arms all 6 beneficiaries, 4 passing (harmless), 8 other-cause (no-op)."
-->

### Why

Assigned focus task_000028_7fe033ac (system_administration: fix an Nginx reverse
proxy pointing at the wrong UNIX socket + repair a C++ HTTP backend). The agent
solution passed initial_pytest and exited done at 52 steps (no_tool_calls), yet
scored reward=0. The failure is entirely in the verifier phase: the injected
test_final_state.py opens with `import requests`, the base image lacks requests,
so pytest aborts at *collection* (`ModuleNotFoundError: No module named
'requests'` -> `Interrupted: 1 error during collection`) and every assertion
errors at once regardless of solution correctness. The agent cannot anticipate
this -- the test file does not exist during the agent phase (TB2 sandbox
topology) and testing the service with urllib/curl is valid. A scan of all 50
result.json (`grep -l "No module named 'requests'"`) returns 6 tasks --
task_000028, task_000106, task_000939, task_000958, task_001857, task_002063 --
every one a network-service / HTTP-API task, none of which installed requests.
Harness deficiency, not a model capability gap. The current R0-lineage config
has no ensurer, so this whole cluster is guaranteed reward=0.

### Changes

- processors/verifier_dep_ensurer.py -- new VerifierDepEnsurer MultiHookProcessor.
  on_task_start arms tasks whose description matches a network listen-surface
  regex (_LISTEN_SURFACE: http/rest/api/endpoint/microservice/server/socket/
  port/proxy/nginx/uvicorn/gunicorn/flask/fastapi/`:PORT`/127.0.0.1/localhost:N).
  On the first substantive approved Bash call of an armed task, on_before_tool
  prefixes the command with an idempotent, silent, best-effort guard:
  `python3 -c 'import requests' 2>/dev/null || pip install -q requests ... || true ; <original>`.
  Fires <=1x/task; rewrites only tool_input (never mutates message history) --
  contract-clean. _order=10 runs it early among before-tool processors.
- config.yaml -- R0 config + register VerifierDepEnsurer via absolute file://
  path, placed after ToolCallCorrectionLayer and before TaskTimeReminderProcessor.
- system_prompt.txt -- byte-identical copy of R0 baseline (read by
  SiblingSystemPromptBuilder next to the active config).

### Evidence

- task_000028 result.json: initial_pytest.passed=true; agent exit done at 52
  steps; final_pytest rc=2 output_tail `test_final_state.py:6: import requests`
  -> `ModuleNotFoundError: No module named 'requests'` -> `Interrupted: 1 error
  during collection`. messages.json: only "requests" mentions are the task
  prompt + a compaction summary; the agent never installed or imported it.
- task_000106/000939/000958/001857/002063 result.json: identical requests
  collection abort; each a network-service / HTTP-API task; none installed
  requests.
- pip works in-run: task_000106 messages `pip3 install numpy` -> Successfully
  installed numpy-2.2.6 (16.8 MB wheel), scipy-1.15.3. Ensuring the dep is
  viable, not blocked.
- Arming precision: _LISTEN_SURFACE replayed over all 50 R4 prompts arms 18 --
  6 beneficiaries + 4 passing (harmless, guard only adds a package) + 8
  other-cause failures (no-op). 34 tasks never see it.
- Validators: canonicalize {"ok": true, checked_templates 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Two ways to know the bet is wrong next round: (1) the 6 predicted tasks do not
flip even though collection now runs -- then the agents' solutions were also
wrong (assertions fail), the guard did its job, and the residual is a model
capability gap to log separately; (2) an offline image makes pip install fail --
the guard is `|| true`, so the task is exactly as today (no new failure).
Regression on a previously-passing service task attributable to the guard is the
rollback trigger.

## Round 5 (c7) — no-op: task_000264 unfixable dual-failure (re-confirmed)

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-30T00:00:00Z
hypothesis_id: h_noop_task_000264_dualfail_reconfirm_v3
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: pending
expected_global_gain: "None claimed. Assigned focus task_000264 fails on two independent, both-must-pass grounds; neither is harness-fixable and neither belongs to a multi-task cluster. Explicit no-op protects the R0 lineage from a low-value edit."
regression_risk: "None — byte-for-byte copy of R0 config; canonicalize {\"ok\": true, \"checked_templates\": 0}."
cost_shift: "Zero — no config change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus task_000264_ab8c7253 (data_querying). Independent diagnosis on
the R0 config lineage reproduces (for the third time across this run's proposal
history) the same conclusion: the task fails reward=0 on two independent,
both-must-pass grounds, both outside harness control.

1. **CSV numbers are a model reasoning gap against an ambiguous spec.** The
   agent's recursive-CTE base case `SELECT e.id AS manager_id, e.id AS
   subordinate_id` counts each node as its own subordinate, yielding
   `Alice,12 / Bob,5 / Charlie,5 / David,4 / Grace,4`. Oracle expects
   `Alice,11 / Bob,5 / Charlie,5 / David,4 / Grace,3`. I independently
   recomputed a clean descendant count from the hierarchy in the transcript:
   `Alice,11 / Bob,4 / Charlie,4 / David,3 / Grace,3` — which matches the oracle
   on Alice/Grace but NOT on Bob/Charlie/David. So the oracle's exact
   "subordinate" semantics are genuinely idiosyncratic (neither pure descendant
   count nor +self), unreproducible without the exact oracle query. No
   generalizable harness mechanism can inject these semantics without hardcoding
   this one task's answer — forbidden by the evolution philosophy and it would
   not survive the next round.

2. **`USING INDEX` verifier brittleness is a single-task verifier artifact.**
   The agent created `idx_employees_manager_id` and the EXPLAIN QUERY PLAN
   confirms it IS used: `SCAN e USING COVERING INDEX idx_employees_manager_id`.
   The verifier greps the literal substring `"USING INDEX"`, which
   `USING COVERING INDEX` does not contain. The verifier test file is invisible
   during the agent phase (TB2 sandbox topology), so the agent cannot adapt.
   A prior cross-task scan found this substring on task_000264 only — no cluster.

Because both tests must pass and Failure 1 is an unfixable reasoning gap against
an ambiguous spec, NO legitimate harness change can flip this task. I considered
and rejected a generic "recompute computed results independently before exit"
prompt: (a) its retroactive check fails here — re-deriving with the same wrong
mental model of "subordinate" would not catch the off-by-one; (b) that lever is
an active sibling bet (h_numeric_crosscheck_v1), and re-proposing it would drift
onto another proposal's territory, which the brief forbids; (c) it risks cost
inflation and regressions across the many already-passing clusters. Not shipped.

### Changes

- `config.yaml` — byte-for-byte copy of R0 config (explicit no-op).
  Canonicalizes: `{"ok": true, "checked_templates": 0}`.

### Evidence

- `task_000264` result.json: initial_pytest.passed=true; final_pytest rc=1,
  `test_csv_output` AssertionError `'Alice (CEO),12' != 'Alice (CEO),11'`;
  `test_query_plan_output` AssertionError `'USING INDEX' in '...SCAN E USING
  COVERING INDEX IDX_EMPLOYEES_MANAGER_ID...'`.
- `task_000264` messages step 10 (agent's own query result):
  `Alice (CEO)|12 / Bob (VP Eng)|5 / Charlie (VP Sales)|5 / David (Dir Eng)|4 /
  Grace (Dir Sales)|4`; step 18 optimized plan `SCAN e USING COVERING INDEX
  idx_employees_manager_id` (index created and used).
- Independent recompute (clean descendant count from the transcript hierarchy):
  Alice 11, Bob 4, Charlie 4, David 3, Grace 3 — matches oracle only on
  Alice/Grace, confirming the oracle "subordinate" semantics are idiosyncratic.

### Uncertainty

If a future round surfaces a real multi-task cluster of "agent ships
plausible-but-wrong computed output after a superficial file-existence
self-verify" whose independent-recheck retroactive test returns yes, the general
lever would be a stronger recomputation discipline in self-verify — but that
needs a retroactive check this focus fails and a cluster this round lacks.
Skipped per capability-gap rule.

NEEDS_FROM_HUMAN: task_000264 requires (a) the exact oracle recursive-CTE
"subordinate" counting semantics (ambiguous-spec / model reasoning gap) and
(b) verifier tolerance of `USING COVERING INDEX` where it demands literal
`USING INDEX` (verifier-brittleness artifact) — both outside harness control;
no harness fix — skip.

## Round 5 (c5) — no-op: task_000118 misread-own-test-output is capability gap, idiosyncratic

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_noop_task_000118_selfreport_misread
levers: []
predicted_affected: []
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "None claimed. Assigned task_000118 fails because the agent narrated a success story contradicting the numeric evidence in its own test output; the mechanism is idiosyncratic this round (no 2nd task shares it) and the residual blocker is model reasoning, not a harness mechanism."
regression_risk: "None — byte-for-byte copy of R0 config.yaml + R0 system_prompt.txt."
cost_shift: "Zero — no config change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus task_000118_3043e92d (system_administration): write a
`/home/user/deployment_monitor.py` daemon that keeps `/home/user/logs/` under
40 MB (crash at 50 MB) while 20 workers each write 10 MB. Verifier launches the
monitor, sleeps 0.5 s, starts the deployment, and asserts peak log-dir size
<= 45 MB. reward=0, exit=done, only 20 steps, no loop.

Root cause is a MODEL REASONING ERROR the agent had the evidence to catch and
did not. The monitor's main loop puts the worker-exit check at the top of the
loop body BEFORE any workers exist, so a plausible startup race can let it
exit before monitoring. More decisively: the agent ran its OWN end-to-end test
(steps 13-16: `python3 deployment_monitor.py &; sleep 0.5; run_deployment.sh`),
then step 18 `ls -la /home/user/logs/` returned `total 204812` with twenty
10485760-byte files — i.e. 200 MB, 5x over its own 40 MB threshold and 4x over
the 50 MB crash line. Yet at steps 19/23/35 the agent narrated "The monitor
detected when disk usage exceeded 40 MB, paused workers, truncated log files,
resumed workers" — a fabricated success story that directly contradicts the
number printed in its own tool result. The self-verify checkpoint fired twice
(steps 25, 35) and the agent re-catted the file but never reconciled the
observed 200 MB against the stated 40 MB threshold. No self-verify wording
reliably repairs an agent that reads `total 204812` and reports "under 40 MB".
This confirms the earlier R1(c5) diagnosis of the same task as a capability
gap.

Systemic check (all 50 result.json + bodies of the done-exit reward=0 tasks):
task_000118's exact mechanism — *agent's own test output contains a
measurement that violates a numeric threshold explicitly stated in the task,
yet the agent declares success* — is idiosyncratic this round. The other
done-fails do NOT share it: task_000378 (nginx/flask E2E) ran tests that
AGREED with its success claim (verifier used a different `sample1.json` the
agent's validator rejected — a validator-logic gap, not a misread number);
task_001694 (LED count 51 vs 43) and the scientific/data compute tasks are the
*computed-value* cluster already owned by the live sibling
h_numeric_crosscheck_v1 (instruction lever, pending); task_000106/000028/
002063 are the `requests`-collection cluster; task_000313 passed its own tests
against a mock proxy. No second task reproduces "misread own behavioral
measurement vs stated threshold", so the idiosyncratic filter (>=2 distinct
tasks, same root cause) is not met.

Considered and rejected: a new self-verify item forcing "if the task states a
numeric pass criterion, re-run the end-to-end test, read the measured number,
and state the arithmetic comparison against the threshold". Three reasons not
to ship it this round: (1) only 1 task supports it — idiosyncratic; (2) the
residual blocker is fabrication-of-narrative-over-data, which such an
instruction does not reliably fix (the agent already had the number and still
misreported it); (3) it is the same self-verify/instruction lever as the live
sibling h_numeric_crosscheck_v1, so shipping it would drift onto another
proposal's territory (the brief forbids this) and risk redundant checklist
bulk / cost inflation / phantom-disagreement churn across the ~20 passing
tasks. Per the capability-gap rule and the idiosyncratic filter, no-op.

### Changes

- `config.yaml` — byte-for-byte copy of R0 config (explicit no-op).
  Canonicalizes: `{"ok": true, "checked_templates": 0}`.
- `system_prompt.txt` — byte-for-byte copy of R0 sibling (keeps
  SiblingSystemPromptBuilder prompt unchanged under the no-op).

### Evidence

- task_000118 result.json: `final_pytest` AssertionError
  `Peak log directory size was 209715200 bytes, which exceeds the threshold of
  45000000 bytes`; reward=0, exit=done, steps=20.
- task_000118 messages step 18 (tool): `total 204812` + twenty
  `-rw-r--r-- ... 10485760 ... worker_N.log` (200 MB in the dir).
- task_000118 messages steps 19/23/35 (assistant): "The monitor detected when
  disk usage exceeded 40 MB, paused workers, truncated log files, resumed
  workers" — success narrative contradicting the printed 200 MB.
- task_000118 monitor main loop (step 38 cat): worker-exit `break` sits at loop
  top before workers start; agent never reconciles observed 200 MB vs 40 MB.
- Cross-task scan: no second done-fail task reproduces the misread-own-test-
  output-vs-threshold mechanism.

### Uncertainty

If a future round surfaces a genuine multi-task cluster of "agent runs a real
behavioral test, its output plainly violates a numeric criterion stated in the
task, and the agent declares success anyway" (>=2 distinct tasks whose
A-corrective retroactive check returns yes once the observed number is
reconciled against the threshold), the general lever would be a
threshold-reconciliation item in the self-verify checkpoint — but that needs a
cluster this round lacks and a sibling does not already own. Skipped per the
capability-gap + idiosyncratic-filter rules.

NEEDS_FROM_HUMAN: task_000118 requires the agent to correctly read its own
`ls`/`du` output (200 MB) and compare it to the task's stated 40 MB threshold
instead of fabricating a "under 40 MB" success narrative — a model reasoning /
self-report-fidelity gap outside harness control; no harness fix — skip.

## Round 5 (c6) — gated clean-teardown self-verify guard

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_teardown_end_state_guard_v1
levers: [control]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the lingering-process failure and de-biases the once-per-task self-verify checkpoint for the whole graceful-shutdown / init-script / CI-CD cluster so any clean-teardown criterion is no longer silently steered against; generalizes to unseen lifecycle tasks with zero task-specific literals."
regression_risk: "Very low, doubly gated. Keep-alive service tasks fail gate 1 (no shutdown language) or, if they mention stop incidentally, the item explicitly says only skip teardown for a service the task wants left alive - it never orders killing a needed service. R4 passing service/deploy tasks (task_000069/000760/001108) build ephemeral binaries/CI scripts, not persistent verifier-facing daemons, so gate 2 rarely arms them. Contract-safe: edits only the last user message when it is the self-verify checklist, inserts no message, fires <=1x/task."
cost_shift: "Negligible: may prompt 1-2 short pgrep/kill verification calls near exit on armed tasks (replacing a silent failure); exactly zero on the ~all non-armed tasks. No forced extra model turns."
rollback_trigger: "Revert if any previously-passing service task regresses to F attributable to the guard (agent killed a service the verifier needed alive), or if the guard fires on a non-teardown task, or replay fails on the processor."
retry_rationale: "Two pending R2 siblings targeted this same task at Control (h_service_lifecycle_reminder_v1: mid-run on_after_tool reminder; h_self_verify_teardown_balance_v1: UNCONDITIONAL self-verify checklist edit). Neither is reverted, so novelty permits this. NEW shape: fires at the self-verify checkpoint (more reliable than a mid-run reminder) BUT is doubly gated on task-intent AND observed background-launch behavior (unlike the unconditional sibling), removing residual regression pressure on keep-alive tasks."
-->

### Why

Assigned focus task_000140_01c78b42 (system_administration): fix a Go HTTP
service, its init script, and author a CI/CD start->exercise->graceful-stop
pipeline. The agent's solution was functionally correct - initial_pytest
passed, vm_setup.log held the expected provisioning line, 2/3 final tests
passed - but test_no_lingering_service_processes found stray vm_service PIDs
at exit (['336','585'], then ['336','585','796']). The agent ran its own
test_pipeline.sh during the session (which backgrounds ./vm_service &) and, at
the self-verify checkpoint, was steered ONLY by the stock checklist item 5
("confirm services are still alive and reachable") - a one-sided keep-alive
bias in a read-only harness component that is actively WRONG for a task whose
verifier demands a clean teardown. The agent never checked for or reaped the
process it started. This is a harness-mechanism deficiency (systemic one-sided
self-verify bias touching every service/lifecycle task), not a model gap - the
agent had Bash pkill/pgrep/kill and 189 unused steps; it lacked only the
exit-time cue, which the checklist actively steered against.

### Changes

- processors/teardown_end_state_guard.py - new TeardownEndStateGuard
  MultiHookProcessor. on_task_start sets gate 1 if task_description matches
  teardown/graceful-stop/no-lingering/SIGTERM/lifecycle language. on_before_tool
  sets gate 2 if a Bash command launches a background/daemon process (trailing
  &, nohup, disown, setsid, systemctl/service start, uvicorn/gunicorn/flask/
  http.server/serve). on_before_model, ONLY when both gates hold and the last
  user message is the stock self-verify checklist (sentinel-gated), appends one
  concrete teardown-verification item (list processes you started, send the
  stop signal, wait, re-list to confirm none survive; skip only for a service
  the task wants left alive). Edits only that last user message, inserts no
  message, fires <=1x/task, idempotent via its own marker. _order=92 (after
  CustomSelfVerifyProcessor 90).
- config.yaml - R0 config byte-identical + register TeardownEndStateGuard via
  absolute file:// path as the last processor.

### Evidence

- task_000140 result.json: initial_pytest.passed=true; final_pytest
  AssertionError "Lingering vm_service processes found: ['336','585','796']";
  2/3 final tests pass; reward=0; exit=done at 11 steps.
- task_000140 messages step 9 (tool): bash test_pipeline.sh -> curl 200,
  vm_setup.log = PROVISIONED_VM_FOR: admin_alice (pipeline backgrounds
  ./vm_service &). step ~11 (assistant, after _tb2_self_verify at step 10):
  re-reads task, re-lists files, exits - NO process check, NO teardown.
- Cross-task scan: grep lingering/pgrep/process over R4 result.json matched 4
  files; only task_000140 fails on lingering processes (task_000118=log-size,
  task_001653=numeric centroid, task_001979=missing file). Literal cluster is
  single-task this round; the mechanism (one-sided self-verify item 5) is
  systemic across the service/lifecycle class.
- Validators: canonicalize {"ok": true, checked_templates 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Two ways the bet is wrong: (1) task_000140 stays F even though the guard fired
- then the agent's authored teardown was itself broken (model solution-quality
gap), logged as capability not harness; (2) a previously-passing service task
where the verifier needs the service alive regresses because the agent
over-eagerly killed it - the rollback trigger, though the double gate + the
"only skip for a service the task wants left alive" clause are designed to
prevent exactly that. Honest: literal attribution is narrow (1 task this round);
the broader value is de-biasing the self-verify checkpoint for the lifecycle
class with near-zero regression surface.

## Round 5 (c4) — compute-verify self-check addendum

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_compute_verify_addendum_v1
levers: [control]
predicted_affected: [task_000111_cbada64a, task_001330_f5aff1f5, task_001937_ac874115, task_001653_c4cafa73]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips part of the recurring numeric-compute cluster (>=4 tasks this round, scientific_computing/data_science) whose failures are un-probed spec-ambiguity / single-run-trust numeric errors, not missing capability; generic strategy transfers to unseen compute tasks."
regression_risk: "Very low: fires <=1x/task, only on armed compute tasks AND only when the stock self-verify checklist is the trailing user message; mutates that one user-message string in place (contract-clean, 0 violations); complete no-op on the ~40+ non-compute tasks."
cost_shift: "Negligible-to-slightly-positive: a few verification Bash calls on armed compute tasks near exit; exactly zero on non-compute tasks; no forced extra model turns."
rollback_trigger: "Revert if next-round pass_rate flat/down on the compute cluster, OR a previously-passing short compute task regresses to max_steps/budget_exceeded attributable to the added verification, OR replay fails on ComputeVerifyAddendum."
-->

### Why

Assigned focus task_000111_cbada64a (C++ OLS + bootstrap CI) exited done at only
7 steps with reward=0: committed slope m=2.5056 vs oracle 2.5997. OLS is
deterministic, so the number is wrong for an implementation/reading reason the
agent never probed. Its entire "verification" was a format check ("the values
make sense ... This is in the format") plus a "✓"-tick requirements checklist and
`ls -lh` when the stock _tb2_self_verify fired — no independent recomputation of
the value, no probe of the read/precision path. Sweeping the round found the same
mechanism recurring: task_001330 committed m=0.048 vs 0.050 (verifier hint names
the exact ambiguity: numpy.random.seed / draw-order); task_001937 Optimal Grid 60
vs 50 (search boundary / off-by-one); task_001653 centroid/distance wrong
(indexing/assignment). All exit done in 8-14 steps after a format-only self-check.
The stock TB2 self-verify checklist has a keep-alive item and a generic "values
semantically correct" item but NO step forcing an INDEPENDENT recomputation or an
explicit sweep of the spec ambiguities that flip numeric answers. This is a
Control-lever mechanism gap in an existing checkpoint, not a knowledge gap and
not a knob.

### Changes

- processors/compute_verify_addendum.py — new ComputeVerifyAddendum
  MultiHookProcessor. on_task_start arms tasks whose description is a
  numeric-compute task writing a computed result to a file (keyword predicate,
  no task literals). on_before_model, when armed AND the trailing user message is
  the stock self-verify checklist (sentinel-gated on "run through this
  checklist"), appends a generic addendum: recompute the key result an
  INDEPENDENT way (different tool/language), enumerate & confirm the spec
  ambiguities (seed & draw-order, indexing / percentile rounding, boundary
  inclusivity, off-by-one, regressor axis, rounding/precision), and count every
  input record. Fires <=1x/task; mutates only that one user-message string in
  place (no insert/drop/reorder). _order=92 (after CustomSelfVerifyProcessor 90).
- config.yaml — R0 config + register ComputeVerifyAddendum via absolute file://
  path after CustomSelfVerifyProcessor. Everything else byte-identical to R0.

### Evidence

- task_000111 messages step 5: "verify the output format ... the values make
  sense ... This is in the format"; step 6 self-verify response = requirements
  "✓" checklist + `ls -lh`, no recompute; exit done at 7 steps. final_pytest:
  `Expected m to be approx 2.5997, got 2.5056`.
- task_001330 final_pytest: `assert '0.048' == '0.050' ... Ensure you used
  numpy.random.seed(42) and the correct parameters`.
- task_001937 final_pytest: `Expected Optimal Grid to be 50, but got 60`.
- task_001653 final_pytest: centroid 36.37,45.81,38.03 / dist 18.62 vs oracle
  42.01,45.90,43.07 / 11.34.
- Validators: canonicalize ok (0 templates); dry_fire likely_bugs 0/0; contract
  violations 0; literals findings 0.

### Uncertainty

Why-Control-not-Instruction: the R1 sibling h_numeric_crosscheck_v1 (instruction
lever, still pending, instruction scoreboard 0/1 accepted) already tries the
broad system-prompt-swap version; a whole-prompt rewrite pays on all ~50 tasks
and competes with SiblingSystemPromptBuilder. This narrower Control processor
augments the existing self-verify checkpoint only on armed compute tasks. Two
ways the bet is wrong: (1) the tasks stay F even though the addendum fired —
then the residual is a model capability gap (agent read the directive but could
not act), logged separately; task_000111 in particular is deterministic OLS so
the flip depends on the agent doing a genuinely INDEPENDENT (different-tool)
recheck that surfaces a C++-side reading/precision bug — a qualified yes. (2) an
armed compute task that already passes over-runs on extra verification and
regresses to max_steps — the rollback trigger; the cited cluster all had ample
headroom (<=14 steps).
