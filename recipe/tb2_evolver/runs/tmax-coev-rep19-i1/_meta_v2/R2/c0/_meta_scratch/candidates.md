# Candidates — R2 / c0

Assigned focus: **`task_000010_644ab1c2`** (system_administration) — `exit_reason=budget_exceeded`, steps=80, reward=0.

## Candidate C-002

**Three-axis tag:** lens = *budget-exhaustion / premature-cap* · lever = **control** · intent = *convergence gate*

**Change:** new `MultiHookProcessor` `StepBudgetVerifyProcessor` (authored under
`processors/step_budget_verify.py`), registered `_order=7` (right after
`TaskTimeReminderProcessor`). It counts steps locally and, once the run crosses
`warn_fraction=0.8` of the `step_budget=80` cap (≈ step 64), injects **one**
user message: a concrete "you are near the step cap — confirm every required
output artifact exists at its exact path, restore any requirement a workaround
renamed/skipped, and prefer a simpler correct deliverable over retrying a stuck
sub-problem" checklist. Fires at most once per run; resets per task.

### Signal (verified from trajectories)

- `task_000010_644ab1c2.result.json`: `agent.exit_reason=budget_exceeded`,
  `steps=80`, `reward=0`. `final_pytest` fails on
  `test_operator_script_exists` — `os.path.isfile('/home/user/operator.py')` is
  **False**.
- `task_000010_644ab1c2.messages.json` msg 56: the agent hit an import error
  (its `operator.py` shadowed the stdlib `operator` module) and *renamed the
  required file to `k8s_operator.py`* — permanently violating the
  `test_operator_script_exists` requirement. It then spent msgs 57–69 fighting a
  hanging pexpect/`timeout` command (exit 124/137) and never restored
  `/home/user/operator.py`. It never reached a voluntary exit-intent turn, so
  `CustomSelfVerifyProcessor`'s checklist never fired.
- Pipeline gap: `TaskTimeReminderProcessor` is a **no-op** in the live config —
  no `timeout_seconds` is set, so its `on_step_start` returns early. Confirmed:
  0/50 trajectories contain any time-reminder text. So NO budget-proximity
  verification nudge is active in the current pipeline.

### Retroactive check (would-this-have-helped, counterfactual variant)

At ≈ step 64, the agent would have received an explicit instruction to
`ls /home/user/operator.py` and restore the required path. In this trajectory
the required file had been renamed away by step 56; a step-64 prompt to confirm
each required output path exists — and that a rename means the requirement is
unmet — targets exactly the miss (`test_operator_script_exists`). Flip is
*plausible not certain* (the model still needs to solve the pexpect hang), but
the sure win is converting "burned 80 steps, produced nothing at the required
path" into "spent the tail restoring/validating the required deliverables."

### Why control (convergence gate), not instruction/action

- **Not instruction (system prompt):** the existing prompt already says "verify
  before you stop"; the failure is that the agent never *stopped* — it died at
  the cap. A static prompt line cannot re-fire mid-run at budget proximity. A
  step-counting processor can.
- **Not action (new tool):** TB2 exposes only `Bash`; no tool can be added.
- **Control** is the right lever: the deficiency is a missing *runtime*
  convergence trigger keyed on step-budget proximity — reusing the pipeline's
  own proven self-verify content but firing on a signal (`budget_exceeded`
  risk) that the exit-intent gate structurally cannot observe.

### Pareto statement

- `expected_global_gain`: targets the `budget_exceeded` cluster (2 tasks:
  `task_000010`, `task_001701`) AND the broader `done`-but-reward-0 cluster (17
  tasks) where the agent stops with wrong/missing outputs — a step-64 checklist
  gives every long-running task a forced convergence checkpoint to salvage
  required deliverables. Generalizes: task-agnostic, names no path/command.
- `regression_risk`: low. The nudge is a single appended user message on tasks
  that reach ≥ step 64 (short passing tasks — most passes finish < 40 steps —
  never see it). Worst case: a small distraction on a long task that was going
  to pass anyway; mitigated by firing once and by content that only re-asserts
  requirement satisfaction (never contradicts the task).
- `cost_shift`: near-neutral. One extra user message (~250 tokens) on the subset
  of runs that reach step 64; expected to *reduce* cost on `budget_exceeded`
  tasks by steering them off a stuck loop toward convergence before the cap.
- `rollback_trigger`: if R3 pass_rate is flat/down AND any previously-passing
  long-running task (≥64 steps: `task_001818_b251e5ea`, `task_001673_86224c91`)
  regresses to reward 0, revert.

### Note on the assigned task's other cause (capability, not harness)

`task_000010` also hit a genuine model-capability gap: running
`python3 /home/user/operator.py` from cwd `/home/user` makes the script shadow
the stdlib `operator` module (circular import). The correct fix is to run from a
different cwd / adjust `sys.path`, not to rename the required file. This
shadowing pattern appears in **only 1 of 50** trajectories, so a
shadowing-specific processor would be a one-task patch — declined as a
capability gap. The harness fix here targets the *general* budget-exhaustion /
missing-required-output failure the task exposes.
