# Evolve journal — tmax-coev-rep10-i1

## Round 1 — step-budget deadline nudge

<!-- journal:frontmatter
round: 1
timestamp: 2026-01-01T00:00:00Z
hypothesis_id: h_step_budget_deadline_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_000118_3043e92d, task_000264_ab8c7253, task_000958_4bb2b05d, task_001031_a8f0eb37, task_001321_658ce4a8]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=30/50; +3/-2 gained=task_000587_9862bb19,task_001032_1adaccb9,task_001515_eed714e6 lost=task_001536_acfe6c35,task_001701_95e3bbcb; score 0.6000 >= incumbent(mean) 0.5800 - tol 0.0400
expected_global_gain: "Flip part of the 6-task budget_exceeded cluster (4 domains) that ends at step 80 with no deliverable on disk; also trims extreme-tail runtimes"
regression_risk: "Low — appends up to 2 user reminders only past 65% of the 80-step budget; passing tasks finish in 8-52 steps and never see it"
cost_shift: "Negligible-to-negative: two ~60-token injections only on long tasks; curbs late thrash so tail-task tokens should drop"
rollback_trigger: "If pass_rate flat/down AND any previously-passing task with steps>52 regresses, revert"
-->

### Why

R0 scored 29/50 (58%). Of 21 failures, 6 hit `exit_reason=budget_exceeded`
at the full 80-step budget, spanning system_administration, data_querying,
scientific_computing, and data_processing. Every one failed the verifier on
a "required output file missing" assertion — the agent explored/thrashed the
whole budget and left **nothing on disk**. task_000264's own words: "I've
been stuck in a loop trying the same query"; then three "Your previous
response was cut off... continue" turns with no file write, ending at step 80
with `top_managers.csv` / `query_plan.txt` missing. The pipeline ships a
`TaskTimeReminderProcessor` but the recipe runner never sets its
`timeout_seconds`, so its `on_step_start` returns early every step — the
deadline nudge is inert. The budget actually enforced is *steps* (max_steps=80),
which nothing tracks.

### Changes

- `processors/step_budget_deadline.py` — new `StepBudgetDeadlineProcessor`
  (`MultiHookProcessor`). On `on_step_start`, computes `step_id / task.max_steps`
  and injects escalating user-role reminders at 65% (early) and 85%
  (critical) telling the agent to commit its best-effort deliverable to the
  required paths NOW and `ls`-verify. Task-agnostic — names no paths/domains.
- `config.yaml` — register the processor at `_order=7` (right after the inert
  time reminder) via absolute `file://` path, `warn_at: [0.65, 0.85]`.

### Evidence

- task_000264_ab8c7253: `exit_reason=budget_exceeded`, steps=80, 953s;
  verifier `top_managers.csv missing` + `query_plan.txt missing` + no index.
  Body last msgs: "I keep getting the same result" / "stuck in a loop".
- task_001031_a8f0eb37: `budget_exceeded`, 80 steps, 2336s; verifier
  `Results file missing at /home/user/results.json`.
- task_000010_644ab1c2: `budget_exceeded`, 80 steps; verifier `operator.py
  does not exist` + `api_success.log does not exist`.
- task_000118_3043e92d / task_000958_4bb2b05d / task_001321_658ce4a8: all
  `budget_exceeded` at 80 steps (same shape).
- Root-cause harness fact: harness.py:246 — `TaskTimeReminderProcessor`
  early-returns when `timeout_seconds` is unset, which the runner never sets.

### Uncertainty

The reminder helps only if a runnable-but-imperfect deliverable exists in the
model's head by ~step 52; on tasks where the agent is genuinely lost (no valid
approach at all) it won't produce a passing file. Watch attribution: if
predicted tasks stay `still_F` while a slow passing task regresses, the nudge
is disrupting more than it helps — revert per trigger above.

## Round 2 — no-op: remaining failures are capability gaps

<!-- journal:frontmatter
round: 2
timestamp: 2026-01-02T00:00:00Z
hypothesis_id: h_r2_noop_capability_wall_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=32/50; +5/-3 gained=task_001031_a8f0eb37,task_001090_c61c71f2,task_001536_acfe6c35,task_001701_95e3bbcb lost=task_000587_9862bb19,task_001032_1adaccb9,task_001264_9f4ca84a; score 0.6400 >= incumbent(mean) 0.6200 - tol 0.0400
expected_global_gain: "None — no config change. R1 step-budget nudge already accepted (29->30) and is clean (never touches passing tasks, which top out at 38 steps < the 52-step 65% threshold)."
regression_risk: "None — byte-identical config copy."
cost_shift: "Zero."
rollback_trigger: "N/A (no change). Next round: if a NEW recurring harness-addressable shape appears (missing-file / wrong-path / recoverable-loop cluster of 2+ tasks), act on it."
-->

### Why

R1 (step-budget deadline nudge) was ACCEPTED: 29/50 -> 30/50 (60%). It got
3 of its 6 predicted budget_exceeded tasks (000118, 000958, 001321) to exit
`done` instead of running out the step budget — i.e. the agent now commits a
deliverable — but those tasks still fail the verifier on *content correctness*.
The nudge did its job (commit-on-disk); the wall it hit next is capability.

I read all 20 R1 failures via final_pytest assertions + trajectory bodies.
The failure landscape splits cleanly and none of it is harness-addressable:

- **14 `done`-fail (agent thought it finished, verifier disagreed):** every
  one is a genuine correctness/capability gap. Examples — accuracy 0.336 vs
  0.98 threshold (000015), F1 0.005 (001321), wrong centroid 36.37 vs 42.01
  (001653), grid 60 vs expected 50 (001937), max deviation 1.0 vs <0.1
  (000396), lingering processes not killed (000140), process name 'bash' vs
  'monitor' (001090), adversarial corpus bypassed (000505). The existing
  CustomSelfVerifyProcessor checklist DID fire on these (confirmed
  "Verification check initiated" + re-verify turns in bodies) — files exist,
  format is right, but the *computed values are wrong*. No processor/prompt
  fixes wrong math or wrong logic.
- **2 `done`-fail environment issues:** 000028, 000958 — verifier phase errors
  with `ModuleNotFoundError: No module named 'requests'` during test
  *collection*. initial_pytest PASSED (requests present pre-agent); final fails.
  Agent never touched pip/venv (grepped commands). This is a verifier/env
  Python mismatch outside config control (internet blocked, can't install).
- **6 budget_exceeded (000010, 000264, 001031, 001536, 001701, 001818):**
  genuinely hard multi-step tasks — audio transcription+DB load (001818),
  parallel matrix decomposition (001031), forensic token classifier (001701),
  recursive-CTE hierarchy w/ sqlite locking (000264, agent self-reports "stuck
  in a loop"). The step-nudge already fires here; the gap is no valid approach,
  not commitment or a recoverable loop.

I checked the two candidate harness levers the playbook flags and DROPPED both:
- **Plan-block instruction (preservative-transfer):** dropped. Both passing AND
  failing multi-part tasks already emit a "Let me break down this task: 1... 2..."
  plan in step 1 (compared 000024 pass vs 001937/001090 fail — all plan).
  Variant-C second end fails: failing tasks DO plan; gap is execution, not
  planning. This is the "encoding a habit that was already free" pitfall.
- **Stronger self-verify:** dropped. The existing checklist is comprehensive
  (re-read task, ls each output, cat contents for semantic correctness, validate
  test method, confirm services alive) and already fires. It can't detect that a
  correct-format value is numerically wrong.

Shipping a timid threshold tweak or embedding task-specific domain knowledge
(the forbidden move) would carry higher regression risk than expected gain.
Per SOUL.md, capability gaps get logged, not patched. Explicit no-op is the
Pareto-correct decision this round.

### Changes

- `config.yaml` — byte-identical copy of R1 config (verified `cmp`; canonicalizes
  `{"ok": true, "checked_templates": 0}`). No new processors/tools/templates.

### Evidence

- Score: summary.json n_passed=30/50 (up from R0 29). by_domain worst:
  system_administration 0/5, software_engineering 2/5, scientific_computing 2/5.
- 000264 body: assistant "I've been stuck in a loop... recursive CTE is creating
  a full Cartesian product"; final_pytest "database is locked (5)" — reasoning gap.
- 001937 final_pytest: "Expected Optimal Grid to be 50, but got 60"; body shows
  self-verify fired ("Verification check initiated") and agent re-confirmed its
  own wrong value 60 — capability, not commitment.
- 000028/000958 final_pytest: `ModuleNotFoundError: No module named 'requests'`
  at collection; initial_pytest passed — verifier-env mismatch, not agent action.
- Passing-cluster protection: max passing step count = 38 (000578) < 52-step
  (0.65*80) R1 nudge threshold, so the accepted R1 change is confirmed
  non-regressive on the passing set.

### Uncertainty

The `requests` ModuleNotFoundError on 000028/000958 could in principle be
worked around if the agent proactively `pip install`ed common test deps — but
internet is blocked and it's only 2 tasks, below the systemic threshold. If a
future round shows a 2+-task cluster of verifier-import failures for the same
module, a Control processor that nudges "ensure the system python used by tests
has required libs" might be worth an evidence-backed try. Watch R2 attribution:
if any predicted R1 task regresses T->F, revisit the nudge thresholds.

## Round 3 — no-op: failures split capability vs verifier-env; incumbent at noisy optimum

<!-- journal:frontmatter
round: 3
timestamp: 2026-01-03T00:00:00Z
hypothesis_id: h_r3_noop_capability_and_verifier_env_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=34/50; +2/-0 gained=task_000587_9862bb19,task_001264_9f4ca84a; score 0.6800 >= incumbent(mean) 0.6400 - tol 0.0400
expected_global_gain: "None — no config change. Every R2 failure is a model capability gap (wrong computed value / wrong logic) or a verifier-phase env issue outside config.yaml control; no candidate passes its retroactive check with net-positive Pareto value in a ±2-task noise regime."
regression_risk: "None — byte-identical config copy (cmp verified; canonicalizes {\"ok\": true, \"checked_templates\": 0})."
cost_shift: "Zero."
rollback_trigger: "N/A (no change). Next round: act only if a NEW harness-addressable shape recurs across >=2 tasks whose Variant-A check is 'yes' — e.g. a tool-return-shape gap, a recoverable-loop that a guard can break before the agent commits a WRONG artifact, or a genuinely config-controllable env fix."
-->

### Why

R2 measured 32/50 (64%) on the R1==R2 config. History shows this config
swings 30↔32 across repeats — an 8-task flip set on IDENTICAL config
(R1→R2: gained 001031,001090,001536,001701,001818; lost 000587,001032,001264).
So the true signal is ~31±1 and several "failures" (001264,001032,000587)
are FRAGILE (pass on some draws), not hard walls. In a ±2-task noise regime,
adding config surface area risks regressing the fragile passing cluster for
speculative gain — the Pareto math favors restraint unless a candidate has a
clean Variant-A "yes".

I read all 17 R2 failures (result.json final_pytest + trajectory bodies). The
landscape splits three ways and NONE is a net-positive harness fix:

- **12 capability gaps (wrong value / wrong logic):** 001653 (centroid
  36.37 vs 42.01), 001937 (grid 60 vs 50), 000396 (max_dev 0.27 vs <0.1),
  001321 (F1 0.0001 vs 0.95), 000015 (accuracy 0.0 vs 0.98), 000587 (C++
  csv parser NaN handling), 000264 (query plan missing USING INDEX),
  000505 (adversarial corpus bypassed 2/2), 000140 (lingering vm_service
  procs not killed), 001781 (Rust deadlock not fixed), 000933 (build didn't
  emit bin/graph_math_double), 000118 (log dir 209MB vs <45MB threshold).
  CustomSelfVerifyProcessor fired on these (per R2's confirmed re-verify
  turns) — files exist, format right, computed values/logic wrong. No
  processor/prompt fixes wrong math.

- **3 verifier-phase env issues outside config control:** 000010 — task
  MANDATES `/home/user/operator.py`; the verifier's python runs with cwd on
  sys.path so stdlib `import operator` (via `collections`) resolves to the
  agent's file → `namedtuple` circular ImportError kills the whole
  interpreter incl. test collection. 000028 & 000958 — C++ HTTP microservices
  verified by python `requests`; initial_pytest PASSED (requests present),
  final_pytest ERROR `ModuleNotFoundError: requests` at collection. Grepped
  all Bash cmds: agent NEVER touched pip/venv/python/PYTHONPATH and created
  NO requests-shadowing file (000958 created httplib.h/req.h/sqlite3.h/
  server.cpp only). So the two pytest phases run different python contexts —
  a verifier-infra mismatch config.yaml cannot reach.

- **2 fragile budget_exceeded thrash (001264, 001032):** genuinely hard —
  001264 thrashes writing a concurrent curation script (curate.sh edited 16×,
  edit-warn fired ~65×, agent DID switch to xargs approach as nudged);
  001032 manually parses a corrupt-tar binary header (zero size field).
  Both PASSED in R1. The R1 step-nudge + CustomEditToolProcessor already
  fire. Variant-A check = NO: committing sooner commits a still-WRONG
  artifact; the gap is "no valid approach", not commitment or a breakable
  loop.

Candidates considered and DROPPED (all fail Variant-A or lose the Pareto
tradeoff):
- **Lower CustomEditToolProcessor.threshold (Configuration):** dropped. It
  already fired ~65× on 001264 without helping; lowering it fires MORE on
  the many passing tasks that legitimately edit a file 3-6× → higher
  regression risk than gain.
- **requests-dep Control nudge (from R2's watch-item):** dropped. Confirmed
  the agent's python is fine and it's the VERIFIER's python that lacks
  requests; a prompt/processor nudge in the agent phase can't change the
  verifier's interpreter. Not config-addressable.
- **operator.py shadow guard:** dropped. The offending filename is
  task-MANDATED (`/home/user/operator.py`); the agent can't rename it, and
  the verifier cwd/path is fixed by infra. 1 task, non-actionable.
- **Stronger self-verify / plan-block (Instruction):** already dropped in R2
  (both already fire; gap is execution/values, not planning/commitment).

Per SOUL.md, capability gaps get logged not patched, and the analyze skill's
rule — "a lever tried on the same cluster without flipping is a signal to look
elsewhere" — applies to the capability cluster. Explicit no-op is the
Pareto-correct decision.

### Changes

- `config.yaml` — byte-identical copy of R1/R2 config (`cmp` clean;
  canonicalizes `{"ok": true, "checked_templates": 0}`). No new
  processors/tools/templates.

### Evidence

- Score: summary.json n_passed=32/50. Worst by_domain: system_administration
  1/5, software_engineering 2/5.
- Noise regime: R1_per_task vs R2_per_task on the SAME config diff = 8 flips
  (4 gained / 4 lost), incl. 001264 T→F, 001032 T→F, 000587 T→F — fragile,
  not hard.
- 000010 final_pytest: `ImportError: cannot import name 'namedtuple' ...
  circular import` traced through `/home/user/operator.py` (task-mandated path).
- 000028/000958 final_pytest: `ModuleNotFoundError: No module named 'requests'`
  at collection; initial_pytest passed; agent Bash log shows zero pip/venv/
  python edits and no requests-shadowing file created.
- 001937 final_pytest: "Expected Optimal Grid to be 50, but got 60" (wrong value).
- 001321 final_pytest: "F1 Score 0.0001 ... required 0.95" (wrong logic).
- 001264 body: "I'm over-editing the file. Let me try a fundamentally different
  approach - use xargs with -P" — edit-warn fired and agent complied, still hit
  budget on a still-wrong script (capability, not commitment).

### Uncertainty

If R3's remeasure of this same config lands at 30 rather than 32, that's the
known ±2 noise, not a regression. The one plausible future harness lever is a
loop-breaker that fires EARLIER than the current edit-warn AND forces a pivot
to a demonstrably-simpler correct baseline on the two fragile thrash tasks —
but that needs body evidence that a simpler correct approach exists and was
within reach (Variant-A "yes"), which R2's bodies do not show (001032's
corrupt-tar is genuinely hard; 001264's concurrency logic never reached a
passing form). Revisit only with that evidence.

## Round 4 — no-op: 15-task hard wall is capability/verifier-env; no new harness shape

<!-- journal:frontmatter
round: 4
timestamp: 2026-01-04T00:00:00Z
hypothesis_id: h_r4_noop_hardwall_capability_verifier_env_v1
levers: []
predicted_affected: []
gating_outcome: reverted
gating_attribution: score=29/50; +1/-6 gained=task_001321_658ce4a8 lost=task_000587_9862bb19,task_001031_a8f0eb37,task_001090_c61c71f2,task_001536_acfe6c35; score 0.5800 < incumbent(mean) 0.6250 - tol 0.0400 -> revert to R3
expected_global_gain: "None — no config change. R3 measured 34/50 (68%), the best score to date and up-trending on the R1==R2==R3 stable config (29->30->32->34). The full 4-round per-task matrix shows a 15-task HARD WALL (fails every round) that is entirely capability gaps (wrong computed value/logic on hidden verify-time data) + verifier-env defects config.yaml cannot reach, plus a 9-task FRAGILE set that already passes on some draws. No candidate passes its Variant-A check with net-positive Pareto value; every plausible change adds surface area that risks the fragile passing cluster."
regression_risk: "None — byte-identical config copy (cmp verified; canonicalizes {\"ok\": true, \"checked_templates\": 0})."
cost_shift: "Zero."
rollback_trigger: "N/A (no change). Next round: act only if a NEW harness-addressable shape recurs across >=2 tasks with a clean Variant-A 'yes' — e.g. a tool-return-shape gap, a recoverable-loop a guard can break BEFORE the agent commits a wrong artifact, or a genuinely agent-phase-reachable env fix. Do NOT re-tune the step-nudge on the budget_exceeded cluster (Variant-A=no there) and do NOT add a preservative-lock 'to be safe' while making no edit (Variant-B=no)."
-->

### Why

R3 measured 34/50 (68%) — the best round yet — on the config unchanged since
R1. I built the full 4-round per-task matrix (R0..R3) from history/*_per_task.json.
It partitions the 50 tasks cleanly:

- **26 always-pass** (stable passing core).
- **15 always-fail (HARD WALL, F in every round):** 000010, 000015, 000028,
  000118, 000140, 000264, 000396, 000505, 000748, 000933, 000958, 001321,
  001653, 001781, 001937.
- **9 fragile (flip T<->F across rounds on the SAME config):** 000587, 001031,
  001032, 001090, 001264, 001515, 001536, 001701, 001818 — model
  non-determinism, not an unprotected config habit.

I read every hard-wall failure's final_pytest tail + representative bodies.
The 15 hard walls split three ways, none net-positive harness-addressable:

- **12 capability gaps (wrong computed value / wrong logic):** 000015
  (verifier runs the agent's migration script on verify-time-generated hidden
  URLs -> accuracy 0.0 vs 0.98; logic wrong on unseen data), 001653 (centroid
  36.37 vs 42.01), 001937 (grid 60 vs 50), 000396 (max_dev 0.607 vs <0.1),
  001321 (F1 0.005, recall 0.0026 — extraction logic wrong), 000264 (recursive
  CTE Cartesian product; wrong CSV rows + missing query plan; budget_exceeded),
  000505 (2/2 adversarial evil bypassed), 000140 (lingering vm_service pids
  347/605/807 not killed), 001781 (rust deadlock not fixed), 000933 (tarball
  contents/binary exec wrong), 001032 (extraction log 1 line vs 2), 000748
  (rust memory_profiling assertion). Files exist, paths/format right, computed
  values/logic wrong. No processor/prompt fixes wrong math on hidden data.
- **2 verifier-phase env defects (config-unreachable):** 000028 & 000958 —
  C++/nginx microservice tasks the agent SOLVED (initial_pytest passed; agent
  verified with `urllib.request`, grepped bash shows zero pip/python/venv edits
  and no requests-shadowing file). final_pytest ERRORs at collection with
  `ModuleNotFoundError: No module named 'requests'`. The two pytest phases run
  different python contexts; config.yaml only controls the AGENT phase, so it
  cannot install `requests` into the verifier's interpreter.
- **1 task-mandated file-shadow (non-actionable):** 000010 — task MANDATES
  writing `/home/user/operator.py`, which shadows stdlib `operator` and crashes
  the verifier's python at collection. The agent even HAD a working operator.py
  (backed up + applied manifests) then overwrote it with a broken bash-wrapper
  while fighting the import error (budget_exceeded, 80 steps). The decisive
  error was destroying a working deliverable — a reasoning gap a processor
  cannot detect (it can't know the prior file was correct).

Candidates considered and DROPPED:
- **Smarter/earlier step-nudge on budget_exceeded (000010/000264/000118):**
  dropped. Variant-A = no — 000264 is a Cartesian-product logic error, 000118
  the log-size approach is wrong, 000010 needs "don't destroy a working file".
  Committing sooner commits a still-wrong artifact. Same lever/cluster the R1
  nudge already covers without flipping.
- **requests-dep injection (Control/Instruction):** dropped again. Confirmed
  the agent's python is fine; only the VERIFIER's interpreter lacks requests.
  No agent-phase mechanism reaches the verifier's python.
- **Preservative-lock on the 9 fragile tasks:** dropped. Variant-B = no. I am
  making NO config edit this round, so there is no drift for a lock-in to
  guard against; encoding a habit "to be safe" is pure surface-area cost (the
  "habit that was already free" pitfall). The R1 step-nudge is already in the
  config and already protected (passing tasks top out well under the 65%
  threshold).

Per SOUL.md, capability gaps get logged not patched, and the analyze skill's
rule — a lever tried on the same cluster without flipping is a signal to look
elsewhere — applies to the whole hard-wall set. Explicit no-op is the
Pareto-correct decision: any speculative change risks the 9 fragile passing
tasks for zero credible flip on capability-bound walls.

### Changes

- `config.yaml` — byte-identical copy of the R1/R2/R3 config (`cmp` clean;
  canonicalizes `{"ok": true, "checked_templates": 0}`). No new
  processors/tools/templates.

### Evidence

- Score: summary.json n_passed=34/50, pass_rate=0.68. Worst by_domain:
  system_administration 1/5, software_engineering 2/5, scientific_computing 3/5.
- Hard-wall proof: R0..R3 per-task matrix — 15 tasks F in all four rounds.
- 000015 final_pytest: verifier generates `/tmp/hidden_test_urls.txt` +
  `/tmp/golden_output.jsonl` at verify time, runs the agent's migration script
  -> "Accuracy metric 0.0000 is below the 0.98 threshold" (logic wrong on
  unseen data — unreachable by any prompt/processor).
- 000028/000958 final_pytest: `ModuleNotFoundError: No module named 'requests'`
  at collection; initial_pytest passed; agent bash log shows only C++/nginx
  edits + `urllib.request` verification, zero pip/python/venv, no
  requests-shadowing file.
- 000010 body (compaction summary): agent had a working operator.py, then
  "renamed the script to operator.py ... circular import error ... tried a
  shell script wrapper ... syntax errors"; final_pytest crashes on
  `/home/user/operator.py` line 2 `cd /tmp && exec python3 ...` SyntaxError.
- 000264: `exit_reason=budget_exceeded`, 80 steps; wrong CSV (`Eve (Eng)|3`...
  vs expected `Alice (CEO),11`...) + missing query_plan — logic error.
- Fragile-set proof: 001264 T,T,F,T; 001032 F,T,F,F; 000587 F,T,F,T — flips on
  identical config = noise, not an unprotected habit.

### Uncertainty

If R4's remeasure of this config lands at 30-32 rather than 34, that is the
known +/-2 noise on the fragile set, not a regression. The one plausible future
harness lever remains a loop-breaker that fires EARLIER than the current
edit-warn AND forces a pivot to a demonstrably-simpler CORRECT baseline on a
thrash task — but that needs body evidence that a simpler correct approach was
within reach (Variant-A "yes"), which none of this round's budget_exceeded
bodies show. Revisit only with that evidence.
