# Candidates — Round 1 (tmax-coev-rep14-i1)

Assigned focus: `task_000010_644ab1c2` fails (`exit_reason=budget_exceeded`,
80 steps, reward=0). Diagnosed before proposing.

## Diagnosis of task_000010_644ab1c2

Task: write `/home/user/operator.py` (a K8s manifest operator) that backs up
manifests, sets up a 9090→8080 port-forward, and drives an interactive CLI.

- Final verifier failure: `test_operator_script_exists` — `/home/user/operator.py`
  does not exist.
- Trajectory evidence (messages.json, verified by direct read):
  - msg 19: agent DOES write `/home/user/operator.py` (`cat > /home/user/operator.py << 'EOF'`).
  - msg 23: agent runs `mv /home/user/operator.py /home/user/k8s_operator.py` —
    **renames the required deliverable away from its specified path.**
  - msg 25–33: from here on the agent only ever references `k8s_operator.py`;
    `operator.py` is never recreated.
  - The last ~40 steps (msgs 40–70, post-compaction) are an unbroken
    port-forward / zombie-process debugging spiral (socat → python proxy → nc →
    kill -9 → retry). The compaction summary at msg 1 is entirely about the
    proxy and drops the deliverable-path requirement.
- Root cause = harness deficiency: as the agent approached the 80-step wall
  there was **no proactive signal** to (a) stop the sub-component spiral and
  (b) re-verify that the required deliverable exists at its EXACT path. The
  agent hit `budget_exceeded` with the primary output file missing/misnamed.

Why this is a harness gap, not a capability gap:
- The agent had already produced a working `operator.py` (msg 19). It lost the
  deliverable to a rename + spiral, not to lack of knowledge.
- The existing `CustomSelfVerifyProcessor` only fires on *voluntary* exit
  (finish_reason ∈ {end_turn, stop}, no tool call). A `budget_exceeded` run
  never reaches voluntary exit, so the verify-checklist never fired here.
- The existing `TaskTimeReminderProcessor` warns on a *time* budget, but the
  tmax harness path never injects `timeout_seconds` (confirmed in
  `recipe/tmax_eval/harness_runner.py::_run_async` — only sandbox_provider +
  tracer are injected). So `self._timeout` stays `None` and the time reminder
  is **inert**. The step budget (`BaseTask.max_steps=80`) has no reminder at all.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `StepBudgetReminderProcessor` that fires at fractions of `task.max_steps`
(0.60 soft, 0.80 hard) via `on_step_start`, injecting one user-role reminder to
stop sub-component exploration and finalize + verify every required output file
at its EXACT specified path (and not rename/relocate deliverables).

- Tasks affected (budget_exceeded cluster, reward=0, 80 steps):
  - task_000010_644ab1c2 (primary — deliverable renamed away + proxy spiral)
  - task_000118_3043e92d (daemon task; 80 steps, output never converged)
  - task_000206_a943669b (log-correlation script; 80 steps)
  - task_000958_4bb2b05d (C++ microservice; 80 steps)
  Same mechanism: agent burns the back half of the step budget on one
  sub-problem and hits the wall without finalizing/verifying the named
  deliverable(s). Fixing one (a step-budget nudge) helps all.
- Signal: `agent.exit_reason=budget_exceeded` + `steps=80` on all four;
  final_pytest shows a missing/misnamed required output (task_000010) or an
  unconverged deliverable at the wall.
- Verified (Read of task_000010_644ab1c2.messages.json):
  - msg 19 `cat > /home/user/operator.py << 'EOF'` (deliverable created)
  - msg 23 `mv /home/user/operator.py /home/user/k8s_operator.py` (renamed away)
  - msgs 40–70: continuous proxy/zombie debugging; no recreation of
    `operator.py`; no step-budget reminder present in context.
  - result.json final_pytest: `Operator script /home/user/operator.py does not
    exist.`
- Why Control not Instruction: the system prompt (sibling `system_prompt.txt`)
  and `CustomSelfVerifyProcessor` already carry the "verify exact output paths"
  instruction — the agent HAS the knowledge but never receives it at the
  decisive moment because it never voluntarily exits and there is no
  step-triggered delivery mechanism. This is a missing *mechanical hook that
  fires uniformly at a step-count boundary*, which a prompt rule cannot express.
  It is the step-count analog of the existing (inert) `TaskTimeReminderProcessor`.
- Why Control not Configuration: `TaskTimeReminderProcessor` cannot be made to
  fire on step count by tuning its kwargs — it is keyed off wall-clock time and
  `timeout_seconds` is never injected in this path. A new hook is required.
- Retroactive check (A-corrective): yes — a hard reminder at step ~64/80
  ("write required outputs at their EXACT path NOW; do not rename/relocate
  deliverables; verify with ls") lands in task_000010's context ~16 steps
  before the wall, at a point where the agent still had budget to run
  `cp /home/user/k8s_operator.py /home/user/operator.py` and re-verify. That
  single restore flips `test_operator_script_exists` from fail to pass.
- expected_global_gain: targets the recurring `budget_exceeded` cluster
  (≥4 tasks this round). Even when it does not flip a task, it biases the last
  ~third of the budget toward deliverable finalization, the highest-leverage
  action near the wall.
- regression_risk: low. It only appends one short user message at 60% and one
  at 80% of the budget, and only when `max_steps >= 20` (so short passing tasks
  that finish in <12 steps — the bulk of the passing cluster — never see it).
  It never removes messages or mutates the system prompt (contract-clean). Worst
  case is a few dozen extra tokens on long-running tasks.
- cost_shift: negligible increase — at most two short injected user messages per
  task, and only on tasks that run past 60% of an 80-step budget (a minority).
  Likely net-neutral to slightly positive on cost by curbing end-of-budget
  spirals.
