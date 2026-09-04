# Evolve journal — tmax-coev-rep14-i1

## Round 1 — step-budget deliverable reminder

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-28T12:00:00Z
hypothesis_id: h_step_budget_reminder_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_000118_3043e92d, task_000206_a943669b, task_000958_4bb2b05d]
cited_candidates: [C-001]
gating_outcome: reverted
gating_attribution: score=29/50; +2/-6 gained=task_000206_a943669b,task_000536_9c16e8ef lost=task_000396_e56917e2,task_000587_9862bb19,task_001031_a8f0eb37,task_001089_220cc46b; score 0.5800 < incumbent(mean) 0.6600 - tol 0.0400 -> revert to R0
expected_global_gain: "Flip/relieve the budget_exceeded cluster (>=4 tasks) by nudging deliverable finalization + exact-path verification before the 80-step wall"
regression_risk: "A couple dozen extra tokens on tasks that run past 60% of an 80-step budget; short passing tasks (<20 steps) never see the reminder"
cost_shift: "Negligible; at most two short injected user messages per long-running task; likely net-neutral by curbing end-of-budget spirals"
rollback_trigger: "If R2 pass_rate is flat/down AND budget_exceeded count is unchanged, or if any previously-passing long-horizon task regresses to F, revert"
-->

### Why

Assigned focus `task_000010_644ab1c2` failed with `exit_reason=budget_exceeded`
(80 steps, reward=0). The verifier's `test_operator_script_exists` reports the
required deliverable `/home/user/operator.py` does not exist. Reading the
trajectory: the agent created `operator.py` (msg 19), then **renamed it away**
with `mv /home/user/operator.py /home/user/k8s_operator.py` (msg 23), and spent
the entire back half of the run (msgs 40–70) in a port-forward / zombie-process
debugging spiral, never restoring the required path. This is the shared shape of
the round's `budget_exceeded` cluster (also task_000118, task_000206,
task_000958): the agent gets absorbed in one sub-component, burns the step
budget, and hits the wall without finalizing/verifying the deliverable at its
exact specified path.

The harness has the *knowledge* (system prompt + `CustomSelfVerifyProcessor`
both say "verify exact output paths") but no *delivery mechanism* at the
decisive moment: `CustomSelfVerifyProcessor` only fires on voluntary exit (never
reached in a budget_exceeded run), and `TaskTimeReminderProcessor` is inert in
the tmax path (`timeout_seconds` is never injected by `harness_runner._run_async`,
so `self._timeout` stays None). The step budget (`BaseTask.max_steps=80`) has no
reminder at all.

### Changes

- `processors/step_budget_reminder.py` — new `StepBudgetReminderProcessor`
  (`MultiHookProcessor`): on `on_step_start`, when `step_id/max_steps` crosses
  0.60 (soft) and 0.80 (hard), appends one user-role reminder to stop
  sub-component exploration and finalize + verify every required output at its
  EXACT specified path (no renaming/relocating deliverables). Fires each
  threshold once; only active when `max_steps >= 20`. Contract-clean (append
  one user message, mirrors `TaskTimeReminderProcessor`; no removals, no
  system-prompt mutation).
- `config.yaml` — register the processor after `TaskTimeReminderProcessor`
  (`warn_at: [0.6, 0.8]`, `min_budget: 20`, `hard_threshold: 0.8`); rest of the
  pipeline byte-identical to R0. `system_prompt.txt` sibling copied unchanged.

### Evidence

- `task_000010_644ab1c2` result.json: `exit_reason=budget_exceeded`, steps=80;
  final_pytest: `Operator script /home/user/operator.py does not exist.`
- `task_000010_644ab1c2` messages.json: msg 19 `cat > /home/user/operator.py`;
  msg 23 `mv /home/user/operator.py /home/user/k8s_operator.py`; msgs 40–70
  continuous proxy/zombie debugging, `operator.py` never recreated.
- `task_000118_3043e92d`, `task_000206_a943669b`, `task_000958_4bb2b05d`:
  each `exit_reason=budget_exceeded`, steps=80, reward=0, deliverable
  unconverged at the wall.
- `harness_runner.py::_run_async`: only `sandbox_provider` + `tracer` injected
  at runtime → `TaskTimeReminderProcessor.timeout_seconds` is None → inert.

### Uncertainty

The reminder may not flip tasks whose failure is a genuine correctness bug
rather than a finalization/path miss (e.g. task_000118's daemon logic, task_000958's
service correctness). For those it is a fair-run improvement, not a guaranteed
flip. If R2 shows flat pass_rate with an unchanged budget_exceeded count, the
nudge isn't landing and should be revisited (e.g. escalate to a hard finalize
directive or an injected verify tool-call at ~step 75). Watch for any long-horizon
passing task regressing — none expected, since the reminder only adds messages.

## Round 1 (c4) — background-process state reconciliation

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-28T13:00:00Z
hypothesis_id: h_bg_state_reconcile_v1
levers: [control]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the clean-shutdown polarity (task_000140) and hardens the service/background cluster against the self-verify checklist's one-sided 'keep alive' wording by making persist-vs-cleanup explicit at exit."
regression_risk: "Low — processor is inert on the ~60% of tasks with no background launch (regex gate), fires at most once on a genuine no-tool-call exit after self-verify, injects text only (never kills, no knob/schema change), keeps message-count contract at +1 user."
cost_shift: "Negligible — one ~250-tok user message + at most a few short ps/pgrep Bash calls, only on tasks that launched a background process."
rollback_trigger: "R2 shows task_000140 still F with no process-state change, OR any previously-passing service-alive task (task_000028/001089/001264) regresses (service killed / not reachable)."
-->

### Why

Assigned focus task_000140_01c78b42 failed on
`test_no_lingering_service_processes`: `pgrep -f vm_service` returned
`['340','586','793']` at grader time. The task is a CI/CD pipeline that
must start AND then SIGTERM-stop a Go `vm_service`, leaving nothing
running. The agent fixed all three files correctly and its own
`test_pipeline.sh` ran (curl returned 200, `vm_setup.log` written), but it
declared done without confirming its own test-run service was reaped — so
the container's final state had lingering `vm_service` processes. Root
harness gap: the agent's own test-time actions mutate the state the
verifier inspects, and nothing prompts it to reconcile leftover
background processes against the task's required end state. The existing
`CustomSelfVerifyProcessor` checklist only nudges the *persist* polarity
("confirm running services are still alive"), the exact opposite of what
this clean-shutdown task needs. Only task_000140 has this grader in R0
(single grader witness), but the mechanism spans a real service cluster
and the one-sided checklist wording is a latent regression risk for any
clean-shutdown grader — hence a *neutral* reconciliation nudge, not a
forced kill.

### Changes

- `processors/bg_state_reconcile.py` — new `BackgroundStateReconcileProcessor`
  (`MultiHookProcessor`, `_order=91`, after `CustomSelfVerifyProcessor` at 90).
  Tracks background-launch commands in `on_before_tool` (regex: `nohup`,
  trailing `&`, `disown`, `setsid`, `start*.sh`, `*_service`, `systemctl/service start`);
  at a genuine no-tool-call exit-intent, if a launch occurred and no cleanup nudge
  has fired, injects one keepalive + one deferred user message asking the agent to
  `ps`/`pgrep` and reconcile persist-vs-cleanup against the task text. Kills nothing.
- `config.yaml` — register the processor via absolute `file://` path after
  `CustomSelfVerifyProcessor`. No other change.

### Evidence

- task_000140_01c78b42 `final_pytest.output_tail`:
  `AssertionError: Lingering vm_service processes found: ['340', '586', '793']`
  from `test_no_lingering_service_processes`; `agent.exit_reason=done`,
  `finished=no_tool_calls`, 11 steps, `initial_pytest.passed=true`.
- task_000140 messages: agent wrote `test_pipeline.sh` ending `kill -TERM $PID`,
  ran `bash /home/user/test_pipeline.sh` (starts `./vm_service &` via
  `start_service.sh`), then the final assistant turn declared success with no
  post-run `ps`/`pgrep` — the test-started service was never confirmed dead.
- Existing checklist (harness.py L96): "For running services: confirm they are
  still alive and reachable right now" — one-sided persist wording.

### Uncertainty

If the model ignores the nudge or cannot reason about which polarity the task
wants, task_000140 stays F. If a service-alive task's agent over-reacts and
kills a service it should keep, that cluster regresses — the nudge is worded
neutrally and gated to fire only once after self-verify to minimise this, but
R2 attribution on task_000028/001089/001264 is the watch signal; revert per
rollback_trigger if any regress.

## Round 1 (c5) — recover from verbatim tool-result loops

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-28T13:00:00Z
hypothesis_id: h_stuck_result_breaker_v1
levers: [control]
predicted_affected: [task_000206_a943669b, task_000958_4bb2b05d, task_000118_3043e92d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the verbatim-loop-burns-80-step-budget cluster (three-plus tasks) by breaking the fixation in place and returning control while budget remains — a recovery strategy complementary to terminate-on-loop."
regression_risk: "Low — append-only note on a tool result after three-plus consecutive byte-identical outputs, a shape no R0-passing task shows; never drops/rewrites messages, never terminates. Contract check passes (append-only on_after_tool)."
cost_shift: "Net decrease — breaks dead loops at repeat three instead of letting them run to step 80, reclaiming tens of steps per stuck task; the note is about 90 tokens and fires only inside an active loop."
rollback_trigger: "If R3 shows any previously-passing task regressing, or the three cited tasks still budget_exceeded with the loop unbroken, revert (raise warn_threshold first if the nudge is merely too weak)."
-->

### Why

Assigned focus task_000206_a943669b exited budget_exceeded at step 80 with a
malformed findings.txt. The jq binary rejects hyphenated keys
(csp-report / blocked-uri) as "report/0 is not defined", and the model
re-issued byte-identical failing jq commands with verbatim assistant text
about 13-plus times in a row (messages.json rows 11-533) — the first third of
the step budget spent making zero progress. It escaped only after a
PostCompaction refresh, then had no budget left to fix its buggy analyze.sh.
The pipeline had no recovery hook for repeated identical tool results:
ParseRetryProcessor handles only unparseable model output, CustomEditToolProcessor
counts only write commands, and CustomSelfVerifyProcessor never fires on a
budget-exhausted run. Two other round-0 tasks share the mechanism
(task_000958 sqlite3 x18, task_000118 wait/ps x13).

### Changes

- processors/stuck_result_breaker.py — new StuckResultBreaker
  MultiHookProcessor. On on_after_tool, tracks consecutive byte-identical
  results per tool; at warn_threshold (3) appends an escalating corrective note
  ("this exact output has repeated N times — stop, switch mechanism") and at
  hard_threshold (6) a stronger note; reset_after_nudge re-fires if the loop
  persists. Append-only, non-terminating, content-agnostic (no task/path/
  syntax literals).
- config.yaml — register StuckResultBreaker at _order 35 (after
  CustomEditToolProcessor, before CustomSelfVerifyProcessor) via absolute
  file:// path. Rest byte-identical to R0.

### Evidence

- task_000206_a943669b messages.json rows 11-533: assistant text "The issue is
  that jq is interpreting report and uri ..." plus tool result "jq: error:
  report/0 is not defined ... (exit 3)" repeat identically over 10 times before
  any tool switch; result.json exit_reason=budget_exceeded, steps=80, reward=0,
  initial_pytest.passed=true; final findings.txt has empty "Attacker IP:" and a
  doubled Blocked URI line.
- task_000958_4bb2b05d, task_000118_3043e92d: prior-round sweep (learnings.md
  lines 108-114) documents sqlite3 x18 and wait/ps x13 consecutive-identical
  loops — same mechanism.

### Uncertainty

Distinct from the c3 proposal (LoopDetectionProcessor, configuration lever,
terminate-on-loop): this is a recovery nudge, not a kill switch. If the model
ignores the note and keeps looping, the reclaimed-budget benefit does not
materialize and the task may still fail — but the append-only design cannot
itself regress a passing task. If R3 shows the nudge too weak (loops persist),
raise the cadence or defer to a terminate-on-loop guard; if a passing task
regresses, revert.

## Round 1 (c7) — verify against real artifacts before finishing

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-28T14:00:00Z
hypothesis_id: h_verify_real_artifacts_v1
levers: [instruction]
predicted_affected: [task_000505_50b5162d, task_000536_9c16e8ef]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=29/50; +2/-2 gained=task_001089_220cc46b,task_001701_95e3bbcb lost=task_000536_9c16e8ef,task_001706_24462a09; score 0.5800 >= incumbent(mean) 0.6200 - tol 0.0400
expected_global_gain: "Flip the OCR/extraction->script cluster (>=2 tasks: 505 security, 536 data_querying) where a self-fabricated test masks a wrong deliverable; the verify-against-real-inputs + distrust-lossy-extraction discipline generalizes to any detector/transformer/script task."
regression_risk: "Low — prompt-only; adds verification steps, removes no capability and changes no processor. Worst case a few extra Bash confirmations on already-passing tasks."
cost_shift: "Small positive (a few extra verification steps on some tasks); likely net-neutral to net-positive by converting silent wrong-answer finishes into corrected deliverables."
rollback_trigger: "If R3 pass_rate is flat/down AND task_000505/task_000536 stay F, or any previously-passing short task regresses to F, revert to R0 prompt."
-->

### Why

Assigned focus task_000505_50b5162d (security, reward=0, exit_reason=done, 10 steps).
The agent OCR'd an SSH key from an image, got a garbled string (standard ed25519
prefix `AAAAC3NzaC1lZDI1NTE5` misread as `AAAAC3NzaC1IZDIINTES`, stray spaces
inserted), hardcoded it, then "verified" by creating test files that echoed its own
garbled string — a guaranteed false pass — and declared done. The verifier ran the
script against the real trojaned binaries (uncorrupted key): `2 of 2 evil bypassed`.
Same mechanism appears in task_000536_9c16e8ef (OCR employee-id -> SQLite audit
script; failed on CSV quoting that comparison against the real DB output would have
surfaced). Shared shape: extraction from a lossy source trusted blindly + validation
against self-fabricated inputs instead of the real artifacts already on disk. The OCR
misread is a model limit; the false-confidence verification loop is a harness
discipline gap the system prompt can close generally.

### Changes

- `system_prompt.txt` (sibling of config, read by SiblingSystemPromptBuilder) —
  add general discipline: (1) survey real input artifacts up front; (2) when the
  deliverable is a script/program/classifier, validate it end-to-end against the
  REAL inputs the task points at, never against self-fabricated inputs echoing a
  derived value; (3) distrust values from lossy sources (OCR/scan), cross-checking
  format (prefix/length/charset/decodability) and the real target data before
  trusting them; (4) before stopping, confirm exact output paths AND correct
  behavior on real inputs. No task-specific literals.
- `config.yaml` — byte-identical to R0 (still SiblingSystemPromptBuilder); the
  change is entirely in the sidecar prompt. Instruction lever only.

### Evidence

- task_000505_50b5162d messages.json msg 7: tesseract output
  `ssh-ed25519 AAAAC3NzaC1IZDIINTES...`; msg 9 hardcodes that string and tests
  `/tmp/malicious_test.txt` built by echoing the same string; result.json
  final_pytest: `2 of 2 evil bypassed: cat_evil, ls_evil`.
- task_000536_9c16e8ef msg 0: OCR of `/app/target_memo.png` + script emitting CSV
  from `/app/audit.db`; result.json final_pytest index-0 diff is a CSV quoting
  mismatch (`"Source Code Repo"` vs `Source Code Repo`) checkable against real DB
  output before exit.

### Uncertainty

If the model reads the discipline but still trusts a garbled OCR value without
running against the real binaries, 505 stays F (the OCR itself is a model limit;
the rule only helps if the agent acts on the "test against real inputs" nudge). For
536 the format cross-check is more mechanical and likelier to land. Append-only
prompt guidance cannot itself regress a passing task; watch for step-count inflation
on easy tasks. If R3 shows flat pass_rate with both targets still F, escalate (e.g.
a Control hook that blocks `exit` until the deliverable has been exercised on a
non-self-generated file) or defer.

## Round 2 — exact output-contract self-verify

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-28T13:00:00Z
hypothesis_id: h_output_contract_verify_v1
levers: [control]
predicted_affected: [task_000264_ab8c7253, task_000536_9c16e8ef]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Relieve the 'false-confidence voluntary exit on wrong output-contract' cluster (>=2 data_querying tasks, plausibly touches the ~8 done/no_tool_calls failures whose miss is exact format/values)"
regression_risk: "Low — behaviour-identical to the CustomSelfVerifyProcessor it replaces except for a longer checklist; same +1 user msg / keepalive / one-shot firing. Worst case is a few extra verification Bash calls."
cost_shift: "Small positive: ~2 extra checklist sentences + 1-3 extra verify Bash calls on tasks that voluntarily exit; net-neutral-to-favourable since it targets would-be failures."
rollback_trigger: "If R3 pass_rate is flat/down AND task_000264/task_000536 still fail on the same exact-match assertion, the nudge isn't landing (residual is a pure model SQL/format capability gap) — revert to stock CustomSelfVerifyProcessor."
-->

### Why

Assigned focus `task_000264_ab8c7253` (data_querying) failed with `reward=0`,
`exit_reason=done`, `finished=no_tool_calls`, steps=17 — a *voluntary* exit, not
a budget/loop wall. Two output-contract errors: (1) the recursive CTE base case
counted each employee as its own subordinate → every count off by +1
(`'Alice (CEO),12' != 'Alice (CEO),11'`); (2) the query-plan test asserts the
literal substring `USING INDEX`, but the agent produced `USING COVERING INDEX` /
`AUTOMATIC COVERING INDEX`. The stock `CustomSelfVerifyProcessor` fired (msg 289
`_tb2_self_verify`) but its generic checklist ("re-read task / files exist /
inspect contents") let both slip: the agent re-`ls`'d and re-eyeballed the
numbers it had already produced, never recomputing a value or comparing the
format literally. `task_000536_9c16e8ef` shows the same shape — correct values
emitted with quoted CSV fields where bare were required. Shared root cause: the
verification discipline at the moment of voluntary exit is too weak to catch
exact-format / off-by-one output-contract misses.

### Changes

- `processors/output_contract_verify.py` — new `OutputContractVerifyProcessor`
  (`MultiHookProcessor`). Drop-in replacement for `CustomSelfVerifyProcessor`:
  identical one-shot exit-verify mechanism (keepalive synthetic tool call + a
  single appended user message, same singleton group `tb2_self_verify`, same
  order 90), but the injected checklist adds two task-agnostic steps — (3) a
  literal byte-for-byte output-format audit (delimiter/quoting/header/trailing
  newline) and (4) an independent re-derivation of at least one expected value
  by a different method (hand-trace / alternate query / manual count). No
  domain knowledge, constants, paths, or task IDs embedded.
- `config.yaml` — swap the final pipeline entry
  `benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor` for a
  `file://…::OutputContractVerifyProcessor` reference. Rest of the pipeline
  byte-identical to R0. `system_prompt.txt` sibling copied unchanged.

### Evidence

- `task_000264_ab8c7253` result.json: `reward=0`, `exit_reason=done`,
  `steps=17`; `final_pytest`: `At index 0 diff: 'Alice (CEO),12' != 'Alice (CEO),11'`
  and `'USING INDEX' not in 'QUERY PLAN … SCAN E USING COVERING INDEX …'`.
- `task_000264` messages.json: msg 88 CTE base case `e.id AS subordinate_id`
  (counts self); msg 289 self-verify tool fired; msgs 307–328 agent re-`ls`'d
  and re-declared success without recomputing.
- `task_000536_9c16e8ef` result.json `final_pytest`:
  `'EMP-4001,"Source Code Repo",…' != 'EMP-4001,Source Code Repo,…'` — right
  values, quoted vs bare fields; `exit_reason=done`, `finished=no_tool_calls`.

### Uncertainty

Part of `task_000264`'s failure is a genuine model SQL-reasoning slip
(self-inclusion) and a subtle test-string expectation. The harness fix does not
inject SQL knowledge — it strengthens the *verification discipline* so the model
re-derives and byte-compares its own output, a general capability. If R3 shows
the cluster unchanged on the same exact-match assertions, the residue is a pure
capability gap; revert to the stock processor per the rollback trigger. No
previously-passing task loses a mechanism, so regression risk is low.

## Round 3 — lossy-source extraction + real-input validation discipline

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-28T15:00:00Z
hypothesis_id: h_ocr_extraction_discipline_v1
levers: [instruction]
predicted_affected: [task_000015_89886d8d, task_000505_50b5162d, task_000536_9c16e8ef]
cited_candidates: [C-001]
gating_outcome: reverted
gating_attribution: score=27/50; +2/-4 gained=task_001031_a8f0eb37,task_001706_24462a09 lost=task_000578_cebe85a5,task_001089_220cc46b,task_001673_86224c91,task_001701_95e3bbcb; score 0.5400 < incumbent(mean) 0.6200 - tol 0.0400 -> revert to R0
expected_global_gain: "Flip/relieve the OCR->script cluster (>=3 tasks: 015 migrate, 505 SSH-key detector, 536 audit script) by teaching the agent to exhaust standard OCR remedies (upscale/fix DPI, re-binarize, multi-PSM) before inferring a schema from samples, and to validate a built parser/script end-to-end against the REAL task inputs rather than self-fabricated echoes."
regression_risk: "Low — prompt-only append; removes no capability, changes no processor. Worst case a few extra image-preprocessing / real-input Bash calls on already-passing tasks. The two passing OCR tasks (338, 1652) already extract cleanly, so guidance only reinforces their habit."
cost_shift: "Small positive — a handful of extra preprocessing/verification Bash calls on extraction tasks; likely net-neutral-to-favourable by converting silent wrong-answer finishes into corrected deliverables and pre-empting the output-token repetition spiral seen when the agent has no remedy to reach for."
rollback_trigger: "If next round is flat/down AND task_000015/505/536 stay F on the same accuracy/format assertions (pure OCR-model-capability limit), OR any previously-passing OCR task (338, 1652) or short non-OCR task regresses to F — revert to the R0 minimal prompt."
-->

### Why

Assigned focus `task_000015_89886d8d` (software_engineering, reward=0,
exit_reason=done, 28 steps): built `/home/user/migrate.py` (OCR a routing-schema
image into URL→JSON mapping rules) that scored accuracy 0.6678 vs the 0.98
threshold on a hidden 2000-URL dataset. Root chain: `tesseract` on the raw
800×400 image emitted `Invalid resolution 0 dpi. Using 70 instead` and garbage
(`eatalogyitem`, `Wept=<depariment>`, `esss_id`). The agent fiddled with
contrast/threshold/PSM ~7 times but never applied the actual fix — upscaling the
image to raise effective DPI — then hit the output-token limit in a repetition
loop (msg 288 truncation notice), gave up, and *guessed* a 3-route schema from
the 4 sample URLs. It then "verified" only against those self-consistent samples
(a guaranteed false pass) and declared done, so URL-encoding / extra-param /
edge-case handling on the hidden set was never exercised — the exact 0.6678→0.98
gap. This is not a missing tool: tesseract, PIL and Bash all work, and the two
`tesseract` tasks that PASS (338, 1652) extract cleanly with the same toolset.
It is a strategy/knowledge gap the instruction lever can close generally, and it
recurs across the OCR→script cluster (505 hardcoded a garbled SSH key tested
against its own echo → `2 of 2 evil bypassed`; 536 OCR'd an employee id in one
shot, failed a CSV quoting/format check verifiable against the real DB).

### Changes

- `system_prompt.txt` (sidecar, read by `SiblingSystemPromptBuilder`) — append
  two general disciplines: (1) lossy-source extraction — treat OCR/scan output as
  unverified; if garbled, exhaust standard remedies (fix DPI by upscaling,
  re-binarize/denoise, try multiple PSM modes, compare attempts) and cross-check
  extracted values against expected shape + other evidence BEFORE inferring from
  a couple of samples; (2) validation — test a built script/parser end-to-end
  against the REAL task inputs, probe edge cases the samples don't cover
  (encoding, missing/extra params, boundaries), and confirm exact output paths +
  format before stopping. No task literals, constants, or copy-paste code.
- `config.yaml` — byte-identical to R0 (still `SiblingSystemPromptBuilder`); the
  change is entirely in the sidecar prompt. Confirmed R0 running prompt is the
  minimal 5-line `DEFAULT_TMAX_PROMPT` (no sidecar existed in R0/R2), so this is
  net-new instruction, not a duplicate of any live guidance.

### Evidence

- `task_000015_89886d8d` result.json: `reward=0`, accuracy `0.6678 < 0.98`;
  messages msg 22 OCR `Invalid resolution 0 dpi` + garbled text; msg 288
  output-token repetition-loop truncation; msg 296 "proceed with the mapping I
  can infer from the sample URLs"; msg 330 verify only against 4 sample URLs.
- `task_000505_50b5162d`: single `tesseract /app/evidence.png stdout`; garbled
  ed25519 key hardcoded + tested against a self-echoed string → verifier
  `2 of 2 evil bypassed`.
- `task_000536_9c16e8ef`: single `tesseract /app/target_memo.png stdout`;
  extracted id fed straight into `run_audit.sh`; final_pytest a CSV quoting miss.
- Passing counter-cluster: `task_000338_27d6a1be`, `task_001652_86e1d185`
  (`reward=1`) — clean extraction with the same toolset (338 simpler artifact;
  1652 iterates with alternate tools), showing capability is present.

### Uncertainty

If the upscaled image still OCRs to garbage the residual is a pure model/OCR
capability limit and 015 stays F; the format cross-check (505 ed25519 prefix,
536 CSV format) is more mechanical and likelier to land. Append-only prompt
guidance cannot itself remove a mechanism, so regression risk is low; watch for
step-count inflation on easy tasks and for any regression on 338/1652. If the
cluster is unchanged on the same assertions next round, revert to R0 prompt per
the rollback trigger.


## Round 3 — break degenerate low-information tool-call loops

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-28T15:00:00Z
hypothesis_id: h_degenerate_loop_breaker_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_000958_4bb2b05d, task_001706_24462a09, task_001031_a8f0eb37]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flip/relieve the degenerate-loop failing cluster (>=4 tasks) where the model cycles on the same completed tool call(s) with identical results, making no new observations; content-agnostic so it generalizes."
regression_risk: "Very low — append-only on the tool result (CustomEditToolProcessor contract), contract-clean, 0 of 29 passing tasks in R2 hit the window=6/distinct<=2 condition so no passing task ever sees the note."
cost_shift: "Net decrease — breaks loops at ~6th repeated result instead of running to the step-80 wall, reclaiming tens of steps per stuck task; ~90-token note fires only inside a loop."
rollback_trigger: "If R4 shows cited loopers still reward=0 with the loop unbroken (nudge ignored) and no passing regression, escalate (lower refire_gap or add hard loop-terminate) rather than reship; if any previously-passing task regresses to F, revert."
-->

### Why

Assigned focus `task_000010_644ab1c2` (system_administration, reward=0,
exit_reason=done, steps=65). The task requires a script at `/home/user/operator.py`,
but that filename shadows Python's stdlib `operator` module — so any `python3`
process with `/home/user` on `sys.path[0]` triggers a circular import
(`from operator import eq` inside `collections`). final_pytest fails with that
exact circular-import traceback.

The agent correctly *diagnosed* the shadowing but never escaped it. It fell into a
byte-identical 2-cycle (messages.json msgs 45–54): tool_call
`rm operator.py && ln -sf k8s_operator.py operator.py && python3 -c "import operator"`
→ `OK`, alternating with `cd /tmp && python3 k8s_operator.py` → the same traceback,
9× each, until the run ended with no working deliverable.

Root harness gap: nothing in R0 intercepts a loop of *completed* tool calls whose
results never change. `LengthTruncationRecoveryProcessor` only handles
`finish_reason=="length" and not tool_calls` (no-tool-call token-cap loops) — the
other loop shape in this same trajectory (msgs 58–84). The completed-tool-call loop
was uncovered. Sweep confirmed a recurring cluster: task_000958 (sqlite3 x13,
budget_exceeded), task_001706 (x12, budget_exceeded), task_001031 (x47, error).

Critically, task_000010's loop is a 2-cycle, so a naive "N consecutive identical
results" detector (max strictly-consecutive = 2) misses it. A rolling window of the
last 6 (command,result) fingerprints collapsing to <=2 distinct values catches both
the 2-cycle AND the back-to-back reruns. Empirically, 0 of 29 R2-passing tasks hit
this condition — the shape is exclusive to the looping/failing cluster.

Distinct from reverted R1 `h_step_budget_reminder_v1` (a step-fraction reminder,
control): this fires on the *repetition shape* of tool results, not on a budget
threshold, and is append-only on the result rather than injecting user messages.

### Changes

- `processors/degenerate_loop_breaker.py` — new `DegenerateLoopBreakerProcessor`
  (`MultiHookProcessor`, `_order=33`, after CustomEditToolProcessor(30), before
  CustomSelfVerifyProcessor(90)). Pairs each command with its result in
  `on_before_tool`/`on_after_tool`, maintains a rolling window of blake2b
  fingerprints; when distinct fingerprints in the window <= `distinct_max` it
  appends an escalating (soft→hard) "you are looping, change strategy" note to the
  tool result. Append-only, non-terminating, content-agnostic (no task ids/paths/
  syntax literals). Re-fires every `refire_gap` results so a stubborn loop is nudged
  again without spamming.
- `config.yaml` — register the processor via absolute `file://` path
  (window=6, distinct_max=2, min_window_fill=6, refire_gap=4). Rest byte-identical
  to R0; `system_prompt.txt` sibling copied unchanged.

### Evidence

- task_000010_644ab1c2 result.json: reward=0, exit_reason=done, steps=65;
  final_pytest circular-import traceback on `import operator` from `/home/user/operator.py`.
- task_000010 messages.json msgs 45–54: the `rm/ln/python3 -c` ↔ `python3 k8s_operator.py`
  2-cycle, 9× each. windows-that-would-fire=13.
- task_000958_4bb2b05d (budget_exceeded, max_consec_result=13), task_001706_24462a09
  (budget_exceeded, max_consec_result=12), task_001031_a8f0eb37 (error, dupcmd=47).
- Passing-task false-positive sweep: 0 of 29 reward=1 tasks would fire the detector.

### Uncertainty

If the model reads the note but cannot find the general escape (for 000010, the
stdlib-shadow escape is a real reasoning step — strip the script dir from sys.path or
run via a wrapper), 000010 may stay F even though the loop is broken; that residual is
a model reasoning limit, not a harness gap. For the budget_exceeded loopers the
reclaimed-budget benefit is more mechanical. Append-only design cannot itself regress a
passing task. If R4 shows the nudge too weak (loops persist), escalate cadence or add a
terminate-on-loop guard per rollback_trigger.

## Round 3 (c3) — escalate on verbatim length-truncation loop

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-28T15:00:00Z
hypothesis_id: h_stuck_truncation_escalator_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Relieve the verbatim length-truncation loop cluster (task_000118 deployment_monitor daemon, task_000010 k8s operator) where the passive length-recovery nudge fails to break the loop and the deliverable is never exercised end-to-end; reclaim tens of burned steps and force a real execution before a voluntary exit."
regression_risk: "Low - fires only on hashed content-identity across 3+ consecutive finish_reason=length no-tool-call turns (a shape healthy tasks never show); append-only (+0 when last role is user else +1), one-shot per run, never terminates, no system-prompt mutation."
cost_shift: "Net decrease - breaks the loop at the 3rd identical truncation instead of running to the step wall (118 burned ~50 of 79 steps looping); directive ~120 tokens, fires at most once."
rollback_trigger: "If R4 shows task_000118/task_000010 still F with the same identical-truncation loop unbroken, OR any previously-passing task regresses to F, revert. If loop breaks but 118 still fails on the log-size peak, residual is a monitor-logic capability gap - keep the escalator, note the gap."
-->

### Why

Assigned focus task_000118_3043e92d (system_administration, reward=0,
exit_reason=done, no_tool_calls, 79 steps). Task: a deployment_monitor.py
daemon that must keep /home/user/logs under 40 MB via SIGSTOP/truncate/SIGCONT;
verifier ran the real run_deployment.sh and peak log size hit 209715200 bytes
(over the 45 MB threshold). The dominant run pathology is a verbatim
length-truncation loop: the model re-emitted byte-identical narration, hit
max_tokens with no tool call, got the passive continue nudge, and repeated - 14
continue-nudges in the transcript; the same paragraph emitted ~10x. The
existing LengthTruncationRecoveryProcessor fired repeatedly (its escalating
NUDGE_REPEAT is the text the model quotes back) yet never broke the loop; ~50 of
79 steps consumed. At the forced self-verify (msg 59) the agent re-read the
source (msg 63) and rubber-stamped it (msg 64) - it never once executed the
deliverable against the real simulator, so the peak was never observed. Same
shape in task_000010_644ab1c2 (k8s operator, 65 steps).

Harness gap: the length-truncation recovery is a passive text nudge; when the
model ignores it the loop persists to the step wall with the deliverable never
run. No runtime hook escalates, on demonstrated non-landing, from
narration-redirect to a concrete write/EXECUTE directive.

### Changes

- processors/stuck_truncation_escalator.py - new StuckTruncationEscalator
  (MultiHookProcessor, _order=7, after LengthTruncationRecoveryProcessor at 5).
  Fingerprints assistant content on each finish_reason=length no-tool-call
  turn; when the same fingerprint repeats hard_repeat (3) times in a row,
  escalates ONCE with a strong directive: abandon narration, issue one Bash
  command that writes the required output and/or EXECUTES the deliverable
  end-to-end against the real inputs the task names, then observe the real
  result. Content-agnostic, append-only, non-terminating.
- config.yaml - register via absolute file:// path immediately after
  LengthTruncationRecoveryProcessor; rest byte-identical to R0; system_prompt.txt
  sibling copied unchanged.

### Evidence

- task_000118_3043e92d result.json: exit_reason=done, steps=79, reward=0;
  final_pytest "Peak log directory size was 209715200 bytes, which exceeds the
  threshold of 45000000 bytes".
- task_000118 messages.json: 14 "cut off by the token limit. Please continue"
  nudges; Counter over assistant contents shows the same
  "The user is telling me to stop the repetition..." text at counts 4/3/3;
  msg 59 self-verify, msg 63 re-read source, msg 64 "looks correct" -
  run_deployment.sh never executed by the agent.
- task_000010_644ab1c2 messages.json final turns: repeated "I've been stuck in a
  loop... Please continue" truncation cycle; deliverable unfinalized.

### Uncertainty

Distinct from pending R1-c5 h_stuck_result_breaker_v1 (tool-RESULT identity) and
the sibling R3 h_degenerate_loop_breaker_v1 (completed tool-call 2-cycle, which
explicitly excludes the finish_reason=length no-tool-call loop this targets), and
from R2 h_output_contract_verify_v1 (format audit). If the model ignores even the
hard directive the loop persists and the task stays F, but the append-only design
cannot regress a passing task. If the loop breaks yet 118 still fails on the
log-size peak, the residual is a monitor-logic model capability gap; keep the
escalator (it reclaimed budget and forced a real run) and note the gap. Watch
task_000010/118 attribution and any long-horizon passing task for T->F.

## Round 3 — provision HTTP verifier dependency (requests)

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-28T15:00:00Z
hypothesis_id: h_http_verifier_dep_guard_v1
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000958_4bb2b05d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flip the verifier-side `import requests` collection-error cluster (>=2 HTTP-service tasks): the grader pytest module does `import requests` at collection time; when the container lacks it the test never collects and the task scores 0 regardless of correctness. Generalizes to any HTTP-service deliverable graded via Python requests."
regression_risk: "Low — drop-in replacement for CustomSelfVerifyProcessor; append-only, one-shot, byte-identical to stock on non-HTTP runs (regex gate suppresses the extra item). Worst case one redundant `pip install requests` (~seconds) on an HTTP task that already had it. No message removed, no process killed, no schema/knob changed; contract check 0 violations."
cost_shift: "Negligible; one conditional ~150-token checklist item + at most one short `pip install` on HTTP-service tasks. Net-favorable: converts guaranteed-0 verifier-collection failures into gradable runs."
rollback_trigger: "If R4 shows task_000028/task_000958 still failing with the same `ModuleNotFoundError: No module named 'requests'` collection error (nudge not acted on), OR any previously-passing HTTP-service task regresses, revert to stock CustomSelfVerifyProcessor."
-->

### Why

Assigned focus task_000028_7fe033ac failed with reward=0 despite the agent
FULLY and correctly solving the task: it fixed the nginx upstream socket,
rewrote the C++ server to compute the real ffprobe frame count and listen on
the correct UNIX socket, resolved the nginx→socket permission-denied 502, wrote
a correct logrotate.conf, started both services, and confirmed
`Status: 200 / Body: '150'` end-to-end through nginx. The task nonetheless
scored 0 because the *verifier* (`/tmp/test_final_state.py`, injected after the
agent exits) does `import requests` at collection time and the container has no
`requests` installed: pytest rc=2, "Interrupted: 1 error during collection",
`ModuleNotFoundError: No module named 'requests'`. task_000958_4bb2b05d shows
the identical collection ImportError. Both are HTTP-service tasks. This is a
harness discipline gap, not a model capability gap: the agent even discovered
the box had no HTTP clients at all (`curl: command not found`, `wget failed`,
`nc` absent) and fell back to python urllib — a clear cue the environment lacked
the HTTP tooling the verifier depends on — but never provisioned `requests`.
Outbound installs work in these containers (task_000684 pip-installed
numpy/scipy, task_001818 torch/whisper, task_001652 pytesseract), so
`pip install requests` closes the gap.

### Changes

- `processors/http_verifier_dep_guard.py` — new `HttpVerifierDepGuardProcessor`
  (`MultiHookProcessor`, singleton group `tb2_self_verify`, order 90). Drop-in
  replacement for `CustomSelfVerifyProcessor`: identical one-shot exit-verify
  mechanism (keepalive synthetic tool call + one appended user message). Tracks
  HTTP-service signals in `on_before_tool` via a content-agnostic Bash regex
  (nginx/proxy_pass/httplib/flask/uvicorn/http.server/HTTP/1.x/loopback host:port/
  urllib/requests.*). On exit-intent, if a signal was seen, appends one extra
  checklist item telling the agent to ensure `requests` is importable
  (`python3 -c "import requests" || pip install requests`). On non-HTTP runs the
  checklist is the stock text.
- `config.yaml` — swap the final pipeline entry
  `benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor` for a
  `file://…::HttpVerifierDepGuardProcessor` reference. Rest byte-identical to R0.
  `system_prompt.txt` sibling copied unchanged.

### Evidence

- task_000028_7fe033ac result.json: reward=0, `exit_reason=done`,
  `finished=no_tool_calls`, steps=25; final_pytest output_tail =
  `import requests` → `ModuleNotFoundError: No module named 'requests'`,
  rc=2, "Interrupted: 1 error during collection".
- task_000028 messages.json: final HTTP test `Status: 200 / Body: '150'`
  (task functionally complete); earlier `curl: command not found`,
  `wget failed`, only `/usr/bin/python3` present.
- task_000958_4bb2b05d result.json: identical `import requests`
  ModuleNotFoundError collection failure (test line 4), reward=0.
- Install viability: task_000684 "Successfully installed numpy ... scipy",
  task_001818 torch/whisper from PyPI, task_001652 pytesseract — outbound pip
  works in these containers.

### Uncertainty

If the model reads the nudge but declines to install `requests` (e.g. treats it
as out of scope because the task text doesn't mention it), the two tasks stay F —
the item is worded to override that ("Do this even though the task text may not
mention it"). If the HTTP regex misses a service shape it won't fire, but that is
a no-op, not a regression. Append-only design cannot itself regress a passing
task. If R4 shows the cluster unchanged with the same requests ImportError,
escalate to a Control hook that runs the install directly at exit; if any HTTP
task regresses, revert per rollback_trigger.

## Round 4 (c5) — noop: task_000264 residual is a capability gap already in-flight

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-28T16:00:00Z
hypothesis_id: h_noop_task264_capgap_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=30/50; +5/-2 gained=task_000578_cebe85a5,task_000587_9862bb19,task_001089_220cc46b,task_001090_c61c71f2 lost=task_001031_a8f0eb37,task_001706_24462a09; score 0.6000 >= incumbent(mean) 0.6133 - tol 0.0400
expected_global_gain: "None — explicit no-op. The one valid harness lever for this task's failure shape (strengthened output-contract self-verify) is already in flight as R2 h_output_contract_verify_v1 (pending); re-shipping it would collide with that proposal and pile a 4th control processor on a cluster the scoreboard shows is saturated (control 3 attempts / 0 accepted / 1 reverted)."
regression_risk: "None — config is byte-identical to R0."
cost_shift: "Zero."
rollback_trigger: "N/A (noop)."
-->

### Why

Assigned focus `task_000264_ab8c7253` (data_querying, reward=0, exit_reason=done,
finished=no_tool_calls, 17 steps — a voluntary exit). Two grader failures, both
model-capability gaps rather than harness deficiencies:

1. **Off-by-one recursive CTE.** The base case seeds `e.id AS subordinate_id`
   (every employee is its own subordinate), so all counts are +1. Grader:
   `At index 0 diff: 'Alice (CEO),12' != 'Alice (CEO),11'`. Alice's count of 12
   equals total headcount (12) — a semantic red flag the model didn't catch. This
   is a SQL-reasoning slip; a harness processor cannot inject the correct
   recurrence.
2. **`USING INDEX` substring miss.** Grader asserts the literal substring
   `"USING INDEX" in content`, but sqlite emits `USING COVERING INDEX` /
   `AUTOMATIC COVERING INDEX` for any index on `manager_id` (verified locally with
   sqlite 3.37.2: the query only touches `manager_id`/`id`, so every candidate
   index is *covering* → plan never contains the bare `USING INDEX` token). To
   satisfy the grader the agent would need a sqlite-specific trick to force a
   non-covering plan — pure domain knowledge, not a harness lever.

The stock `CustomSelfVerifyProcessor` DID fire (msg 30 verification-check) and the
agent rubber-stamped (msgs 31-33: re-`ls`'d files, re-eyeballed its own numbers,
never recomputed). Strengthening that self-verify checklist is exactly the
in-flight R2 hypothesis `h_output_contract_verify_v1` (byte-for-byte format audit +
independent re-derivation), which is `pending` and lists task_000264 as its primary
predicted_affected. That is the correct and only defensible harness lever for this
shape.

### Decision

Explicit no-op. Per the brief ("if your focus turns out to be unsupported by the
trajectories … make the smallest defensible edit rather than drifting onto another
proposal's territory") and SOUL.md's capability-gap rule, I ship R0 byte-for-byte.

Rationale for not shipping a change:
- The residual failure is two model-capability gaps (SQL self-inclusion + sqlite
  plan-string knowledge); a Control/Instruction lever cannot supply either without
  embedding task-specific SQL knowledge (banned).
- The one legitimate harness lever (output-contract self-verify discipline) is
  already in flight as R2 `h_output_contract_verify_v1` (pending) — re-proposing it
  is a novelty/territory collision and would confound its attribution.
- Lever scoreboard: control = 3 attempts, 0 accepted, 1 reverted. Piling a 4th
  overlapping control processor on this exact cluster is the textbook
  "tried repeatedly without flipping → look elsewhere" signal.

### Evidence

- `task_000264_ab8c7253` result.json: `final_pytest` `At index 0 diff:
  'Alice (CEO),12' != 'Alice (CEO),11'` and `'USING INDEX' not in
  'QUERY PLAN … SCAN E USING COVERING INDEX …'`.
- messages.json msg 9: CTE base case `SELECT e.id as manager_id, e.id as
  subordinate_id FROM employees` (self-inclusion). msg 30 `_tb2_self_verify` fired;
  msgs 31-33 re-`ls`/re-eyeball, no recomputation.
- Local sqlite 3.37.2 repro: index on `manager_id` (plain or wide) yields
  `SCAN … USING COVERING INDEX` / `SEARCH … USING COVERING INDEX` — never the bare
  `USING INDEX` token the grader requires.

### Uncertainty

If R2's `h_output_contract_verify_v1` lands and flips task_000264, this confirms
the self-verify discipline was the right lever and no further action is needed. If
R2 is reverted or leaves task_000264 F on the same two assertions, the residual is
confirmed as a pure capability gap (SQL recurrence + sqlite plan-string knowledge)
that no harness lever should chase — log and skip. NEEDS_FROM_HUMAN candidate: the
`USING INDEX` grader assertion is arguably an over-strict test (rejects a correct
covering-index optimization); if graders are tunable, relaxing to
`"USING" and "INDEX"` or accepting `COVERING INDEX` would fix a false-negative.

## Round 4 — computed-result plausibility + full-range compare

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-28T16:00:00Z
hypothesis_id: h_computed_result_plausibility_v1
levers: [instruction]
predicted_affected: [task_000396_e56917e2, task_001653_c4cafa73, task_001937_ac874115]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Relieve the 'declared done on a glaringly wrong self-computed value' cluster (>=3 tasks across scientific_computing + data_science) by making magnitude-plausibility and full-range/tail comparison an explicit pre-exit discipline; generalizes to any reproduce/validate/optimize task."
regression_risk: "Low - prompt-only append to the minimal R0 sidecar; removes no capability, changes no processor. Worst case a few extra comparison Bash calls on already-passing compute tasks (000338, 001088 extract/compute cleanly, so guidance only reinforces their habit). Append-only guidance cannot remove a mechanism."
cost_shift: "Small positive - a handful of extra tail/edge comparison Bash calls on compute tasks; likely net-neutral-to-favourable by converting silent wrong-answer finishes into corrected or honestly-flagged work."
rollback_trigger: "If next round is flat/down AND task_000396/001653/001937 stay F on the same value mismatches (residual is pure model numerical capability), OR any previously-passing compute task (000338, 001088, data_science 000578/000760/001498/001697) regresses to F - revert to the R0 minimal sidecar prompt."
-->

### Why

Assigned focus `task_000396_e56917e2` (scientific_computing, reward=0,
exit_reason=done, no_tool_calls, 30 steps). Task: fix a vendored RK45 lib's
build + step-size bug, run the sim, compute the maximum absolute deviation of
`output.dat` vs an analytical `reference.dat`, write it to
`/home/user/validation.log`; verifier asserts `0.0 < max_dev < 0.1`. The agent
fixed the two obvious bugs (`-lm`; exponent `-0.25 -> 0.25`), computed
**max_dev = 0.607**, wrote it, and declared done. The vendored integrator is a
forward-Euler step ("using Euler for demonstration"), unconditionally unstable
for an oscillator: the reference stays on the unit circle (amp ~1.0) while the
agent's output diverges to amp ~1.64 by t~10 - the sign fix was necessary but
NOT sufficient (a stable RK4/RKF is needed). The agent had its own 0.607 metric
in hand - the smoking gun - but "verified" by spot-checking only the first 4
(easy) leading time steps and re-listing files, never comparing the divergent
tail or questioning the magnitude. Same shape recurs: task_001937 reported
"Optimal Grid: 60" (expected 50); task_001653 emitted Distance 18.6199
(expected 11.3444). Shared root: agent treats "produced a number / script ran"
as success and does not sanity-check magnitude or compare across the full range.
Implementing a stable integrator is a model capability gap (not patched); the
verification discipline is a general, harness-addressable gap.

### Changes

- `system_prompt.txt` (sidecar, read by `SiblingSystemPromptBuilder`) - append a
  general "computed-result plausibility + full-range comparison" discipline:
  judge whether a computed value/metric is plausible in magnitude/sign; when
  matching/validating/minimizing error against a reference, a large residual or
  out-of-range value means the computation is still wrong (fix root cause, don't
  record the bad value); compare across the FULL range incl. hardest/tail/edge
  samples, not just the easy leading ones; a compile-error or sign-bug fix is
  not necessarily the complete fix - re-run end-to-end and confirm the
  correctness criterion before finishing. No task literals, tolerances, or
  formulas.
- `config.yaml` - byte-identical to R0 (still `SiblingSystemPromptBuilder`); the
  change is entirely in the sidecar prompt. Instruction lever only. Confirmed R0
  running prompt is the minimal 5-line `DEFAULT_TMAX_PROMPT`, so this is net-new
  guidance, not a duplicate of any live rule.

### Evidence

- `task_000396_e56917e2` result.json: reward=0, exit_reason=done;
  final_pytest `max deviation 0.6072 is not within (0.0, 0.1)`. messages msg 441
  `compare.py` -> `0.6072399999999999`; msg 449 writes it verbatim; msgs 495-521
  "verify" only t in {0, 0.01, 0.0534, 0.1437}; msg 531 `_tb2_self_verify` fires;
  msgs 549-583 re-ls/cat + re-declare success. Divergence verified: ref
  t=9.94 -> (-0.870, 0.493) amp 1.0; output t~9.94 -> (-1.447, 0.765) amp 1.64.
- `task_001937_ac874115` final_pytest: `Expected Optimal Grid to be 50, but got 60`.
- `task_001653_c4cafa73` final_pytest: `Centroid/Distance 18.6199` != expected
  `11.3444`.
- Passing counter-cluster: `task_000338_27d6a1be`, `task_001088_6f566806`
  (reward=1) - clean compute/extraction with the same toolset; the rule only
  reinforces their habit.

### Uncertainty

For task_000396 the flip is not guaranteed: after flagging 0.607 as wrong the
agent must still author a stable integrator (a model capability limit); the
discipline converts a silent wrong-answer finish into an honest not-yet-correct
signal and is a fair-run improvement regardless. task_001937 (recompute the
grid) and task_001653 (recompute the report values) are more mechanical and
likelier to land once the agent stops rubber-stamping obviously-off numbers.
Append-only prompt guidance cannot itself regress a passing task; watch for
step-count inflation on easy tasks and any regression on 000338/001088 or the
passing data_science cluster. If the cluster is unchanged on the same
value-mismatch assertions next round, revert to the R0 minimal prompt.

## Round 4 — polarity-neutral lifecycle self-verify

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-28T16:00:00Z
hypothesis_id: h_lifecycle_selfverify_polarity_v1
levers: [control]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flip the clean-shutdown witness (task_000140) and remove a latent harness harm: the stock self-verify checklist's one-sided 'confirm services still ALIVE' wording actively mis-nudges every teardown-polarity lifecycle task (graceful shutdown / no lingering processes / resource release)."
regression_risk: "Very low — the persist branch of checklist item 5 is preserved in meaning, so service-must-stay-alive tasks are unaffected; message-count contract is identical to stock CustomSelfVerifyProcessor (+1 user, one-shot, order 90), no new firing conditions."
cost_shift: "Negligible — item 5 is ~2 sentences longer; at most one extra pgrep/kill Bash call on teardown-polarity tasks that would otherwise fail. Net-favourable (converts silent lingering-process failures into corrected clean exits)."
rollback_trigger: "If next round shows task_000140 still F on test_no_lingering_service_processes with the process table unreconciled (nudge ignored -> model reasoning limit), OR any previously-passing service-must-stay-alive task regresses (service killed/unreachable), revert to stock CustomSelfVerifyProcessor."
-->

### Why

Assigned focus `task_000140_01c78b42` (system_administration, reward=0,
exit_reason=done, finished=no_tool_calls, 11 steps) failed only on
`test_no_lingering_service_processes`: `pgrep -f vm_service` found lingering
PIDs at grader time (`['344','591']`, then `['344','591','787']` on the
second grader run). The agent correctly fixed all three files, ran its own
`test_pipeline.sh` (which starts `./vm_service &` then `kill -TERM $PID`),
saw curl 200 + the log written, and declared done. The stock
`CustomSelfVerifyProcessor` DID fire, but its checklist item 5 is one-sided
— "For running services: confirm they are still alive and reachable right
now" — the exact wrong polarity for a clean-shutdown task. It gave the agent
no reason to reap the test-run service, so the container's final live state
kept the process the grader inspects. This is a harness-level polarity bug:
the same wording is a latent regression risk for ANY task whose required end
state is "stopped / no lingering processes", not just task_000140. Sweep
confirmed the `no_lingering` grader is a single witness this batch, but the
mis-nudge mechanism is general and lives in the harness, not the task.

### Changes

- `processors/lifecycle_self_verify.py` — new `LifecycleSelfVerifyProcessor`,
  a drop-in replacement for `CustomSelfVerifyProcessor`: byte-identical
  firing mechanics (singleton group `tb2_self_verify`, `_order=90`,
  synthetic keepalive tool call + deferred +1 user message on no-tool-call
  exit intent), with checklist item 5 rewritten to be polarity-neutral —
  reconcile the *required* end state (persist OR teardown), and if the task
  requires a stopped/clean state, confirm via `ps`/`pgrep` that nothing
  remains, explicitly including any process started while self-testing. No
  task ids / paths / service names embedded.
- `config.yaml` — swap the final pipeline entry
  `benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor` for a
  `file://…::LifecycleSelfVerifyProcessor` reference. Rest byte-identical to
  R0; `system_prompt.txt` sidecar copied unchanged.

### Evidence

- `task_000140_01c78b42` result.json: `reward=0`, `exit_reason=done`,
  `finished=no_tool_calls`, steps=11, `initial_pytest.passed=true`;
  final_pytest fails only `test_no_lingering_service_processes`
  (`Lingering vm_service processes found: ['344','591']`).
- `task_000140` messages.json: msg 117 `test_pipeline.sh` ends
  `kill -TERM $PID`; msg 157 ran `bash test_pipeline.sh` (starts
  `./vm_service &`); msg 213 `_tb2_self_verify` fired; msgs 229–250 agent
  re-`ls`'d files and re-declared success with no post-run `ps`/`pgrep`.
- Stock checklist (harness.py L96): "For running services: confirm they are
  still alive and reachable right now" — one-sided persist polarity.
- Cluster sweep: 028/958 fail on verifier `ModuleNotFoundError: requests`;
  118 log-size; 1706 redis-log; 1937 report-value — none share the
  clean-shutdown mechanism, confirming 140 is the single grader witness but
  the checklist polarity bug is harness-general.

### Uncertainty

If the model reads the corrected item 5 but cannot reason that its
test-run service must be reaped, task_000140 stays F (residual is model
reasoning, not harness). Distinct from the still-pending R1(c4)
`h_bg_state_reconcile_v1`, which added a *separate* processor with regex
launch-detection and an extra injected message; this candidate is a smaller,
different shape — it corrects the polarity of the message the harness already
sends, adding zero net messages and zero new firing conditions. If next round
is flat with 140 still F on the same assertion, revert per rollback_trigger.


## Round 4 (c7) — guard self-fabricated-test tautology (Control)

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-28T16:30:00Z
hypothesis_id: h_self_fabricated_test_guard_v1
levers: [control]
predicted_affected: [task_000505_50b5162d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Close the self-fabricated-test false-pass mechanism for detector/classifier/parser deliverables: agent validates a built script only against input files it manufactured from a derived value (a tautology that passes even when the value is wrong), never exercising the real on-disk artifacts. Primary target task_000505; the discipline generalizes to any task with ground-truth inputs on disk."
regression_risk: "Very low. Append-only note on the tool result (mirrors CustomEditToolProcessor contract), never terminates, never mutates the system prompt, fires <=2x/run. Trajectory sweep over R2: fires on exactly 1 of 29 passing tasks (task_000760, a reproducibility probe with a self-made CSV) - harmless because the note is purely additive and 760 also has the real binary available. No passing task can be broken by extra advisory text."
cost_shift: "Negligible-to-favourable. One ~120-token note (<=2x) only on the tautology shape; may add a few Bash calls to inspect real artifacts on targeted tasks, converting silent wrong-answer finishes into corrected deliverables."
rollback_trigger: "If next round shows task_000505 still F on the same '2 of 2 evil bypassed' assertion AND the note demonstrably fired (nudge ignored -> residual is a pure OCR/model capability limit), OR any previously-passing task regresses to F (esp. task_000760), revert to R0 pipeline."
-->

### Why

Assigned focus task_000505_50b5162d (security, reward=0, exit_reason=done,
no_tool_calls, 14 steps). The agent OCR'd an ed25519 SSH key from
/app/evidence.png, getting a GARBLED string (correct prefix AAAAC3NzaC1lZDI1NTE5
misread as AAAAC3NzaC1IZDIINTES: l->I, 1->I, 9->S, plus stray spaces around +).
It hardcoded that garbled key into /home/user/detect_trojan.sh, then validated
the script by creating /tmp/malicious_test.txt via echo-ing the SAME garbled
string and running detect_trojan.sh /tmp/malicious_test.txt -> exit 1 ("works").
This is a self-fabricated-test TAUTOLOGY: a test input manufactured from the
derived value can only ever pass, so the misread was never exposed. The real
trojaned binaries (the evil corpus, present on disk) contain the CORRECT key and
were never run through the script; the verifier's real run yielded
"2 of 2 evil bypassed".

Lever choice: the instruction lever has ALREADY targeted this exact discipline on
this exact task twice - R1(c7) h_verify_real_artifacts_v1 (accepted) and R3
h_ocr_extraction_discipline_v1 (pending), both telling the agent in the system
prompt to validate against real inputs and distrust lossy-source values.
task_000505 still failed. Per the analyze skill's cross-round rule (a lever tried
repeatedly on the same cluster without flipping -> look elsewhere), a third prompt
rewrite is the wrong move. The gap is not knowledge, it is DELIVERY: a
session-start prompt sentence loses to the model's over-confidence, whereas a
Control hook fires the correction at the DECISIVE moment (right after the
tautological test run, injected into tool-result context). Distinct shape and
distinct lever from both prior hypotheses.

### Changes

- processors/self_fabricated_test_guard.py - new SelfFabricatedTestGuardProcessor
  (MultiHookProcessor, _order=34, after CustomEditToolProcessor(30), before
  CustomSelfVerifyProcessor(90)). In on_before_tool it (1) records every path the
  agent WRITES to (heredoc / redirect / tee / sed -i, reusing edit-guard
  semantics) and (2) parses each execution segment; when a segment EXECUTES an
  agent-authored deliverable (interpreter+script, ./script, or a written *.sh)
  whose file-path arguments are ALL agent-fabricated (excluding this run's own
  stdout/redirect outputs) and NONE pre-existing, it flags the call.
  on_after_tool appends a one-shot corrective note (you tested against a file you
  fabricated; run against the ORIGINAL on-disk artifacts; cross-check
  lossy-source values against the real target). Append-only, non-terminating,
  content-agnostic (no task ids / paths / literals). Fires <=2x/run.
- config.yaml - register the processor via absolute file:// path at order 34;
  rest of the pipeline byte-identical to R0. system_prompt.txt sidecar copied
  unchanged (Control-only change).

### Evidence

- task_000505_50b5162d result.json: reward=0, exit_reason=done,
  finished=no_tool_calls, steps=14; final_pytest "2 of 2 evil bypassed:
  cat_evil, ls_evil".
- messages.json msg 7 (tool result): tesseract emitted
  "ssh-ed25519 AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8"
  (garbled). msg 9 hardcoded it into the script. msg 15/17: created
  /tmp/malicious_test.txt by echoing the same garbled string, ran
  detect_trojan.sh /tmp/malicious_test.txt -> exit 1; msg 19 final-verification
  ran the script against /tmp/test_binary (also fabricated with the derived key).
  The real evil corpus was never inspected.
- Detector dry-run over this trajectory: FIRES on the msg-19 fabricated-input run;
  a synthetic real-input run (detect_trojan.sh /app/evidence.png) is correctly
  SILENT (real_data present).
- False-positive sweep over all 29 R2 passing tasks: fires on exactly 1
  (task_000760, reproducibility probe with a self-made /tmp/test.csv) - additive
  note only, cannot break the pass.

### Uncertainty

Partial-yes retroactive check: after the nudge the agent's natural next step is
running strings on a real evil binary, which surfaces the CORRECT key and lets it
fix the hardcoded string - closing the false-confidence loop that PREVENTED
discovery. The residual OCR misread is a model limit; if the model reads the note
but still declines to inspect the real corpus, task_000505 stays F, but the
append-only design cannot itself regress anything. If next round is flat with 505
still F on the same assertion and the note fired, the residual is a pure
OCR/model-capability limit - revert per rollback_trigger.

## Round 5 — noop: task_000010 residual is a single-task capability gap; cluster saturated

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-28T17:00:00Z
hypothesis_id: h_noop_task010_shadow_capgap_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=29/50; score 0.5800 >= incumbent(mean) 0.6133 - tol 0.0400 (final-round scoring)
expected_global_gain: "None — explicit no-op. The r4 failure of task_000010 is a single-task Python-stdlib-shadow reasoning trap plus a self-inflicted give-up-on-the-required-path error; there is no 2-or-more-task systemic harness gap to close, and every legitimate lever for this shape is already in-flight (3 pending control processors explicitly list task_000010 as predicted_affected)."
regression_risk: "None — config is byte-identical to R0."
cost_shift: "Zero."
rollback_trigger: "N/A (noop). If none of the pending control processors (R3 h_degenerate_loop_breaker_v1 / R3 h_stuck_truncation_escalator_v1) flip task_000010 next round, the residual is confirmed a pure model-reasoning limit (finding the sys.path/cwd escape) — no harness lever applies; keep skipping."
-->

### Why

Assigned focus task_000010_644ab1c2 (system_administration, reward=0,
exit_reason=done, finished=no_tool_calls, 32 steps — a voluntary exit).
The task hard-requires a script at /home/user/operator.py; grader
test_operator_script_exists only checks os.path.isfile('/home/user/operator.py')
and the other two final tests pass. The failure is that at exit the required
file does not exist — the agent renamed it away and gave up.

Root chain (messages.json): msg 13 the agent wrote /home/user/operator.py; msg
17-19 running python3 -c "import pexpect" triggered a circular import because
operator.py in cwd /home/user shadows Python's stdlib operator module (any
stdlib chain subprocess to threading to operator breaks). The agent correctly
diagnosed the shadow (msg 19) and renamed to k8s_operator.py, then ran the
pipeline successfully as k8s_operator.py (msgs 35-44: backup created, both
manifests applied, socat forward up). The stock CustomSelfVerifyProcessor fired
(msg 45) and the agent even moved the file back to operator.py (msg 47) — but on
re-running from cwd /home/user it hit the same shadow traceback (msgs 49-54),
concluded the exact-path requirement was impossible (msg 51/55/57), and
re-renamed it away for good (msg 59), exiting with /home/user/operator.py absent.

The escape the agent missed is a real reasoning step, not a missing mechanism:
run the script from a non-/home/user cwd (cd /tmp then python3 /home/user/operator.py)
or strip sys.path[0], which sidesteps the shadow while keeping the file at the
required path. Even leaving the file in place unmodified would have passed
test_operator_script_exists.

### Decision

Explicit no-op. Two reasons this focus does not support a systemic harness change:

1. Idiosyncratic, not systemic. The Python-stdlib-shadow circular-import shape
   appears in exactly 1 of 50 r4 trajectories (grep of messages.json for
   circular import / partially initialized module → only task_000010). The other
   6 failing tasks with does-not-exist / not-found graders (000015, 000118,
   000536, 000748, 000933, 001653) are content-correctness or accuracy misses
   (wrong values, wrong bytes, CSV format), NOT deliverable-removed-from-path — so
   there is no 2-or-more-task cluster sharing this mechanism. Per analyze's
   systemic-vs-idiosyncratic filter, a single-task observation is a skip.

2. Cluster saturated plus wrong-lever record. Lever scoreboard: control = 5
   attempts / 0 accepted / 1 reverted; instruction = 2 / 0 / 1 reverted. task_000010
   specifically is already predicted_affected by THREE in-flight control processors
   (R1 h_step_budget_reminder_v1 [reverted], R3 h_degenerate_loop_breaker_v1
   [pending, targets its 2-cycle], R3 h_stuck_truncation_escalator_v1 [pending,
   targets its length-truncation loop]). Piling a 4th overlapping control/instruction
   change on this exact task would confound their attribution and is the textbook
   tried-repeatedly-without-flipping signal to look elsewhere. The one general
   discipline that would help (an exact output path is non-negotiable; never
   delete/relocate a required deliverable to dodge a tooling conflict — change the
   invocation instead) is real but (a) single-task-grounded here and (b) the R3
   instruction sidecar carrying exact-path guidance was net-negative and reverted
   (h_ocr_extraction_discipline_v1), so shipping more prompt path-discipline now
   carries live regression risk for ~1 speculative flip.

This mirrors the accepted R4 c5 precedent (h_noop_task264_capgap_v1): a single-task
capability-gap focus with the only valid lever already in flight → ship R0
byte-for-byte rather than drift onto another proposal's territory.

### Evidence

- task_000010_644ab1c2 result.json: reward=0, exit_reason=done, steps=32;
  final_pytest AssertionError: Operator script /home/user/operator.py does not
  exist. You must create it. (other 2 final tests pass).
- messages.json msg 19 "There's a naming conflict … Python is trying to import it
  when importing operator"; msg 47 mv k8s_operator.py operator.py; msgs 49-54
  circular-import traceback on import subprocess; msg 51 "this creates a circular
  import issue"; msg 59 mv operator.py k8s_operator.py (final — path left empty).
- Cluster sweep: grep -l "circular import" *.messages.json → only task_000010
  (1/50). does-not-exist / not-found grader sweep → 000015/000118/000536/000748/
  000933/001653 all content/accuracy misses, distinct mechanisms.

### Uncertainty

If either pending R3 control processor (loop-breaker / truncation-escalator) breaks
task_000010's loop next round, the agent may still fail on the same shadow-escape
reasoning step (a model limit), confirming no harness lever applies. If a future
round finds a second task with the same stdlib-shadow-then-abandon-path shape,
revisit as a genuine cluster (candidate lever: an on_after_tool detector for the
circular-import-via-cwd-shadow traceback that injects a general cwd/sys.path hint).
Config is byte-identical to R0, so this round cannot itself regress anything.

## Round 5 (c1) — upscale-before-OCR remedy on tesseract low-DPI signal

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-28T17:00:00Z
hypothesis_id: h_ocr_lowdpi_upscale_remedy_v1
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000505_50b5162d, task_000536_9c16e8ef]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Relieve the dense-OCR-extraction failing cluster (015 routing-schema table, 505 garbled ed25519 key, 536 garbled memo id) by delivering the one resolution remedy the agent never tries (upscale to raise effective DPI) at the exact moment tesseract reports low/zero DPI. Generalizes to any low-DPI tesseract task."
regression_risk: "Near-zero — append-only note on a tool result, runtime-gated to the tesseract low-DPI signal AND a tesseract-family command AND absence of an existing upscale token; fires <=2x/run. Inert on all non-OCR tasks (0 of 29 R2-passers invoke tesseract) and harmless to the 2 passing OCR tasks (338/1652 OCR short/clean text that survives upscaling). No prompt mutation, no removals, no termination."
cost_shift: "Negligible — one ~150-token note at most twice per OCR task; net-favourable if it converts a guessed schema into a correct one and pre-empts the sample-inference spiral."
rollback_trigger: "If next round shows task_000015 still F on the same accuracy assertion after an upscale+re-OCR attempt (residual = pure OCR-model limit), OR any previously-passing OCR task (338, 1652) regresses to F, revert to R0."
-->

### Why

Assigned focus `task_000015_89886d8d` (software_engineering, reward=0,
exit_reason=done, no_tool_calls, 41 steps). final_pytest:
`Accuracy metric 0.6614 is below the 0.98 threshold`. Task builds
`/home/user/migrate.py` from routing rules in `/app/routing_schema.png`
(800x400 RGB, a dense multi-row mapping table); the verifier runs the script
on a hidden 2000-URL edge-case set.

Root chain: `tesseract /app/routing_schema.png stdout` (msg 2) emits
`Warning: Invalid resolution 0 dpi. Using 70 instead. Estimating resolution as 111`
and garbled text (`eatalogyitem`, `Wept=<depariment»`, `fsort=<sortorder>`). The
agent tried grayscale, contrast enhance, binarize, autocontrast, sharpen, and PSM
1/3/6/11/12 (msgs 9-23) — all *contrast* remedies — but **never once upscaled**
the image (grep resize|--dpi|resample|LANCZOS over all tool_calls = empty). At
~70 effective DPI a dense table OCRs to garbage regardless of contrast tuning.
Unable to read the schema, the agent guessed a 3-route mapping from the 3 sample
URLs (msg 25+) and "verified" only against those samples — so hidden edge cases
(URL-encoding, extra/missing params) were never handled: the exact 0.66->0.98 gap.

Harness gap: the agent has only Bash and lacks the operational fact that the
`Invalid resolution 0 dpi / 70 dpi` warning is a *resolution* problem whose fix
is upscaling, not more contrast tuning. Nothing in R0 supplies this at the moment
the signal appears.

### Changes

- `processors/ocr_lowdpi_remedy.py` — new `OcrLowDpiRemedyProcessor`
  (`MultiHookProcessor`, `_order=34`, after CustomEditToolProcessor(30), before
  CustomSelfVerifyProcessor(90)). Tracks each Bash command by tool_call_id in
  `on_before_tool`; in `on_after_tool`, when the result carries the tesseract
  low-DPI signal AND the command invoked tesseract AND the command did not
  already upscale/`--dpi`, appends ONE content-agnostic remedy note (upscale
  ~3-4x with LANCZOS + re-OCR, optionally `--dpi 300`, before inferring a schema
  from samples). Fires <=`max_fires`(2). Append-only, non-terminating, no
  prompt mutation, no task literals.
- `config.yaml` — register the processor via absolute `file://` path; rest
  byte-identical to R0. `system_prompt.txt` sibling copied unchanged.

### Evidence

- task_000015_89886d8d result.json: reward=0, accuracy 0.6614 < 0.98;
  messages msg 2/4 tesseract `Invalid resolution 0 dpi. Using 70 instead`
  + garbled table; msgs 9-23 contrast/PSM remedies only, no upscale; msg 25+
  schema guessed from 3 sample URLs.
- Cluster: `grep "Invalid resolution 0 dpi"` matches all 5 tesseract tasks
  (015/505/536 fail; 338/1652 pass). Passers OCR short/simple text
  (`121\n000\n-1-2-1`; `Admin Token: TKN-8842-OMEGA`) that survives 70 dpi.
- Validators: canonicalize ok (0 templates), dry_fire 0 likely_bugs,
  contract 0 violations, literals 0 findings.

### Uncertainty

Distinct from the REVERTED R3 `h_ocr_extraction_discipline_v1` (instruction
lever, system-prompt append that fired on every task and regressed 4 unrelated
tasks). This is the corrected control-lever form: same mechanical fact, delivered
only when the low-DPI signal actually appears, provably inert on non-OCR runs —
eliminating the regression channel that killed the R3 bet. If the upscaled re-OCR
still garbles, the residual is a pure OCR-model capability limit and 015 stays F,
but the note also blocks the premature infer-from-samples shortcut that produced
the 0.66 guess. Append-only design cannot itself regress a passing task; watch
338/1652 for T->F and revert per rollback_trigger.

## Round 5 (c2) — enable built-in terminate-on-identical-loop guard

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-28T18:00:00Z
hypothesis_id: h_loop_detection_terminate_v1
levers: [configuration]
predicted_affected: [task_000028_7fe033ac, task_001706_24462a09, task_001031_a8f0eb37, task_000396_e56917e2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Close the identical-completed-tool-call loop cluster (4-plus tasks: 028 pkill x23, 1706 ps|grep x33, 1031 x35, 396 x19) by TERMINATING the loop at the 5th consecutive identical call (exit_reason=loop_detected) instead of burning the full 80-step budget; reclaims tens of steps and prevents further state corruption from continued thrashing."
regression_risk: "Very low - 0 of 30 R4-passing tasks reach 5 consecutive byte-identical tool calls (max passing = 4, task_001090); the failing cluster sits at 19-35, a wide margin above threshold=5. Strategy 2 (name-only) is warn-only and never raises, so varied-argument sequences cannot be terminated."
cost_shift: "Net decrease - terminates dead loops ~15-30 steps in rather than at the step-80 wall, reclaiming tens of steps/tokens per stuck task; the warn nudge is ~60 tokens and only appears inside an active loop."
rollback_trigger: "If next round shows any previously-passing task regress to F with exit_reason=loop_detected (false-positive termination), OR the cited cluster still shows maxrun 19-plus loops running to the wall (guard not wired), revert to R0. If loops terminate early but the four tasks stay F on their own correctness/verifier assertions, keep the guard (budget reclaimed, corruption avoided) and note the residual."
-->

### Why

Assigned focus task_000028_7fe033ac (system_administration, reward=0,
exit_reason=budget_exceeded, 80 steps). initial_pytest passes 4/4; final_pytest
fails at collection with ModuleNotFoundError: No module named 'requests' (the
grade-level cause, already owned by in-flight R3 h_http_verifier_dep_guard_v1 -
re-shipping that is a territory collision). The dominant harness gap the
trajectory exposes is different and systemic: a degenerate completed-tool-call
loop that burns the entire step budget with no forced termination. From msg 21
to msg 77 the agent issues the byte-identical command pkill -9 -f
"server|nginx" ...; /app/server ... nginx -c ... 23x consecutively - the pkill
-f "server" matches and kills the agent's own shell, so every result is (exit
137, no output captured) and the model observes nothing new. The existing
append-only nudges (EditDetection, LengthTruncation) fire and are quoted back
("The user is telling me to stop the repetitive reasoning") yet the loop
continues to the wall. Nothing in R0 TERMINATES a loop of completed tool calls
whose results never change. Sweep confirms a 4-plus-task cluster (028 maxrun 23,
1706 maxrun 33, 1031 maxrun 35, 396 maxrun 19), all reward=0 /
budget_exceeded|error.

### Changes

- config.yaml - enable the built-in
  harnessx.processors.control.loop_detection.LoopDetectionProcessor at default
  parameters (window_size=12, warn_threshold=3, threshold=5,
  name_warn_threshold=8, compaction_drop_threshold=5), inserted after
  ToolCallCorrectionLayer (consistent with its _order=20). Strategy 1 raises
  LoopDetectedError at 5 consecutive byte-identical calls -> run loop exits
  loop_detected; Strategy 2 is warn-only. Rest of the pipeline byte-identical to
  R0; system_prompt.txt sibling copied unchanged.

### Evidence

- task_000028_7fe033ac messages msgs 21-77: identical pkill -9 -f
  "server|nginx" ... -> (exit 137, no output captured) 23x consecutive; msg 21
  quotes the ignored nudge; run to step 80, exit_reason=budget_exceeded.
- task_001706_24462a09 msgs 2-68: identical ps aux | grep -E
  "(processor|sink|generator)" | grep -v grep 33x consecutive; budget_exceeded.
- task_000396_e56917e2 msgs 17-53: identical mock-error-coefficient probe 19x
  consecutive after the sim already showed a mismatch; budget_exceeded.
- task_001031_a8f0eb37: maxrun 35 consecutive identical, exit_reason=error.
- Passing false-positive sweep: 0 of 30 R4 reward=1 tasks reach maxrun 5-plus
  (highest = task_001090 at 4) - threshold=5 leaves a clean margin.

### Uncertainty

Why Configuration not Control: the exact-repeat terminate mechanism already
exists as a shipped built-in; the only gap is that it is absent from R0 -
enabling it is a registration/knob change, not new code. The in-flight R3
h_degenerate_loop_breaker_v1 (control) is append-only NUDGE, which this
trajectory proves is ignored (model quoted EditDetection/LengthTruncation back
while looping). A hard terminate is the differentiated mechanism the evidence
demands. Terminating early does not by itself flip task_000028's grade (verifier
import requests, a separate gap); the capability repaired is the missing
forced-exit on a degenerate loop, systemic across the cluster. If a passing task
ever issues one command 5x in a row it would be terminated early - the sweep
shows none do at maxrun 5-plus, so risk is very low. Watch next round for any
loop_detected regression per rollback_trigger.

## Round 5 (c3) — enable first-party LoopDetectionProcessor

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-28T17:00:00Z
hypothesis_id: h_enable_loop_detection_v1
levers: [configuration]
predicted_affected: [task_000118_3043e92d, task_001031_a8f0eb37, task_001706_24462a09, task_000396_e56917e2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Relieve the exact-tool-call-repetition budget-burn cluster (task_000118 tail 4x, task_001031 35x, task_001706 33x, task_000028 23x, task_000396 19x) by hard-terminating dead loops at repeat 5 instead of running to the step-80 wall; reclaimed budget + shorter wall-clock, generalizes to any exact tool-call loop."
regression_risk: "Low — the exact raise is gated to 5 strictly-consecutive identical tool calls, a shape no r4-passing task shows (max observed = 4 on task_001090, which only receives a harmless warn note). Name-only strategy is warn-only, never raises."
cost_shift: "Net decrease — loops end at repeat 5 rather than the wall (tens of reclaimed steps per stuck task); the warn note is ~60 tokens and fires only inside a loop."
rollback_trigger: "If a later round shows the big loopers (001031/001706/000028/000396) still budget_exceeded/error with the same 19-35x repetition unbroken, OR any previously-passing task regresses T->F on a loop-terminate, revert. If loopers are terminated but task_000118 stays F on the log-size peak, keep the processor (global budget win) and treat 118's residual as a monitor-logic capability gap."
-->

### Why

Assigned focus task_000118_3043e92d (system_administration, reward=0,
exit_reason=budget_exceeded, steps=80, initial_pytest.passed=true). The
deployment_monitor daemon must keep /home/user/logs under threshold via
SIGSTOP/truncate; the verifier ran run_deployment.sh and peak log size hit
209715200 bytes (over 45 MB). The dominant run pathology is an EXACT tool-call
repetition loop: from msg 33 the model cycles a 3-command loop
(rm+launch-monitor / sleep+ps / timeout-run), with the tail (msgs 56/60/64/68)
re-issuing the byte-identical launch command 4x, each returning
"(exit 0, no output captured)". It never runs run_deployment.sh under load, so
the peak is never observed. Crucially the model QUOTES the passive
LengthTruncationRecoveryProcessor nudge back ("The user is right - I've been
stuck in a loop") and keeps looping — passive text nudges demonstrably do not
break this loop. R0 has no loop-TERMINATION hook for repeated completed tool
calls; the length-recovery processor only covers finish_reason=length+no-tool-call.
The same exact-repetition shape recurs across a budget-burning cluster
(task_001031 35x, task_001706 33x, task_000028 23x, task_000396 19x).

### Changes

- config.yaml — register harnessx.processors.control.loop_detection.LoopDetectionProcessor
  (first-party, tested; _order=20, between TaskTimeReminderProcessor and
  CompactionProcessor) with defaults window_size=12, warn_threshold=3,
  threshold=5, name_warn_threshold=8, compaction_drop_threshold=5. Strategy 1
  (name+inputs) warns at 3 consecutive exact repeats (note appended to the tool
  result) and RAISES LoopDetectedError at 5, ending the run with container state
  preserved. Strategy 2 (name-only) is warn-only. No authored code. Rest of the
  pipeline byte-identical to R0; system_prompt.txt sibling copied unchanged.

### Evidence

- task_000118_3043e92d result.json: exit_reason=budget_exceeded, steps=80,
  reward=0; final_pytest "Peak log directory size was 209715200 bytes, which
  exceeds the threshold of 45000000 bytes."
- task_000118 messages.json toolcall fingerprints: 3-cycle from msg 33; tail
  msgs 56/60/64/68 identical launch command 4x consecutive, all "(exit 0, no
  output captured)"; passive continue-nudges at msgs 3/16/24/32/40/50/59/63/67
  quoted back by the model and ignored.
- Passing-set false-positive sweep (all reward=1 tasks, r4-traj): max
  strictly-consecutive identical tool-call run = 4 (task_001090_c61c71f2,
  legitimate ps-poll) -> threshold=5 raises on ZERO passing tasks.
- Failing loopers that threshold=5 would terminate: task_001031_a8f0eb37 (35x,
  error), task_001706_24462a09 (33x, budget_exceeded), task_000028_7fe033ac
  (23x), task_000396_e56917e2 (19x).
- Lever scoreboard: configuration=0 attempts (untried); control=5/0acc/1rev
  (four pending) — prior loop bets all authored bespoke PASSIVE nudge processors,
  never enabled this first-party terminate-on-loop config.

### Uncertainty

task_000118's tail loop peaks at 4 consecutive-exact, so the conservative
threshold=5 (kept low to protect passing task_001090's 4-poll) WARNS 118 but
does not terminate it; 118 flips only if the model acts on the injected warn (its
deeper miss — never running run_deployment.sh under load — is a reasoning gap, not
a harness gap). The strong, near-certain win is terminating the 19-35x loopers
early. If those stay unbroken next round, loop-detection isn't firing (revert); if
they terminate but 118 stays F on the peak, keep the processor and log 118 as a
capability gap.

## Round 5 (c4) — noop: task_000140 clean-shutdown gap already covered by two in-flight processors

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-28T18:00:00Z
hypothesis_id: h_noop_task140_shutdown_covered_v1
levers: []
predicted_affected: []
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "None — explicit no-op. task_000140's failure (lingering vm_service processes at grader time) is a single grader witness (1/50 this round) whose one legitimate harness lever — verify the required end-state / reconcile self-started background processes at voluntary exit — is ALREADY in flight as two pending control processors (R1-c4 h_bg_state_reconcile_v1, R4 h_lifecycle_self_verify_v1), both listing task_000140 as their primary/only predicted_affected."
regression_risk: "None — config is byte-identical to R0 (canonicalize ok, checked_templates 0)."
cost_shift: "Zero."
rollback_trigger: "N/A (noop). If neither pending control processor flips task_000140 next round on the same test_no_lingering_service_processes assertion, the residual is a model-reasoning limit (agent must reason that its own test-run service, plus the grader-run pipeline's, must be reaped) — no distinct harness lever applies; keep skipping unless a SECOND clean-shutdown grader witness appears (then a genuine cluster exists)."
-->

### Why

Assigned focus `task_000140_01c78b42` (system_administration, reward=0,
exit_reason=done, finished=no_tool_calls, 11 steps — a voluntary exit;
initial_pytest passed). The task is a CI/CD pipeline: fix a Go `vm_service`
(port 8080, `/provision` endpoint, `username` form param, exec `qemu-mock`),
fix `start_service.sh` (PATH + save PID to `/home/user/service.pid`), and write
`test_pipeline.sh` that starts the service, curls a POST, then SIGTERMs the PID.
The agent fixed all three files CORRECTLY (main.go endpoint/param/port, PATH,
PID save), ran its own `test_pipeline.sh` (msg 14) — curl returned 200,
`vm_setup.log` written with `PROVISIONED_VM_FOR: admin_alice` — and its pipeline
even ends with `kill -TERM $PID`. It then declared done after only re-`ls`-ing
files. The sole grader failure is `test_no_lingering_service_processes`:
`pgrep -f vm_service` returned `['345','588']` / `['345','588','797']` at grader
time. The agent never ran `ps`/`pgrep` after its test to confirm the required
end-state (zero lingering processes); its single-PID `kill -TERM` did not reap
every `vm_service` instance the test/grade runs leave behind.

This is a real harness gap — the moment-of-exit verification discipline is too
weak to catch a required "clean/stopped end-state" — but it is NOT a new or
distinct one. It is exactly the shape two in-flight pending candidates already
target for this exact task.

### Decision

Explicit no-op. Ship R0 (config.yaml + system_prompt.txt sidecar) byte-for-byte.
Three reasons this focus does not support a fresh, defensible harness change:

1. **Not systemic — single grader witness.** `grep -l "lingering|no_lingering"
   *.result.json` over all 50 r4 trajectories → only task_000140. The other 10
   `exit_reason=done` failures (010, 015, 264, 505, 536, 748, 933, 1653, 1781,
   1937) are content/accuracy/format misses with distinct mechanisms already
   diagnosed in prior rounds — none share the clean-shutdown grader. No 2+-task
   cluster to close, so per the systemic-vs-idiosyncratic filter this is a skip.

2. **Territory collision — lever already in flight twice.** R1-c4
   `h_bg_state_reconcile_v1` (regex background-launch detection + injected
   reconcile message) and R4 `h_lifecycle_self_verify_v1` (polarity-neutral
   self-verify checklist that explicitly reconciles persist-vs-teardown and
   reaps self-test processes) BOTH list task_000140 as their primary/only
   predicted_affected and are both `pending`. A 6th overlapping control
   processor would confound their attribution and re-propose the same
   mechanism — a novelty/territory violation.

3. **Saturated lever, poor record.** Scoreboard: control = 5 attempts / 0
   accepted / 1 reverted; instruction = 2 / 0 / 1 reverted. Piling another
   control processor on this exact single-task cluster is the textbook
   tried-repeatedly-without-flipping → look-elsewhere signal, and adds
   synthetic-replay + global-Pareto regression risk for ~1 speculative flip.

Mirrors the accepted R4-c5 (`h_noop_task264_capgap_v1`) and R5-c?
(`h_noop_task010_shadow_capgap_v1`) precedents: single-task focus whose only
valid lever is already in flight → ship R0 byte-for-byte rather than drift onto
another proposal's territory.

### Evidence

- `task_000140_01c78b42` result.json: reward=0, exit_reason=done,
  finished=no_tool_calls, steps=11, initial_pytest.passed=true; final_pytest
  fails ONLY `test_no_lingering_service_processes`
  (`AssertionError: Lingering vm_service processes found: ['345','588']` and
  `['345','588','797']` across the two grade runs).
- messages.json: msg 10 `test_pipeline.sh` ends `kill -TERM $PID`; msg 14 ran
  `bash test_pipeline.sh` (starts `./vm_service &` via start_service.sh); msg 16
  curl 200 + msg 17 log written; msg 21 `_tb2_self_verify` fired; msgs 22-24 agent
  only re-`ls`'d files and re-declared done — no post-run `ps`/`pgrep`.
- Cluster sweep: `grep -l "lingering|no_lingering|should not be running"
  *.result.json` → task_000140 only (1/50). Single grader witness confirmed.
- In-flight coverage: R1-c4 `h_bg_state_reconcile_v1` and R4
  `h_lifecycle_self_verify_v1`, both pending, both predicted_affected =
  [task_000140_01c78b42].
- canonicalize on the copied config: `{"ok": true, "checked_templates": 0}`;
  config.yaml + system_prompt.txt sha256 identical to R0.

### Uncertainty

If either pending control processor flips task_000140 next round, this confirms
the correct lever was already deployed and no further action is needed. If both
leave it F on the same `test_no_lingering_service_processes` assertion, the
residual is a model-reasoning limit (the agent must reason that BOTH its own
test-run service and the grader-run pipeline's service must be reaped, e.g. by
`pkill -f vm_service` or a robust kill+wait loop) — a knowledge gap the harness
should not paper over with task-specific process-name literals. Revisit only if a
SECOND clean-shutdown grader witness appears, making it a genuine cluster worth a
distinct, general end-state-reconciliation mechanism. Config is byte-identical to
R0, so this round cannot itself regress anything.

## Round 6 (c5) — noop: task_000264 residual = SQL slip + over-strict grader; lever in flight

<!-- journal:frontmatter
round: 6
timestamp: 2026-08-28T18:00:00Z
hypothesis_id: h_noop_task264_capgap_v2
levers: []
predicted_affected: []
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "None — explicit no-op. The only legitimate general harness lever for task_000264's fixable sub-failure (strengthened output-contract self-verify with independent re-derivation) is already in flight as R2 h_output_contract_verify_v1 (pending, task_000264 = primary predicted_affected). Re-proposing it would trip the novelty gate and confound its attribution; the second sub-failure requires banned sqlite-plan-string domain knowledge."
regression_risk: "None — config + system_prompt.txt are byte-identical to R0."
cost_shift: "Zero."
rollback_trigger: "N/A (noop). If R2 h_output_contract_verify_v1 lands and flips task_000264 on the count assertion, the self-verify lever is confirmed and no further action is needed. If it is reverted or leaves 264 F on the same '(CEO),12 != 11' assertion, the residual is a pure SQL-reasoning capability gap + an over-strict USING INDEX grader — skip."
-->

### Why

Assigned focus `task_000264_ab8c7253` (data_querying, reward=0, exit_reason=done,
finished=no_tool_calls, 17 steps — a voluntary exit). Two grader failures, both
outside the reach of a legitimate, general, non-task-specific harness lever:

1. **Off-by-one recursive CTE.** The base case seeds each employee as its own
   subordinate, so every count is +1 (`'Alice (CEO),12' != 'Alice (CEO),11'`).
   Alice's 12 equals total headcount (12) — a semantic red flag the model missed.
   This is a SQL-reasoning slip; a harness processor cannot inject the correct
   recurrence. The ONE valid general lever — strengthen the exit self-verify so
   the model independently re-derives a value instead of rubber-stamping its own
   numbers — is exactly R2 `h_output_contract_verify_v1` (byte-format audit +
   independent re-derivation), which is still `pending` and already lists
   task_000264 as its primary predicted_affected. Re-shipping it here trips the
   novelty gate and confounds its attribution.

2. **`USING INDEX` substring miss.** The grader asserts the literal
   `"USING INDEX"` (uppercased) in the query plan. NEW finding this round (local
   sqlite 3.37.2 repro): the R4 "unfixable" claim was too strong — the bare
   `USING INDEX` token IS reachable, but only by writing the recursive query to
   select a non-indexed column (e.g. `SELECT e.name` in the recursion), forcing a
   non-covering `SEARCH e USING INDEX idx (manager_id=?)`. The agent instead
   produced the optimal *covering* index (`USING COVERING INDEX`), which the
   grader rejects. Making the agent defeat the covering optimization requires
   sqlite-plan-string domain knowledge — a task-specific literal, banned. Logged
   as a NEEDS_FROM_HUMAN over-strict-grader item (relax to accept
   `COVERING INDEX`).

Lever scoreboard reinforces the skip: control = 5 attempts / 0 accepted / 1
reverted; instruction = 2 / 0 / 1 reverted. Both levers show zero flips on this
cluster, and this exact task is already covered by an in-flight control processor.
Piling a 6th overlapping change on a saturated cluster is the textbook
tried-repeatedly-without-flipping signal. This mirrors the accepted R4 c5
precedent (`h_noop_task264_capgap_v1`).

### Decision

Explicit no-op. `config.yaml` and its sibling `system_prompt.txt` are shipped
byte-for-byte identical to R0 (sha256 verified). No candidates.md (noop round).

### Evidence

- `task_000264_ab8c7253` result.json: `reward=0`, `exit_reason=done`, steps=17;
  final_pytest `At index 0 diff: 'Alice (CEO),12' != 'Alice (CEO),11'` and
  `'USING INDEX' not in 'QUERY PLAN … SCAN E USING COVERING INDEX IDX_EMPLOYEES_MANAGER_… USING AUTOMATIC COVERING INDEX …'`.
- messages.json msg 9-10: CTE self-inclusion → Alice=12 (=headcount); msg 18/26
  agent's optimized plan shows `SCAN e USING COVERING INDEX idx_employees_manager_id`;
  msg 30 `_tb2_self_verify` fired; msgs 31-33 re-`ls`/re-eyeball, no recomputation.
- NEW local repro (sqlite 3.37.2): plain `manager_id` index on a covering
  recursive query → `USING COVERING INDEX` (no bare token); adding a non-indexed
  selected column → `SEARCH e USING INDEX idx (manager_id=?)` (bare token present).
  Reaching the token requires sqlite-specific knowledge → banned literal.
- In-flight overlap: R2 `h_output_contract_verify_v1` (pending) lists
  task_000264 as primary predicted_affected; re-proposal blocked by novelty gate.

### Uncertainty

If R2's `h_output_contract_verify_v1` lands and flips task_000264 on the count
assertion, the self-verify lever is confirmed correct. If it is reverted or
leaves 264 F on the same assertions, the residual is a pure SQL-reasoning
capability gap plus an over-strict `USING INDEX` grader (NEEDS_FROM_HUMAN) — no
harness lever applies; keep skipping. Config byte-identical to R0, so this round
cannot itself regress anything.

## Round 5 (c6) — break degenerate completed-tool-call loops (budget_exceeded witness)

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-28T17:00:00Z
hypothesis_id: h_degenerate_loop_breaker_v2
levers: [control]
predicted_affected: [task_000396_e56917e2, task_000958_4bb2b05d, task_001706_24462a09, task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flip/relieve the degenerate completed-tool-call loop cluster (>=4 tasks) where the model cycles on the same tiny set of commands producing unchanging results and burns the whole step budget. task_000396 is a FRAGILE task (passed R0, F in R1-R4) whose model reaches the correct diagnosis unaided in-transcript — reclaiming ~70 looped steps lets the already-present fix land. Content-agnostic, so it generalizes across the sqlite3/jq/import rerun and A/B/A/B cycle shapes documented across rounds."
regression_risk: "Very low — append-only on the tool result (mirrors CustomEditToolProcessor contract): never inserts/removes messages, never mutates the system prompt, never terminates. R3 false-positive sweep found 0 of 29 R2-passing tasks satisfy the window=6/distinct<=2 condition, so no passing task should ever see the note. Contract check: 0 violations."
cost_shift: "Net decrease — breaks loops at ~the 6th repeated result instead of the step-80 wall (task_000396 spent ~74 of 80 steps looping), reclaiming tens of steps per stuck task; the note is ~90-130 tokens and fires only inside an active loop."
rollback_trigger: "If R6 shows task_000396 still reward=0 with the loop unbroken (nudge ignored) — escalate cadence / add a terminate-on-loop guard rather than reship — OR if any previously-passing task regresses to F, revert."
-->

### Why

Assigned focus `task_000396_e56917e2` (scientific_computing, reward=0,
exit_reason=**budget_exceeded**, steps=80). The deliverable
`/home/user/validation.log` exists but holds the wrong value (0.60724; grader
requires `0.0 < max_dev < 0.1`). The harness gap is the *run pathology*, not the
value: from msg 4 the agent fell into a byte-identical A/B/A/B 2-cycle
(`cat /home/user/validation.log` -> `0.60724` alternating with a long analysis
command whose stdout never changes), repeated ~30 times, consuming the entire
80-step budget. It even *narrated* the loop ("I've been repeating the same
analysis many times", msgs 19-53) yet could not escape; it briefly broke out at
msgs 62-66 (read `/app/reference.dat` and `/app/lib-rk45/rk45.c`, correctly
identified the injected "PERTURBATION: Negative exponent causes divergence!"
bug) then RELAPSED into the same 2-cycle (msgs 66-69) and hit the wall with the
fix never applied.

This is a *different run shape* from the R4 `h_computed_result_plausibility_v1`
(instruction, pending) witness of the same task, which observed
`exit_reason=done, no_tool_calls, 30 steps` (agent computed 0.607 and voluntarily
declared done). Same fragile task, run-to-run-varying failure. The R4 instruction
lever targets the voluntary-exit shape; this control lever targets the
budget-burning loop shape — complementary, not a territory collision.

Root harness gap: nothing in R0 intercepts a loop of *completed* tool calls
whose results never change. `LengthTruncationRecoveryProcessor` handles only the
`finish_reason=length` no-tool-call loop; the completed-tool-call 2-cycle is
uncovered. A naive "N consecutive identical results" detector misses a 2-cycle
(no two adjacent calls identical); a rolling window of the last 6 (command,result)
fingerprints collapsing to <=2 distinct values catches both shapes.

### Changes

- `processors/degenerate_loop_breaker.py` — `DegenerateLoopBreakerProcessor`
  (`MultiHookProcessor`, `_order=33`, after CustomEditToolProcessor(30), before
  CustomSelfVerifyProcessor(90)). Rolling window of blake2b (command,result)
  fingerprints; when distinct-in-window <= `distinct_max` appends an escalating
  soft->hard "you are looping, change strategy; inspect un-examined inputs, find
  the root cause, apply a concrete fix, verify the exact output path/value" note
  to the tool result. Append-only, non-terminating, content-agnostic. Re-fires
  every `refire_gap` results so a relapse (msgs 66-69) gets re-nudged. Hard-nudge
  wording strengthened vs the R3 draft to point at un-examined inputs / root-cause
  fix, motivated by 000396's relapse-after-brief-escape.
- `config.yaml` — register via absolute `file://` path (window=6, distinct_max=2,
  min_window_fill=6, refire_gap=4). Rest byte-identical to R0; system_prompt.txt
  sibling copied unchanged.

### Evidence

- `task_000396_e56917e2` result.json: exit_reason=budget_exceeded, steps=80,
  initial_pytest.passed=true; final_pytest `AssertionError: The maximum deviation
  0.60724 is not within the expected range (0.0, 0.1)`.
- messages.json msgs 4-54: A/B/A/B 2-cycle of two commands with unchanging
  results, ~30x; msgs 19-53 self-narration of the loop; msgs 62-66 brief escape +
  correct bug diagnosis; msgs 66-69 relapse.
- Offline detector replay on the recorded transcript (window=6, distinct_max=2):
  loop condition first satisfies at tool-result index 5 (~step 6), holds for 23 of
  32 windows -> nudge fires ~70 steps before the wall.
- Per-task history: task_000396 R0=True (PASSED under incumbent), R1-R4=False ->
  FRAGILE, model-capable, blocked only by the budget the loop consumes.
- False-positive sweep (R3): 0 of 29 R2-passing tasks satisfy the window=6/
  distinct<=2 condition.

### Uncertainty

If the model reads the nudge but still cannot converge on the stable-integrator
fix within the reclaimed budget, 000396 may stay F (residual = numerical
capability limit); but in this transcript it already reached the correct
diagnosis unaided, so the reclaimed budget is the binding constraint. Append-only
design cannot itself regress a passing task. Relationship to R3
`h_degenerate_loop_breaker_v1` (pending, never gated, incumbent still R0): not
reverted so novelty-permitted; distinct witness (000396, fragile) + fresh
hypothesis id + strengthened hard-nudge. If R6 shows the nudge too weak (loops
persist), escalate cadence or add a terminate-on-loop guard per rollback_trigger.


## Round 6 (c7) — verify against real artifacts, not self-authored fixtures

<!-- journal:frontmatter
round: 6
timestamp: 2026-08-28T18:00:00Z
hypothesis_id: h_real_artifact_verify_v1
levers: [control]
predicted_affected: [task_000505_50b5162d, task_000536_9c16e8ef, task_000015_89886d8d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
retry_rationale: "Reverted R3 h_ocr_extraction_discipline_v1 was an always-on Instruction prompt append (lost 4 tasks via step inflation on easy tasks). This is a different lever (Control) and a different shape: a drop-in self-verify replacement that GATES the extra guidance on a mechanical runtime fingerprint (echo/here-doc fixture write plus run-executable-against-file), emitting byte-identical stock checklist on non-matching runs -- so it carries none of the always-on regression surface that sank the prior attempt."
expected_global_gain: "Flip/relieve the self-referential-validation cluster (>=3 tasks: 505 trojan detector, 536 audit script, 015 migrate parser) where the agent verifies a derived-value program against a fixture it built from its own derived value; discipline generalizes to any detector/classifier/transformer/parser task graded on real on-disk artifacts."
regression_risk: "Very low -- drop-in for CustomSelfVerifyProcessor with identical firing mechanism/contract (+1 user msg, one-shot, keepalive tool call, no system-prompt mutation). On any run not matching the fixture+run-against-file fingerprint the injected message is byte-for-byte the stock checklist, so non-matching tasks are unchanged."
cost_shift: "Negligible-to-small-positive: one conditional ~180-token checklist item fires only on antipattern runs plus a few find/ls/re-run Bash calls; nothing added on non-matching tasks."
rollback_trigger: "If next round is flat/down AND task_000505/536/015 stay F on the same real-input assertions (pure model extraction/OCR limit), OR any previously-passing voluntary-exit task regresses to F, revert to stock CustomSelfVerifyProcessor."
-->

### Why

Assigned focus task_000505_50b5162d (security, reward=0, exit_reason=done,
no_tool_calls, 10 steps). The agent OCR'd an SSH key from /app/evidence.png and
got a garbled string (real ed25519 prefix AAAAC3NzaC1lZDI1NTE5 misread as
AAAAC3NzaC1IZDIINTES, stray spaces inserted), hardcoded it into
/home/user/detect_trojan.sh, then "verified" by echoing the same garbled string
into a /tmp fixture and running the detector against THAT file -- a guaranteed
false pass. The real trojaned binaries cat_evil/ls_evil (the grader's EVIL_DIR,
present on disk during the agent phase) were never grepped, so the verifier
reported "2 of 2 evil bypassed". The OCR misread is a model limit, but the
self-referential validation loop -- testing a derived-value program against a
fixture built from the same derived value -- is a harness discipline gap. Same
shape recurs: task_000536 (OCR'd id -> audit script, CSV quoting never diffed
against the real DB output) and task_000015 (schema inferred from 4 sample URLs,
"verified" only against those 4 samples -> 0.66 accuracy on the hidden real
2000-URL set). All three are voluntary exits where the self-verify processor
already fires but its generic checklist does not catch the circular test.

### Changes

- processors/real_artifact_verify.py -- new RealArtifactVerifyProcessor
  (MultiHookProcessor, singleton group tb2_self_verify, order 90). Drop-in
  replacement for CustomSelfVerifyProcessor: identical one-shot keepalive +
  single appended user message. on_before_tool tracks a content-agnostic
  fingerprint -- a here-doc or echo/printf redirect into a file (self-authored
  fixture) AND a later run of an executable/script against a file path. On
  voluntary exit, if both fired, injects a checklist with ONE extra task-agnostic
  item: exercise the deliverable against the REAL artifacts present in the
  environment (survey with find/ls), not a fixture built from a derived value;
  cross-check lossy-source values against an authoritative on-disk source.
  Otherwise the injected message is byte-for-byte the stock checklist.
- config.yaml -- swap the final pipeline entry
  benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor for a
  file://...::RealArtifactVerifyProcessor reference. Rest byte-identical to R0;
  system_prompt.txt sibling copied unchanged.

### Evidence

- task_000505_50b5162d messages.json msg 7 (tesseract): garbled ed25519 key;
  msg 9 hardcodes it; msg 11 echo "<key>" into a /tmp fixture then runs
  /home/user/detect_trojan.sh against that fixture -> exit 1 -> "works" (the exact
  echo-into-file + run-script-against-file fingerprint). result.json final_pytest:
  "2 of 2 evil bypassed: cat_evil, ls_evil".
- task_000536_9c16e8ef final_pytest: EMP-4001 quoted-vs-bare CSV field mismatch,
  checkable against the real DB; exit_reason=done, no_tool_calls.
- task_000015_89886d8d final_pytest: accuracy 0.6614 < 0.98 on the hidden real
  dataset; agent verified only against the 4 samples it inferred rules from;
  exit_reason=done, no_tool_calls.

### Uncertainty

If the model reads the nudge but still trusts a garbled OCR value without
re-running against the real binaries, 505 stays F (OCR itself is a model limit;
the rule only helps if the agent acts on the "exercise against real inputs" cue).
For 015/536 the cue is more mechanical (real hidden inputs / real DB) and likelier
to land. Append-only, one-shot, stock-preserving-on-non-match design cannot itself
regress a passing task. If next round shows the cluster unchanged on the same
real-input assertions, the residual is a pure extraction/OCR capability gap;
revert per rollback_trigger.
