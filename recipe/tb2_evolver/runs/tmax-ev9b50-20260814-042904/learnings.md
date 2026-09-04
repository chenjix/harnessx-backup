# Evolution Journal — tmax-ev9b50 (Terminal-Bench 2)

## Round 1 — add loop-detection guard

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-14T05:10:00Z
hypothesis_id: h_loop_detection_v1
levers: [configuration, control]
predicted_affected: [task_000010, task_001031, task_000958]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=29/50; +3/-4 gained=task_000965_069bb95f,task_001264_9f4ca84a,task_001818_b251e5ea lost=task_000536_9c16e8ef,task_000578_cebe85a5,task_000684_1a33ef37,task_000748_c9807703; score 0.5800 >= incumbent(mean) 0.6000 - tol 0.0400
expected_global_gain: "Flips the exact-consecutive-repeat runaway cluster (3 reward=0 tasks) by aborting stuck loops early enough to leave step/time budget for recovery; cheaper failed runs on any future runaway."
regression_risk: "A legitimate task that issues 6 identical tool calls in a row would abort early. Max exact-consec among the 30 passing baseline tasks is 2, so headroom is 3x; risk is low. Strategy-2 (name-only) warnings disabled (name_warn_threshold=999) to avoid false-positive nagging on legitimate exploration."
cost_shift: "Net negative expected: runaway tasks currently burn 30-66 steps / hundreds of seconds looping; early abort at 6 consec cuts those tails. No added cost on passing tasks (guard is dormant below threshold)."
rollback_trigger: "If any baseline-passing task regresses to exit_reason=loop_detected, or pass_rate drops, revert the processor."
-->

### Why

Baseline: 30/50 pass (0.6). Among the 20 failures, one cluster is
cleanly harness-addressable: three reward=0 tasks fall into runaway
exact-repeat loops, emitting the *identical* tool call dozens of times
until they exhaust step/time budget with no progress. This is a harness
deficiency (no interrupt on a non-recoverable repeat state), not a model
capability gap. The other failure buckets — 8 `chat ... timed out`
agent_errors (model-server slowness, outside the HarnessConfig surface),
and diverse sysadmin/reward=0 capability gaps — are not clusterable into
one harness fix and are logged below as skips.

### Changes

- `config.yaml` — register `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
  on `_hook_: '*'` with `window_size=12, warn_threshold=3, threshold=6,
  name_warn_threshold=999, compaction_drop_threshold=5`. Strategy 1
  (exact name+input) warns at 3 consecutive repeats and raises
  `LoopDetectedError` at 6; Strategy 2 (name-only) warnings disabled.
  `LoopDetectedError` maps to `exit_reason=loop_detected` (not `error`),
  so it does not trip the post-flight replay gate.

### Evidence

- `task_000010`: 48 exact-consecutive identical tool calls, reward=0.
- `task_001031`: 41 rewrites of analyze.py (35 exact-consecutive),
  66 steps / 548s, reward=0.
- `task_000958`: 31 exact-consecutive identical calls, reward=0.
- Contrast: max exact-consecutive-repeat among all 30 *passing* tasks
  is 2 → threshold=6 leaves a 3x safety margin.
- `task_001818` (separate thrash, timed out): rewrote main.rs 12x,
  rebuilt 11x over 54 calls — different-args thrash, not exact-repeat;
  Strategy 1 will not catch it (correctly, since it is not the target
  cluster) and Strategy-2 is disabled to avoid false positives.

### Skips (model-capability / out-of-surface — no harness fix)

- 8 tasks `chat failed at step N: timed out`: single model API
  generation call times out (fires even at step 7 / ~8.7K chars, far
  below compaction threshold). Root cause is model-server slowness;
  retry/timeout lives in the provider/model-client layer, not the
  evolvable HarnessConfig surface. No harness fix — skip.
- Sysadmin cluster (0/5) and remaining reward=0 `ok` exits: diverse
  genuine capability gaps (missing flock, unresolved deadlock, wrong
  process persistence/naming, incomplete at max_steps). Not one class
  — no single harness fix — skip.

### Uncertainty

The bet is that aborting the loop early frees enough budget for the
model to recover on those 3 tasks; it is possible the underlying
capability gap means they still fail (just cheaper/faster). Even then,
the change is net-positive on cost and carries near-zero regression
risk (3x threshold margin, Strategy-2 disabled). If any passing task
regresses to `loop_detected`, revert.

## Round 2 — verify-before-exit system prompt

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-14T05:25:00Z
hypothesis_id: h_verify_before_exit_v1
levers: [instruction]
predicted_affected: [task_000748_c9807703, task_001090_c61c71f2, task_000933_1f27096a, task_000015_89886d8d]
cited_candidates: [C-002]
gating_outcome: accepted
gating_attribution: score=28/50; +3/-4 gained=task_000578_cebe85a5,task_000684_1a33ef37,task_000748_c9807703 lost=task_000760_76ba653c,task_001498_df8254c9,task_001536_acfe6c35,task_001701_95e3bbcb; score 0.5600 >= incumbent(mean) 0.6000 - tol 0.0400
expected_global_gain: "Flips part of the 9-task no_tool_calls premature-stop cluster by forcing literal re-verification of every explicit task requirement (exact paths, filenames, strings/codes, process names) against real filesystem/process state before the agent stops."
regression_risk: "Adds a few verify steps. All 29 passing tasks already stop at no_tool_calls with median 13 / max 64 steps vs the 80-step cap, so headroom is large; verification cannot turn a correct solution incorrect. Residual risk: prompt over-encourages exploration — mitigated by a concise strategy-level prompt with zero task literals."
cost_shift: "+1 to ~6 extra Bash steps per task for the verify pass; modest token increase bounded by max_steps=80."
rollback_trigger: "If R3 pass_rate is flat/down vs 30/50 incumbent while median step count climbs toward the cap, revert the prompt to the default."
-->

### Why

Mechanism correction first: the tmax eval loop (`recipe/tmax_eval/run_eval.py`
+ `agent_loop.py`) does **not** run the `config.yaml` processor pipeline. It
resolves only the system prompt via `_resolve_system_prompt`
(`system_prompt.txt` sibling of the config, else first `templates/*.j2`, else
the built-in default). R1's loop-detection *processor* was therefore inert,
and R1's `system_prompt.txt` was byte-identical to the built-in default — so
R0≈R1 and the reported +3/-4 (30→29) flip is run-to-run noise from the
stochastic 9B server, not a real regression. The only live lever on this
benchmark is the system prompt.

The dominant harness-addressable failure cluster is premature stopping: 9/21
R1 failures finish `no_tool_calls`, and their verifier failures are on
*explicit, checkable* requirements the agent never re-verified — exact string
`E0502` (agent wrote `0502`), process name `monitor` (ended up `bash`), a
build artifact at the wrong path, a required file never created. The built-in
prompt actively invites early exit ("When the task is complete, stop calling
tools"). This is an instruction deficiency, not a capability gap — Bash can
check all of these.

### Changes

- `system_prompt.txt` — new strategy-level prompt: SURVEY & PLAN (enumerate
  every explicit requirement — exact paths/filenames/strings/process names)
  → IMPLEMENT (write to exact paths, preserve literal strings) → VERIFY
  BEFORE STOPPING (re-check each requirement against real filesystem/process
  state via Bash; fix and re-verify before stopping). No task literals.
- `config.yaml` — byte-identical copy of R1 (processor pipeline is inert on
  this loop; left unchanged to avoid churn).

### Evidence

- `task_000748_c9807703` (no_tool_calls, 18 steps): final assistant message
  narrates "extract just the numeric code (e.g., '0502' from 'E0502')";
  verifier `assert 'E0502' in '0502\n'` fails. Agent dropped a required char
  and never re-read the requirement.
- `task_001090_c61c71f2` (no_tool_calls, 32 steps): final message "The task
  is complete. Let me summarize..."; verifier finds process `comm=='bash'`,
  expected `'monitor'`.
- `task_000933_1f27096a` (no_tool_calls, 8 steps): final message "All
  requirements have been completed successfully"; verifier: binary path
  `bin/graph_math_double` missing.
- `task_000015_89886d8d` (no_tool_calls, 78 steps): required file
  `/home/user/test_parser.py` never created.
- Headroom check: 29/29 passing tasks stop at `no_tool_calls`, median 13 /
  max 64 steps vs cap 80.

### Skips (model-capability / out-of-surface — no harness fix)

- 8 tasks `chat failed at step N: timed out` + 1 `HTTP 400` finish `error`:
  `agent_loop._chat` has no retry, so a single transient model-server timeout
  aborts the session and discards progress. This is a genuine harness
  deficiency, BUT `agent_loop.py` is under `recipe/` (read-only) and is not
  reachable from the evolvable `config.yaml`/system-prompt surface. Logged for
  a human: a retry-with-backoff wrapper around `_chat` would likely recover
  several of these. No harness fix available from this surface — skip.
  (See `_meta_scratch/candidates.md` mechanism note.)

### Uncertainty

The bet is that explicit verify-before-exit discipline flips premature-stop
failures without pushing passing tasks toward the step cap. If some failures
are true capability gaps (the agent can't produce the right value even when it
knows to check), they stay failed — but the change stays net-positive on
correctness and near-zero on regression risk given the large step headroom.
If R3 pass_rate is flat/down while step counts climb, revert to the default
prompt.

## Round 3 — adversarial verify-before-exit

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-14T05:45:00Z
hypothesis_id: h_verify_before_exit_v2
levers: [instruction]
predicted_affected: [task_000760_76ba653c, task_001090_c61c71f2, task_000140_01c78b42, task_001032_1adaccb9]
cited_candidates: [C-003]
gating_outcome: accepted
gating_attribution: score=30/50; +5/-3 gained=task_000536_9c16e8ef,task_000760_76ba653c,task_001032_1adaccb9,task_001090_c61c71f2 lost=task_000578_cebe85a5,task_001652_86e1d185,task_001818_b251e5ea; score 0.6000 >= incumbent(mean) 0.6000 - tol 0.0400
expected_global_gain: "Flips part of the no_tool_calls+reward=0 cluster (10 tasks in R2) whose verifier keys on exact grader semantics the agent self-certified past — process comm-name, forbidden substrings (incl. comments), exact counts/formats. Sharpens the accepted R2 verify phase from optimistic to adversarial; generalizes to any task graded on a name/forbidden-string/exact-count."
regression_risk: "Adds a few Bash verify steps. All 28 passing tasks stop well under the 80-step cap (median ~13); adversarial verification cannot make a correct artifact incorrect, and the prompt says stop once every strict check passes. Residual: a few extra steps on tasks already right. 4 max_steps failures are unrelated capability gaps and already at cap."
cost_shift: "+1 to ~5 extra Bash steps per task for the strict verify pass; modest token increase bounded by max_steps=80."
rollback_trigger: "If R4 pass_rate is flat/down vs the 28-30 band while median step count climbs materially toward the cap, revert to the R2 prompt."
-->

### Why

Re-verified mechanism: the tmax eval loop (recipe/tmax_eval/run_eval.py::_resolve_system_prompt + agent_loop.py) does NOT run the config.yaml processor pipeline — it resolves only the system prompt (sibling system_prompt.txt) and runs a fixed single-Bash-tool loop (temperature=0, max_steps=80). System prompt is the sole live lever.

R2 (28/50, ~flat vs 30/50 baseline within 9B run-to-run noise) added a generic verify-before-exit phase. Inspecting R2's residual failures shows the phase is followed superficially: the agent produces confident ✅ "all requirements verified" checklists after a happy-path run, but the verifier fails on the EXACT grading semantics the agent never adversarially tested — a process's comm-name (checked `/proc/PID/comm`), a forbidden substring left in a comment, an exact line count / format. This is an instruction-shape deficiency (how to verify), not a capability gap: every one of these checks is a one-line Bash command the agent could have run.

### Changes

- `system_prompt.txt` — evolve phase 3 from generic "re-check each requirement" into an ADVERSARIAL, grader-mirroring verify: for each stated constraint run the exact strict test a grader would (existence/exact-path; grep artifact for required literals; grep artifact — comments included — for forbidden "must not" tokens; check process identity via /proc/<pid>/comm and pgrep -a/-f, not just PID-alive; assert exact counts/formats/ordering via wc -l etc.; actually run produced scripts). Treat a happy-path run or a self-test that itself errored as NOT proof. Stop once every strict check passes. No task literals — only generic Linux idioms.
- `config.yaml` — byte-identical copy of R2 (processor pipeline inert on this loop).

### Evidence

- task_000760_76ba653c (no_tool_calls): grader `assert "/app/model_predict" not in content`; agent's script works and does not call the binary at runtime, but left `# (reverse-engineered from /app/model_predict)` in a comment — substring check fails. Final message: "predictions are correct ... task is complete"; never grepped its own artifact.
- task_001090_c61c71f2 (no_tool_calls): grader reads `/proc/{pid}/comm`, `assert comm == "monitor"`. Agent verified "Server running (PID 553)" and the /status endpoint in a 13-item ✅ list but never checked the process NAME; comm=='bash'.
- task_000140_01c78b42 (no_tool_calls): grader `pgrep -f vm_service` must be empty; agent stopped with lingering PIDs ['333','585','777'].
- task_001032_1adaccb9 (no_tool_calls): grader wants exactly 2 log lines / zip-slip prevented; agent produced 5 lines, slip unhandled.
- Headroom: 28/28 passing tasks stop under the 80-step cap (median ~13).

### Skips (model-capability / out-of-surface — no harness fix)

- 8 `error` (chat timed out / HTTP 400) tasks: single model-server call fails; retry lives in agent_loop._chat under recipe/ (read-only, not reachable from the evolvable system-prompt surface). Logged for a human previously. Skip.
- Genuine value-computation gaps: task_001937 (grid=60 vs 50), task_000536 (extra rows), task_001321 (F1), task_000264 (SQL query plan lacks USING INDEX), task_001781 (deadlock not truly fixed) — adversarial verify may surface a mismatch but the agent still can't compute the right value. Not addressed by this round.

### Uncertainty

The bet is that adversarial, grader-mirroring verification flips the exact-semantic near-misses (name/forbidden-substring/count) while step headroom absorbs the extra checks. If a failure is a true value-computation gap even after the agent detects the mismatch, it stays failed. If R4 is flat/down while step counts climb toward the cap, revert to the R2 prompt.

## Round 4 — anti-loop / break-the-repeat discipline

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-14T06:05:00Z
hypothesis_id: h_anti_loop_discipline_v1
levers: [instruction]
predicted_affected: [task_000010_644ab1c2, task_000028_7fe033ac, task_000118_3043e92d, task_001701_95e3bbcb, task_000015_89886d8d]
cited_candidates: [C-004]
gating_outcome: accepted
gating_attribution: score=32/50; +5/-3 gained=task_000578_cebe85a5,task_001089_220cc46b,task_001652_86e1d185,task_001701_95e3bbcb lost=task_001090_c61c71f2,task_001653_c4cafa73,task_001673_86224c91; score 0.6400 >= incumbent(mean) 0.6000 - tol 0.0400
expected_global_gain: "Targets the 7-task runaway identical-command repeat cluster (4 max_steps + 3 error) — the largest harness-addressable cluster in R3. Breaking the loop reclaims 30-50 steps/task for real diagnosis and, for task_000015, avoids a context-growth-induced upstream HTTP 400 that discards the session. Generalizes to any temperature-0 deterministic repeat loop."
regression_risk: "Low. Among 30 passing tasks, max-consecutive-identical is <=3 for all except task_000684 (PASS) whose 42x benign `echo` loop is harmless to abort early. Directive is advisory (change approach OR stop cleanly), explicitly permits short polling, and is appended to the accepted R3 verify-before-exit prompt so the 28-30 band behavior is preserved."
cost_shift: "Strongly negative (cheaper). 4 max_steps loop tasks each burn the full 80-step budget on identical commands; early break reclaims 30-50 steps each. No added cost on passing tasks (they do not loop)."
rollback_trigger: "If R5 pass_rate drops below the 28-30 band, or any currently-passing task regresses to a premature stop attributable to the anti-loop directive (agent stops after a legitimate 2-3x poll before finishing work), revert to the R3 prompt."
-->

### Why

Re-verified mechanism (again, by reading `recipe/tmax_eval/agent_loop.py::run_agent` + `run_eval.py::_resolve_system_prompt`): the tmax eval loop ignores the config.yaml processor pipeline entirely and consumes only the resolved system prompt string (sibling `system_prompt.txt`). Fixed single Bash tool, temperature=0, max_steps=80. The system prompt is the ONLY live lever. So the R1 `LoopDetectionProcessor` is inert.

R2/R3 pushed the verify-before-exit instruction line twice; scores stayed in the 28-30/50 run-to-run-noise band. That lever is exhausted, and most residual `no_tool_calls` failures are genuine value-computation gaps (F1, grid=50, deadlock). But a DISTINCT, larger, clearly harness-addressable cluster was hiding in the exit_reason data: runaway identical-command repeat loops. Measured max-consecutive-identical Bash commands per task: the 7 loop tasks (task_000010=39, task_000028=43, task_000118=54, task_001701=45, task_000015=59, task_001652=6, task_000505=8) are ALL fails; every passing task is <=3 except one benign `echo` loop. This is a control-flow deficiency: at temperature 0, re-issuing the same command yields the same result, so the model deterministically wastes its whole budget re-running a stuck command instead of diagnosing the root cause.

### Changes

- `system_prompt.txt` — kept the accepted R3 adversarial verify-before-exit prompt verbatim and ADDED a general "NO-PROGRESS DISCIPLINE" section between IMPLEMENT and VERIFY: if the same/trivially-varied command produces the same result 2-3 times, treat it as a hard no-progress signal, stop repeating, diagnose the root cause (read errors/logs; question assumptions — a created file may shadow a system module, a background service may be dying, a poll may never converge), then change approach or stop cleanly. Short polling is explicitly allowed. Also added two small VERIFY hardening bullets (general): (a) data-type/format read-back (a JSON number must be a real number not a quoted string — targets task_000578's `"24.0000"` string bug and the process-respawn check for task_000140), and (b) "an unrun check is a failed check" to curb hallucinated ✅ checklists. No task literals (grep-verified clean).
- `config.yaml` — byte-identical copy of R3 (pipeline inert on this loop).

### Evidence

- task_000010 (max_steps): created `/home/user/operator.py` (required by spec) which shadows stdlib `operator`; every `python3` from /home/user then fails, and the agent repeats the identical debug one-liner ~39x without recognizing the shadow. Verifier's own runpy also breaks (`operator.py` in cwd).
- task_000028 / task_000118: agent re-runs the same `ps`/`sleep` poll 43x/54x waiting for a background service that never comes up; never reads why.
- task_001701: identical `jshon ... -e 0 -k cert_b64` 45x.
- task_000015 (error, HTTP 400 @ step 75): 59x identical repeat inflates history until the upstream model server 400s and the session is discarded — the loop directly causes an otherwise out-of-surface crash.
- Contrast: 30 passing tasks all <=3 max-consecutive-identical except task_000684 (PASS, 42x harmless `echo "Task completed successfully!"` after the artifact was already correct — safe to abort early).

### Skips (model-capability / out-of-surface — no harness fix)

- Genuine value-computation gaps unchanged by this round: task_001321 (F1 0.075, extraction logic wrong), task_001937 (Optimal Grid 60 vs 50), task_001781 (deadlock not truly fixed), task_000140 (vm_service respawns after kill — needs disabling the restart mechanism, not just `kill`), task_001498 (validate-endpoint error value). Adversarial verify may surface the mismatch but the agent still cannot compute the right value.
- Pure model-server timeouts with no loop (task_001031 @ step 6, task_001089, task_000264, task_000396): single `_chat` call times out with no retry; retry lives in agent_loop._chat under recipe/ (read-only). Logged for a human previously. Skip.

### Uncertainty

The bet: breaking deterministic repeat loops reclaims budget for real diagnosis (and avoids the task_000015 context-blowup 400). If the underlying task is a true capability gap the agent still fails — but cheaper, and without crashing the session. Near-zero regression risk given the <=3 passing-task repeat ceiling and the benign echo exception. If R5 drops below the band or a passing task regresses to an early stop, revert to the R3 prompt.

## Round 5 — explicit no-op (instruction lever saturated)

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-14T06:30:00Z
hypothesis_id: h_noop_lever_saturated_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=29/50; score 0.5800 >= incumbent(mean) 0.5920 - tol 0.0400 (final-round scoring)
expected_global_gain: "0 flips by design. Preserves the R4 32/50 peak (the top of the same-config 28-32 noise band) rather than gambling a 5th marginal instruction tweak that the evidence says targets capability gaps, not knowledge gaps."
regression_risk: "None — byte-identical copy of R4 config.yaml and system_prompt.txt. The only live lever (sibling system_prompt.txt) is unchanged."
cost_shift: "Zero. No prompt change, so no per-task step/token delta."
rollback_trigger: "N/A (no change). If a future round finds a genuinely new, generalizable, non-domain-knowledge harness mechanism, ship it then."
-->

### Why

R4 reached 32/50 (0.64) — the current peak and top of the same-config
noise band (R0-R3 drew 30/29/28/30 from identical config; observed spread
= 4 tasks). The ONLY live lever on this benchmark is the sibling
`system_prompt.txt` (re-confirmed by reading
`recipe/tmax_eval/run_eval.py::_resolve_system_prompt` + `agent_loop.py`:
the config processor pipeline and `tool_registry` are entirely inert — the
loop consumes only the resolved prompt string, fixed single Bash tool,
temperature 0, max_steps 80).

That instruction lever has now been pushed four consecutive rounds
(verify-before-exit → adversarial-verify → anti-loop discipline) and the
score has stayed inside the 28-32 noise band. Per the `analyze` skill, a
lever repeatedly pushed on the same clusters without flipping them is a
signal to look elsewhere. I read all 18 R4 failures and found no clean,
generalizable, un-addressed harness cluster remaining:

- ~9 are genuine value-computation capability gaps (task_000264 query
  plan, task_000396 sim error 0.607 vs <0.1, task_001673 path cost 533 vs
  <=450, task_001937 grid 60 vs 50, task_001031 rel_tol, task_001653
  centroid distance, task_001781 deadlock/leak, task_001321 180s timeout,
  task_000933 build/tar contents).
- 2 are out-of-surface model-server timeouts (task_000505, task_000015 —
  `chat failed ... timed out`; retry lives in agent_loop._chat under
  read-only recipe/).
- 2 are out-of-surface verifier-environment gaps: task_000028 and
  task_000958 are C++/HTTP-microservice tasks whose verifier fails at
  COLLECTION with `ModuleNotFoundError: No module named 'requests'` — the
  test file's own dependency is absent from the container and internet is
  blocked, so neither the agent (no signal it's needed) nor the prompt can
  fix it.
- 3 are process-identity / lingering-process near-misses (task_001090
  comm=='bash' vs 'monitor'; task_000140 lingering worker PIDs;
  task_000118 deployment monitor). These are the most "harness-shaped"
  remaining, BUT R3's adversarial-verify prompt ALREADY instructs checking
  `/proc/<pid>/comm` and `pgrep -f` for lingering processes, and they
  still failed. The residual gap is the launch TECHNIQUE (e.g. running the
  compiled binary directly vs through a shell wrapper so comm matches) —
  supplying that is task-specific domain knowledge, forbidden by the
  evolution philosophy. So the honest retroactive check (Variant A) on any
  new process-discipline instruction returns **no**: the agent already
  knows to check, and still can't produce the right value.

### Changes

- `config.yaml` — byte-identical copy of R4 (verified with `diff`).
- `system_prompt.txt` — byte-identical copy of R4 (verified with `diff`).

Explicit no-op: the disciplined Pareto move is to preserve the 32/50 peak
rather than ship a 5th marginal instruction bet whose retroactive check
fails and which risks landing in-band (auto-reverted, wasted round) or
regressing already-passing tasks.

### Evidence

- OVERVIEW + summary.json: R4 pass_rate=0.64 (32/50); same-config draws
  R0-R3 = 30/29/28/30 → noise band width 4, R4 at the top.
- task_000028 / task_000958 final_pytest tail: `ERROR collecting
  test_final_state.py ... ModuleNotFoundError: No module named 'requests'`
  — verifier-side dependency gap, not agent-addressable.
- task_001090 final_pytest: `- monitor / + bash` (comm mismatch) — R3
  already instructed the comm check; unflipped → capability/technique gap.
- task_000010 final_pytest: `cannot import name 'namedtuple' from
  partially initialized module 'collections' ... /home/user/operator.py` —
  cwd module-shadow (R4 prompt already flags shadowing; still failed).

### Uncertainty

The risk of a no-op is under-spending on a live signal. I judged the
signal genuinely exhausted for the evolvable surface: every remaining
failure is a capability gap, an out-of-surface issue (model-client retry,
verifier missing deps), or an already-instructed-and-failed near-miss
whose only fix is forbidden domain knowledge. If a future round surfaces a
NEW generalizable harness mechanism (not a re-run of verify/anti-loop, not
task literals), ship it. Meanwhile the no-op guarantees the 32/50 peak is
not regressed by a speculative in-band tweak.
