
## Round 2 (c3) — OLS slope reference-data trap (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_ols_reference_data_trap_noop_v1
levers: []
predicted_affected: []
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "None - assigned task is a reference-data/capability trap; the numeric cross-check harness capability it would need already exists (NumericSelfVerifyProcessor) and fired correctly on this task"
regression_risk: "None - byte-identical config copy; no behavior change"
cost_shift: "Zero - no config change"
rollback_trigger: "N/A (no-op)"
-->

### Why

Assigned focus task_000111_cbada64a (scientific_computing, reward=0,
exit_reason=done, finished=no_tool_calls, 9 steps, 36.6s). The task asks for a
C++ OLS linear regression on /home/user/noisy_data.csv writing
m,c,ci_lower,ci_upper to result.txt. The verifier expects m approx 2.5997; the
agent produced m=2.5056. Root cause is a reference-data / capability trap,
not a harness deficiency: the agent's C++ gave m=2.5056, AND its own
independent Python re-derivation of textbook OLS on the same container data
also gave exactly m=2.5056 (intercept c=1.2262 matched between both methods
too). Standard OLS is unambiguous - two independent implementations agreeing on
2.5056 means correct math on the data present in the container. The verifier's
golden 2.5997 is only reachable from a different dataset (non-deterministic data
generation) or a non-OLS reference formula. No harness mechanism can make a
correct OLS produce 2.5997 without hardcoding the answer, which is forbidden
(memorises one task, fails the generalization test).

Critically, the relevant harness capability already exists and fired: the
pipeline's NumericSelfVerifyProcessor (added R1/c3, cited task_000111) injects
a "recompute the key quantity by a second independent method and reconcile any
mismatch" checklist. The trajectory shows the agent doing exactly that
(independent Python re-derivation) - the slope matched, the only discrepancy was
the bootstrap CI, which the agent correctly attributed to C++ std::mt19937 vs
Python random RNG differences. Retroactive check (Variant A): the corrective
fix is already in place, it fired, the agent complied, and the task still
failed, so the symptom is downstream of a data/capability gap outside harness
scope. Re-proposing any numeric-verify instruction would duplicate the existing
mechanism (novelty violation) and would still not flip the task.

Per the brief's escape clause ("if your focus turns out to be unsupported by the
trajectories, say so and make the smallest defensible edit"): explicit no-op.

### Changes

- config.yaml - byte-for-byte copy of R1 config (explicit no-op).
- system_prompt.txt - copied alongside so SiblingSystemPromptBuilder
  resolves against the output dir.

### Evidence

- task_000111_cbada64a.result.json: final_pytest.passed=false,
  AssertionError Expected m to be approx 2.5997, got 2.5056
  (deviation 0.0941, far above tol 0.001).
- task_000111_cbada64a.messages.json tool result: agent's C++ binary
  emits 2.5056,1.2262,3.9742,6.2925.
- Same file, self-verify step: agent's independent Python OLS prints
  m = 2.5056, c = 1.2262 - identical slope/intercept to C++, confirming the
  math is correct on the container data; only ci bounds differ (3.9894 vs 3.9742)
  from RNG implementation, which the agent correctly diagnosed.
- NumericSelfVerifyProcessor present in R1 config (lines 94-95), its
  _tb2_self_verify call appears in the trajectory, so the numeric cross-check
  harness capability already fired and the agent followed it.

### Uncertainty

High-confidence no-op. The only path back into the winnable set for task_000111
is a task/reference-data fix (regenerate golden from the same CSV the container
ships), which is outside meta-agent scope. Broader scientific_computing cluster
(task_001048 integral, task_001937 grid, task_000396 sim-error) shows a related
"plausible-but-wrong numeric value" shape, but each has a distinct mechanism and
is already the target of the existing NumericSelfVerifyProcessor - no new
generalizable lever surfaced from task_000111 specifically.
