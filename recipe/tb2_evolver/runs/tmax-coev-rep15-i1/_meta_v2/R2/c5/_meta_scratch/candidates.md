# Candidates — R2/c5 (focus: task_000396_e56917e2)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `MetricReconcileReminderProcessor`: a one-shot, exit-time Control hook that
— on tasks whose description states a quantitative acceptance criterion AND
where the agent's own Bash activity shows it wrote a computed value into an
output/metric file — injects a nudge to reconcile the *written number* against
the *stated bound* before exiting, and to NOT exit if its own prior analysis
already flagged the result as wrong.

- Tasks affected (corrective, same mechanism):
  - `task_000396_e56917e2` (primary focus)
  - `task_001653_c4cafa73`
  - `task_000748_c9807703`
- Signal: `exit_reason=done`, `finished=no_tool_calls`, `reward=0`; verifier
  `final_pytest` fails a *numeric* assertion on a value the agent wrote to an
  exact output file (`assert 0.6201 < 0.1` on validation.log; centroid/distance
  value mismatch in etl_report.txt; leaked-bytes count mismatch in
  leak_bytes.txt). Stock `CustomSelfVerifyProcessor` fired in each case; the
  agent's follow-up re-checked only file existence/format.
- Verified (Read, message bodies quoted):
  - `task_000396_e56917e2` msg 43: agent narrates "The maximum deviation is
    quite high (0.62), which suggests the simulation might still have issues";
    msg 45: "the amplitude is growing significantly ... This is not correct for
    a harmonic oscillator". Yet msgs 51-62 (after `_tb2_self_verify` fired at
    51) only re-check file existence, the Makefile `-lm`, and the `dt_new` line
    — never reconcile 0.62 against the task's "match the analytical reference"
    goal — then exit. result.json final_pytest: `assert 0.6201280000000001 <
    0.1`. Task text (msg 0): "the integration diverges and fails to match the
    analytical reference dataset".
  - `task_001653_c4cafa73` last-4 assistant narrations: agent confirms
    "output file ... created with the correct format: `Centroid: 37.3120, ...
    Distance: 17.6896`" and declares done on FORMAT; verifier fails on the
    VALUES. Task text frames a computed similarity metric that must match.
  - `task_000748_c9807703` late narration: "The leak_bytes value changed from
    138 to 214 because valgrind's output can vary slightly between runs" —
    agent explicitly notes the metric is unstable/uncertain, then exits anyway;
    verifier compares leak_bytes.txt against an expected leaked-bytes count.
- Why Control not Instruction: the mechanism must fire *exactly at the exit
  decision*, conditioned on runtime signals only visible mid-run (the agent
  actually wrote a value to a metric file) — an always-on system-prompt rule
  fires on every task including the pure-transform majority (prompt bloat, no
  targeting) and the agent already skims a static "verify values" line (stock
  self-verify's weak value item is precisely what it rubber-stamps). A Control
  hook that arms only on the co-occurrence of criterion-vocabulary + a
  metric-write, and delivers the nudge via the proven keepalive-tool-call
  exit-intercept, targets the exact turn where the miss happens.
- Why Control not Configuration: no existing knob expresses "reconcile a
  written numeric metric against the stated bound at exit"; the stock
  self-verify checklist is existence/format oriented and exposes no such
  parameter.
- Retroactive check (A-corrective): yes — in all three cases the correct value
  (or the doubt about it) was already in context at the decisive exit turn; a
  reconcile-the-number-against-the-bound prompt at that turn gives the agent a
  concrete reason not to rubber-stamp. For task_000396 the agent had 32/80
  steps used and had already diagnosed divergence, so it had budget and a lead
  to fix the root cause; for task_000748 it had already noticed instability and
  could pin the value; task_001653 had the format right and only needed to
  re-derive the numbers. The nudge cannot conjure correct math the model can't
  do, but it converts a self-contradicted rubber-stamp exit into a second,
  targeted attempt — the harness-actionable half of the failure.

### Pareto reasoning
- `expected_global_gain`: Flips the "silent wrong-metric exit" slice of the
  large `done`/`reward=0` cluster — tasks that write a graded numeric value to
  a file under a stated acceptance bound. Generalizes to any unseen task with a
  quantitative success criterion and a metric-file deliverable.
- `regression_risk`: Low. New singleton group `metric_reconcile_reminder`
  (`_order=93`), additive — replaces nothing, changes no control flow. Arms only
  when BOTH task criterion-vocabulary AND an agent metric-write are seen; fires
  at most once; injects one user message via the contract-clean keepalive path;
  always yields to a genuine exit on the next turn, so a correctly-finished run
  is never trapped. Worst false-positive cost: one ~300-token advisory on a
  criterion-task that already had the right value — the nudge explicitly says
  "if it satisfies the bound, finishing is correct." Pure-transform / one-shot
  answer tasks and non-criterion tasks never arm.
- `cost_shift`: +1 reminder message + typically +1-3 Bash reconcile/recompute
  round-trips on armed tasks only; ~0 on the tasks that do not arm. Net-positive
  when it converts a 0-reward run into a pass.
- `rollback_trigger`: If the next round shows task_000396 still failing
  `assert max_dev < 0.1` AND task_001653/task_000748 still failing their numeric
  assertions AND no criterion-task flips, the residual blocker is model
  capability (it cannot compute/align the metric) not verification discipline —
  revert the processor. Also revert if any previously-passing criterion-task
  regresses to done/reward=0 or budget_exceeded with this processor firing.
