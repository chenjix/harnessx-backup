# NEEDS_FROM_HUMAN — task_000264_ab8c7253 query-plan grader

## New finding this round (R6/c5)

The `test_query_plan_output` assertion in `/tmp/test_final_state.py` for
`task_000264_ab8c7253` requires the literal substring `"USING INDEX"` in the
uppercased EXPLAIN QUERY PLAN. Local repro (sqlite 3.37.2, the container's
version) shows:

- A plain index `CREATE INDEX idx ON employees(manager_id)` with a recursive CTE
  that only touches `manager_id`/`id` produces `USING COVERING INDEX` — the index
  is *covering*, so the bare token `USING INDEX` never appears. This is the
  optimal, correct optimization and is what the agent produced (msg 18/26).
- The bare `USING INDEX` token IS reachable, but only by writing the recursive
  query so it selects a column NOT in the index (e.g. `SELECT e.name` inside the
  recursion), forcing a non-covering lookup:
  `SEARCH e USING INDEX idx (manager_id=?)`.

So the earlier R4 note ("unfixable capability gap") is too strong — the state is
reachable — BUT reaching it requires sqlite-plan-string domain knowledge (know
that COVERING triggers on covering indexes and how to defeat it). Embedding that
into a harness processor/prompt would be a task-specific literal (banned). No
legitimate general harness lever supplies it.

## Recommendation (if graders are tunable)

The `USING INDEX` substring check is arguably an over-strict false-negative: it
rejects a *correct and better* covering-index optimization. Relaxing the
assertion to accept `USING COVERING INDEX` (i.e. check `"USING" and "INDEX"` on
the same line, or `"USING INDEX" in content or "COVERING INDEX" in content`)
would make the test reward the optimization the task actually asks for.

## The other sub-failure (off-by-one CTE count)

`'Alice (CEO),12' != 'Alice (CEO),11'` — the recursive CTE base case seeds each
employee as its own subordinate (+1 everywhere). Alice's 12 equals total
headcount, a semantic red flag. This is a model SQL-reasoning slip. The one
legitimate general harness lever (strengthened output-contract self-verify with
independent re-derivation) is already in flight as R2 `h_output_contract_verify_v1`
(pending, task_000264 = primary predicted_affected). Re-proposing collides with
novelty/attribution — hence this round is a no-op.
