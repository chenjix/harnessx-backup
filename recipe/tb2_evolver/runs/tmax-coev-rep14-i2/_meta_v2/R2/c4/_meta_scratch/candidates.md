# Candidates — R2 c4

Assigned focus: `task_000111_cbada64a` (scientific_computing) — reward=0,
exit=done at 7 steps. Deterministic OLS: agent committed slope m=2.5056,
oracle expected 2.5997. The agent's "verification" (step 7 & step 11) was a
requirements/format checklist plus "the values make sense" — it never
re-derived the slope by an independent method that would have exposed the
computation bug. This is the exact "done-but-numerically-wrong after
format-only self-verify" cluster.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Author a `ComputeCrossCheckReminder` processor that, on numeric-computation
tasks, appends an *independent-recompute* directive to the `_tb2_self_verify`
ACK tool-result — nudging the agent to re-derive the key value(s) with an
independent method before exiting, and to probe indexing/parsing/seed/precision
/search-boundary error sources on disagreement.

- Tasks affected (same mechanism: commit a self-consistent but numerically
  wrong computed value after a format-only self-verify):
  - `task_000111_cbada64a` — OLS slope 2.5056 vs 2.5997 (deterministic).
  - `task_001653_c4cafa73` — ETL centroid 36.3687,… / dist 18.6199 vs
    42.0095,… / 11.3444.
  - (weaker, same family) `task_001937_ac874115` (Optimal Grid 60 vs 50),
    `task_001035_26564093` (DP matrix 3.0 vs 4.0), `task_001330_f5aff1f5`
    (Monte-Carlo m=0.048 vs 0.050).
- Signal: `exit_reason=done` with very low step count (7,7,8,13,20) on
  scientific_computing/data_science tasks; `final_pytest` fails on a single
  numeric assertion (`Expected m ... got ...`, `Expected ... to be 50 got 60`,
  centroid/distance mismatch); `initial_pytest.passed=true` (infra fine).
- Verified (Read):
  - task_000111 step 7 body: "the values make sense … This is in the format
    `m,c,ci_lower,ci_upper` … as required" — verification is purely
    format/requirement; committed `2.5056,1.2262,3.9742,6.2925`. Step 11
    (after `_tb2_self_verify` ACK) re-checks the same checkboxes, never
    recomputes the slope. OLS is deterministic — an independent numpy/manual
    recompute of the same CSV would disagree and surface the C++ bug.
  - task_001653 final_pytest: `Centroid 36.3687,… Distance 18.6199` vs
    expected `42.0095,… 11.3444` — a large, non-noise disagreement an
    independent recompute would flag.
- Why Control not Instruction: a sibling round already bet Instruction
  (`h_numeric_crosscheck_v1`, R1, pending) — a system-prompt strategy section
  read once at task start. task_000111 shows the agent DID run the self-verify
  checklist yet only re-checked format by step 11; guidance planted at task
  start had already decayed. A Control hook that injects the recompute
  directive *at the self-verify exit moment* is temporally targeted at the
  decisive step (right when the agent tries to end_turn), a materially
  different and stronger shape than the pending Instruction bet, at a different
  lever. It also avoids editing the shared system prompt (lower blast radius).
- Why not extend `CustomSelfVerifyProcessor` in place: that class is read-only
  benchmark infra and its checklist is task-agnostic; a separate, armed,
  compute-scoped rider keeps the change isolated and revertable, and avoids a
  singleton-group collision.
- Retroactive check (A-corrective): yes for the deterministic cases —
  task_000111's OLS and task_001653's centroid are computations where a second
  *independent* method necessarily disagrees with the buggy committed value,
  giving the agent a concrete reason to debug before exit rather than
  declaring "the values make sense". Honestly weaker for same-mental-model
  bugs (001330 seed draw-order, 001035 DP recurrence) where re-deriving with
  the same wrong notion reproduces the wrong number — the nudge explicitly
  says "a rerun of the SAME code is not independent" and lists the specific
  culprits (draw-order, indexing) to counter exactly that, but its power there
  is a nudge, not a guarantee.
- expected_global_gain: Flips the recurring done-but-numerically-wrong compute
  cluster (>=2 strong tasks: 000111, 001653; up to 5 in the family across
  scientific_computing + data_science). Generalises because the mechanism —
  format-only self-verify of a computed value — is domain-agnostic; the fix
  encodes an independent-recompute discipline, not any task's answer.
- regression_risk: Low. Fires <=1x/task, only on armed compute tasks, and only
  APPENDS text to the self-verify ACK string (no message insertion, no system
  prompt edit — contract-clean). On an already-passing compute task the
  recompute agrees and the agent exits (confirmatory no-op). The only real risk
  is 1-3 extra verification tool calls pushing a near-budget compute task over;
  the 8 passing compute tasks this round span 8-67 steps with ample headroom,
  and the two shortest (8,9 steps) are far from budget.
- cost_shift: +1 to +4 short tool calls on compute tasks that reach exit;
  negligible on non-compute tasks (not armed). Net positive if it recovers
  even one pass in the 5-task family.
- rollback_trigger: Revert if next-round pass_rate on the compute cluster is
  flat/down, OR any previously-passing compute task (esp. task_000602,
  task_000710, task_001832) regresses to budget_exceeded/max_steps
  attributable to the added recompute steps, OR the nudge fires on non-compute
  tasks (arming too broad).
