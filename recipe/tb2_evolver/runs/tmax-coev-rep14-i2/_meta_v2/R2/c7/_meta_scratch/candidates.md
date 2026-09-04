# Candidates — R2 c7 (assigned focus: task_000264_ab8c7253)

## Decision: EXPLICIT NO-OP (config = byte-for-byte copy of R0)

No `## Candidate C-NNN` is shipped this round because the assigned focus
task's failure is not a harness deficiency. The decision contract permits
an explicit no-op (copy `current_config` → `output_dir/config.yaml`) with no
candidates; this file records the diagnosis that justified the no-op.

### Focus diagnosis — task_000264_ab8c7253 (data_querying)

`result.json`: `initial_pytest.passed=true`, `final_pytest.passed=false`,
2 failed / 1 passed. Two independent, both-required-to-pass failures:

**Failure 1 — CSV off-by-one (MODEL REASONING GAP, not harness).**
Agent output: `Alice (CEO),12 / Bob,5 / Charlie,5 / David,4 / Grace,4`.
Oracle expected: `Alice,11 / Bob,5 / Charlie,5 / David,4 / Grace,3`.
The agent's recursive CTE base case `SELECT e.id AS manager_id,
e.id AS subordinate_id` counts each node as its own subordinate. Critically
the delta is NOT a uniform -1 (Alice 12→11 and Grace 4→3 differ, but
Bob/Charlie/David match at 5/5/4). So the oracle's exact "subordinate"
semantics are subtle and the model modeled the recursion wrong. No
generalizable harness mechanism can inject the correct counting semantics
without hardcoding this one task's expected answer — forbidden by the
evolution philosophy and guaranteed not to survive the next round.

**Failure 2 — `USING INDEX` verifier brittleness (VERIFIER ARTIFACT, not
harness).** The agent DID everything correctly: created
`idx_employees_manager_id`, verified the plan changed from `SCAN e` to
`SCAN e USING COVERING INDEX idx_employees_manager_id`. The verifier greps
the literal substring `"USING INDEX" in content.upper()`. SQLite emitted
`USING COVERING INDEX`, which does not contain the exact substring
`USING INDEX` — the index IS created and IS used, but the naive substring
check misses it. The verifier test file does not exist during the agent
phase (TB2 sandbox topology, per playbook), so the agent cannot observe or
adapt to this string requirement. This is a verifier-brittleness artifact,
not something a general harness fix can anticipate.

### Why no harness fix (retroactive check + cluster scan)

- **No cluster.** `grep "USING INDEX"` / `"COVERING INDEX"` across all 50
  `*.result.json` returns ONLY task_000264. The query-plan-substring
  mechanism is unique to this one task (idiosyncratic-filter ≥2 tasks NOT
  met). No generalizable Control/Configuration mechanism applies.
- **Task cannot flip regardless.** Both tests must pass. Even if Failure 2
  were somehow avoidable, Failure 1 (the CSV recursion semantics) is an
  independent hard model-reasoning gap that no legitimate harness change can
  close. So there is no harness intervention that flips this task.
- **Considered & rejected: "verify computed results independently" prompt.**
  Retroactive check fails: the off-by-one is a shared-mental-model error;
  re-deriving the count with the same wrong notion of "subordinate" would
  not catch it. It would also risk cost inflation and regressions across the
  many already-passing clusters. Moreover a sibling proposal
  (`h_numeric_crosscheck_v1`, instruction lever) already targets exactly this
  compute-crosscheck class — re-proposing it here would drift onto another
  proposal's territory (brief forbids this) and duplicate a live pending bet.

### Global-optimization statement

- `expected_global_gain`: None claimed. No defensible generalizable
  mechanism exists for this focus; shipping a task-specific patch would very
  likely degrade global health (prompt bloat / cost inflation across 19
  passing tasks) with zero robust upside.
- `regression_risk`: Zero — byte-for-byte copy of R0 config.
- `cost_shift`: Zero — no config change.
- `rollback_trigger`: N/A (no-op).

### NEEDS_FROM_HUMAN

task_000264 requires (a) the exact oracle recursive-CTE "subordinate"
counting semantics (a model reasoning gap) and (b) verifier tolerance of
`USING COVERING INDEX` where it currently demands the literal `USING INDEX`
(a verifier-brittleness artifact). Both are outside harness control — no
harness fix; skip.
