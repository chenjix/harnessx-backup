## Round 1 — numeric-result interpretation audit

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_numeric_audit_v1
levers: [control]
predicted_affected: [task_000111_cbada64a, task_001330_f5aff1f5, task_001937_ac874115]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=20/50; +1/-4 gained=task_001653_c4cafa73 lost=task_000682_af0248a6,task_000939_1592be48,task_001098_f5acdd79,task_001832_dd672877; gating disabled (tolerance < 0)
expected_global_gain: "Raise catch-rate on the scientific_computing/data_science close-but-wrong-numeric cluster (only 1/5 sci-comp passed in r0) by steering the existing self-verify turn toward interpretation audit"
regression_risk: "Numeric tasks that were already correct spend a few extra Bash verification turns; non-numeric tasks untouched (processor stays silent). No correctness risk — only augments a tool result, never injects a message."
cost_shift: "Small + on numeric tasks (a few extra verification Bash calls); ~0 on non-numeric tasks; per-call token cap unchanged"
rollback_trigger: "If next round shows the numeric cluster pass-rate flat/down AND numeric tasks' mean step-count materially up, revert (cost without catches)"
-->

### Why

Assigned focus `task_000111_cbada64a` (scientific_computing) fails: the agent
wrote structurally-correct OLS+bootstrap C++, ran it in 7 steps / 16.9s, and
committed `m=2.5056` where the verifier expected `m≈2.5997`. OLS is
deterministic in the data, so the miss is an *interpretation* error (parsing /
index / formula), not a harness crash. The same shape recurs across the
numeric cluster: `task_001330` (Monte-Carlo fit, m=0.048 vs 0.050, 7 steps)
and `task_001937` (grid opt, 60 vs 50, 15 steps). In every case the agent
exited fast (`no_tool_calls`) and its one-shot `_tb2_self_verify` turn only
re-`cat`'d the output and re-confirmed format — it never re-audited the
algorithmic interpretation where the answer actually went wrong. The built-in
`CustomSelfVerifyProcessor` prompt (read-only) is oriented to file
existence/format, which is the wrong lens for numeric-computation tasks.

### Changes

- `processors/numeric_result_audit.py` — new `NumericResultAuditProcessor`
  (`MultiHookProcessor`). On `on_after_tool`, when the built-in synthetic
  `_tb2_self_verify` tool fires, it appends a computational-interpretation
  audit directive (re-read the method; check draw-order/seed, index/axis
  conventions, parsing edge cases, formula/rounding variant; recompute an
  independent second way; try the alternative reading of any ambiguity). Fires
  at most once, and only when the session produced a value-agnostic
  numeric-result signature. Contract-safe: augments `event.result` only.
- `config.yaml` — register the new processor immediately after
  `CustomSelfVerifyProcessor` (`_order=95`, after self-verify's `_order=90`).

### Evidence

- `task_000111_cbada64a` result.json: `finished=no_tool_calls`, `steps=7`,
  `elapsed_s=16.9`, verifier `abs(2.5056-2.5997)=0.094 > 0.001`. Messages
  after `_tb2_self_verify` (msgs 87-128): only `ls -lh`/`cat result.txt` +
  format narration — no re-derivation.
- `task_001330_f5aff1f5` result.json: `steps=7`, verifier `'0.048' != '0.050'`
  with hint "Ensure you used numpy.random.seed(42) and the correct
  parameters". Messages after `_tb2_self_verify` (msgs 86-129): `cat
  trajectory_fit.txt` + "Both values are rounded to 3 decimal places" — never
  questions the RNG draw order producing 0.048.
- `task_001937_ac874115` result.json: `steps=15`, verifier "Expected Optimal
  Grid to be 50, but got 60".
- Heuristic unit-checked: `_looks_numeric` fires on `2.5056,1.2262,...`,
  `m=0.048, c=40.237`, `Frame 0: X=41, Y=20`; silent on `ls -lh` output,
  version banners, and prose.

### Uncertainty

Honest weak point: for `task_000111`/`task_001330` the misread is subtle, so
re-reading may not deterministically flip them — the directive raises the
probability of catching an interpretation error, it does not guarantee it (the
retroactive check is partial-yes, strongest on `task_001937`-style
"alternative reading" misses). The intervention is deliberately free on tasks
where it doesn't fire, so the downside is bounded. If the numeric cluster
doesn't move next round while step-counts rise, revert per the trigger. This
was NOT patched by embedding any task answer or constant — it is a
generalizable strategy nudge gated on a value-agnostic signal.

## Round 2 — no-op: assigned focus is infra, not harness

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_docker_name_collision_noop_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "0 flips this round — the assigned failure is not fixable via HarnessConfig; documented the systemic infra bug for a human"
regression_risk: "None — config is a byte-for-byte copy of R1"
cost_shift: "0 — no config change"
rollback_trigger: "N/A (no-op)"
-->

### Why

Assigned focus `task_000118_3043e92d` fails with `status=error`,
`elapsed_s=0.1`, and NO agent trajectory. Root cause is a Docker container
name collision raised in `recipe/tmax_eval/docker_env.py::start_container`
BEFORE the run loop (and therefore any HarnessConfig processor/tool/prompt)
is ever invoked. This is a harness INFRASTRUCTURE bug, not a HarnessConfig
capability gap and not a model capability gap. It is systemic: 10/50 tasks
this round (20%) failed with the identical error and 0.1s elapsed.

### Changes

- `config.yaml` — byte-for-byte copy of R1 config (explicit no-op; verified
  `cmp` identical). No processor/tool/template authored.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — full diagnosis + recommended fix.

### Evidence

- `task_000118_3043e92d.result.json`: `status=error`, `elapsed_s=0.1`,
  error `docker run failed ... container name "/tmax-task0001183043e92d-1788106888"
  is already in use`.
- Same error shape on 9 other tasks (0.1s each): task_000010_644ab1c2,
  task_000028_7fe033ac, task_000140_01c78b42, task_000505_50b5162d,
  task_000748_c9807703, task_000933_1f27096a, task_001032_1adaccb9,
  task_001781_529727cf, task_001937_ac874115. Timestamps `-1788106887` /
  `-1788106888` show adjacent-second collisions.
- `recipe/tmax_eval/docker_env.py:105`: name = `f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"` — whole-second granularity + 20-char id truncation gives no per-container uniqueness within a second.

### Uncertainty

None on diagnosis (deterministic error text, pre-run failure, read-only
recipe). The fix (uuid/PID/ns suffix or rm-then-run/retry-on-Conflict) is
outside my write scope; HarnessConfig has no lever that runs before container
launch. Any config edit here would be drift onto invented territory, so the
correct action is the explicit no-op + NEEDS_FROM_HUMAN.

## Round 2 — infra flake, not a harness gap (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-01T12:00:00Z
hypothesis_id: h_docker_name_conflict_infra_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None from config — the assigned failure is a host-side Docker runner bug (container-name collision), not reachable by any processor/tool/template. Documented as an out-of-scope fix request."
regression_risk: "None — config is byte-for-byte identical to current_config."
cost_shift: "Zero — no change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus `task_000010_644ab1c2` (system_administration) did NOT fail
on agent behavior. Its `result.json` shows `status: error`, `elapsed_s: 0.1`,
and NO episode/message log — the task died at host-side `docker run` before
the agent phase ever started. The error is a Docker container-**name
Conflict**: `/tmax-task000010644ab1c2-1788106887` already in use. This is
identical across ALL 10 `status: error` tasks this round (000010, 000028,
000118, 000140, 000505, 000748, 000933, 001032, 001781, 001937), each at
0.1s with timestamps clustered at `1788106887/…888`. Root cause:
`recipe/tmax_eval/docker_env.py:105` builds the container name with
`int(time.time())` (1-second resolution), so concurrent workers / leftover
containers collide. HarnessConfig only evolves the in-container processor
pipeline + system prompt (tb2-playbook); no hook fires before the agent
launches. Therefore this is neither a harness deficiency nor a model
capability gap — it is a runner infrastructure bug outside my writable scope.

### Changes

- `config.yaml` — byte-for-byte copy of `current_config` (R1/config.yaml).
  Explicit no-op. No processors/tools/templates authored.

### Evidence

- `task_000010_644ab1c2/result.json`: `status: "error"`, `elapsed_s: 0.1`,
  `error: "RuntimeError: docker run failed ... The container name
  \"/tmax-task000010644ab1c2-1788106887\" is already in use ..."`.
- Cross-task: `grep -l '"status": "error"'` → 10/50 tasks, all with the same
  Conflict shape and near-identical timestamps → deterministic collision, not
  a per-task agent issue.
- `docker_env.py:105`: `name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]`.

### needs_from_human

See `_meta_scratch/NEEDS_FROM_HUMAN.md`. Fix `docker_env.py::start_container`
to use a high-entropy suffix (e.g. `uuid4().hex[:12]`) instead of
second-resolution time, and/or `docker rm -f <name>` before `docker run`,
and/or retry-with-fresh-name on Conflict. Until fixed, these 10 tasks are
un-scoreable regardless of agent/harness quality.

### Uncertainty

Low. The failure signature is unambiguous and host-side. Risk is only that a
future round mistakes these `status: error` tasks for agent failures and
spends config edits chasing them — this entry exists to prevent that.

## Round 2 — broaden HTTP-verifier dep-reminder trigger

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_verifier_http_dep_v2
levers: [control]
predicted_affected: [task_000106_23215092, task_000809_760d7fa0, task_000958_4bb2b05d, task_002063_8c8adcfe]
cited_candidates: [C-002]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flip the build-a-service-the-prober-calls cluster (4 tasks hard-failing on ModuleNotFoundError: requests regardless of service correctness) by making the R1 VerifierHttpDepReminder actually fire on their phrasing"
regression_risk: "Fires on 3 already-passing service tasks, but the injected message is advisory and `pip3 install requests` is idempotent, so it cannot flip a pass to a fail; _SERVICE_RE unchanged so no new fires on non-service tasks"
cost_shift: "Small + on ~11 service tasks (one reminder message + at most one pip install); ~0 on the other 39 tasks"
rollback_trigger: "If next round shows the 4 reqfail tasks still failing on `requests` (agent ignores the fired reminder) AND service-task mean step-count materially up, revert to the R1 narrow _VERIFIER_RE"
-->

### Why

Assigned focus `task_000106_23215092` (data_querying) fails reward=0 with the
verifier's `test_final_state.py` raising `ModuleNotFoundError: No module named
'requests'` during collection. The agent built a fully correct Flask API on
`127.0.0.1:8000` (co-authorship self-join SQL, networkx PageRank, oracle
subprocess, 404 handling) and exited cleanly in 39 steps — it scored 0 solely
because the external Python prober could not import `requests`. This is the
exact failure the R1 `VerifierHttpDepReminder` was built to prevent, but the
R1 reminder never fired: its `_VERIFIER_RE` demanded explicit *verifier*-family
vocabulary, and this task says "leave your API running ... so that our
automated integration tests can query it to verify your work." Across all 50
trajectories, four tasks hard-fail on `requests` (task_000106, task_000809,
task_000958, task_002063) and the R1 regex fired on 0/4 of them — a pure
recall defect in an otherwise-correct mechanism.

### Changes

- `processors/verifier_http_dep_reminder.py` — replaced R1's narrow
  `_VERIFIER_RE` with a broadened `_PROBER_RE` that recognises the full
  external-prober phrasing family (verifier / grader / integration test /
  automated test / "our tests" / "will|can query|send|hit|probe|exercise" /
  "leave it running" / "accept traffic" / "runs continuously" / "running in
  the background"). `_SERVICE_RE` (the strict is-this-a-network-service gate)
  is unchanged; reminder text updated to note the prober is Python even when
  the agent's own service is C++/Rust.
- `config.yaml` — repointed the processor `_target_` `file://` path to this
  candidate's copy; `dep_module: requests` knob unchanged.

### Evidence

- `task_000106_23215092.result.json`: `reward=0`,
  `final_pytest.output_tail` = "ModuleNotFoundError: No module named
  'requests' ... Interrupted: 1 error during collection". Message log confirms
  R1 reminder text ("EXTERNAL automated verifier") absent → R1 never fired.
- Regex replay over all 50 trajectories: R1 `_VERIFIER_RE ∧ _SERVICE_RE` fires
  on 0/4 reqfail tasks; widened `_PROBER_RE ∧ _SERVICE_RE` fires on 4/4.
- `task_000958` and `task_002063` use no verifier vocabulary at all — they say
  "server runs continuously ... to accept traffic" and "Leave the server
  running in the background"; those "leave it running for an external prober"
  markers are the generalisable structural signal added to the trigger.

### Uncertainty

The reminder raises but does not guarantee the fix — the agent must still act
on it (run `pip3 install requests` system-wide). If the model ignores the fired
reminder, the reqfail tasks stay F and this becomes a model-compliance gap, not
a harness recall gap → revert per the trigger. Downside is bounded: fires only
on genuine service tasks, message is advisory, install is idempotent, so it
cannot regress an already-passing task. No task-specific literal, port, or id
was embedded.

## Round 2 — numeric independent-recompute gate

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_numeric_recompute_gate_v1
levers: [control]
predicted_affected: [task_000111_cbada64a, task_001330_f5aff1f5, task_001937_ac874115, task_001035_26564093]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Raise catch-rate on the scientific_computing/data_science close-but-wrong-numeric cluster (4/5 failed in r0) by forcing the one missing ACTION at self-verify — an independent second computation + reconcile — that the whole cluster skipped"
regression_risk: "Numeric tasks already correct spend a few extra verify Bash turns; non-numeric tasks untouched (processor stays silent, gated on value-agnostic numeric result shape). No correctness risk — augments event.result only, never injects a message; agent still commits if the two computations agree"
cost_shift: "Small + on numeric tasks (one extra independent recompute + compare); ~0 on non-numeric; per-call token cap unchanged"
rollback_trigger: "If next round shows sci-comp/data-science numeric cluster pass-rate flat/down AND numeric tasks' mean step-count materially up (cost without catches), revert"
-->

### Why

Assigned focus task_000111_cbada64a (scientific_computing) fails: the agent
wrote structurally-correct OLS+bootstrap C++, ran it in 7 steps, and committed
m=2.5056 where the verifier expected m about 2.5997. OLS is deterministic in
the data, so the miss is an interpretation error (parsing / index / estimator /
formula variant), not a crash. The same close-but-wrong-numeric shape recurs
across the cluster: task_001330 (Monte-Carlo fit, m=0.048 vs 0.050),
task_001937 (grid opt, 60 vs 50), task_001035 (primer opt, GCGC vs GCAT). In
every failing case the agent's one-shot _tb2_self_verify turn only re-cat'd the
output and re-confirmed format — it never recomputed the answer independently.
Decisively, in the one PASSING analogue task_000011 the agent noticed a
discrepancy between its hand-calc and its program during self-verify, then
explicitly talked itself out of investigating ("let me trust the server
output... my manual calculation was wrong"). The cluster's shared gap is not
knowledge but a missing ACTION: an independent cross-check before commit. NOTE:
my baseline (R1 config) contains NO numeric self-verify augmentation — the R1
h_numeric_audit_v1 mechanism lives only in a sibling R1 candidate config, so
this cluster is unprotected in the baseline handed to this proposal.

### Changes

- processors/numeric_recompute_gate.py — new NumericRecomputeGate
  (MultiHookProcessor). On on_after_tool, when the synthetic _tb2_self_verify
  tool fires and the session produced a value-agnostic numeric-answer
  signature, appends ONE directive that forces the agent to recompute the
  answer a second, independent way (not reusing the first program's code),
  compare, and reconcile any disagreement before committing — explicitly
  forbidding "trusting" a clean run over a disagreeing cross-check. Fires at
  most once, silent on non-numeric tasks. Contract-safe (augments event.result
  only). _order=96 (after CustomSelfVerifyProcessor _order=90).
- config.yaml — register NumericRecomputeGate immediately after
  CustomSelfVerifyProcessor.

### Evidence

- task_000111_cbada64a result.json: finished=no_tool_calls, steps=7;
  final_pytest abs(2.5056-2.5997)=0.094 over tolerance. Msgs 87-128 after
  _tb2_self_verify: only ls + cat result + format narration.
- task_001330_f5aff1f5: verifier "Expected m=0.050, but found m=0.048. Ensure
  you used numpy.random.seed(42)..."; self-verify only re-reads the fit file.
- task_001937_ac874115: verifier "Expected Optimal Grid to be 50, but got 60".
- task_001035_26564093: verifier "Expected optimal primer 'GCAT', got GCGC".
- task_000011_d089ef35 (passing analogue) msgs 344-452: agent detects hand-calc
  (16.625) vs program (17.25) disagreement during self-verify, then commits
  anyway — demonstrates the missing forcing function.

### Uncertainty

Honest weak point: forcing an independent recompute raises the probability of
surfacing an interpretation delta but does not guarantee a flip — if the agent
repeats the same misreading in its second path, both agree on the wrong value.
Strongest on task_001937-style alternative-reading misses and task_000011-style
"already saw the disagreement" cases. Distinct from R1's h_numeric_audit_v1 (a
"re-read the spec" advisory): this forces the recompute ACTION the whole
cluster skipped, and is applied to a baseline lacking any numeric self-verify
augmentation. Free on tasks where it doesn't fire, so downside is bounded; if
the cluster stays flat while step-counts rise, revert per the trigger. No task
answers or constants were embedded — the gate is a generalizable strategy nudge
gated on a value-agnostic numeric signature.
