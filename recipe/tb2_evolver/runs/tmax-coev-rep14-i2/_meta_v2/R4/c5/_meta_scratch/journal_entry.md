
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
