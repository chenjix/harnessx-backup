# Candidates — R1 / c0

Assigned focus: `task_000010_644ab1c2` (system_administration) fails with
`exit_reason=budget_exceeded` at the 80-step cap. Diagnosis below is grounded
in the message log, then generalized to the benchmark-wide `budget_exceeded`
cluster.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a step-budget-based converge guard (`StepBudgetReminderProcessor`) that
injects a "stop exploring, write + verify every required deliverable" nudge at
60% and 85% of the run's `max_steps`.

- Tasks affected (same mechanism — thrash to the step cap, deliverable never
  finished): `task_000010_644ab1c2`, `task_000958_4bb2b05d`,
  `task_001032_1adaccb9`, `task_001321_658ce4a8`. All four are
  `exit_reason=budget_exceeded` at exactly `steps=80` (the run loop's
  `max_steps` cap).
- Signal: `agent.exit_reason=budget_exceeded` + `agent.steps=80` on all four;
  each message log also shows 1–5 `finish_reason=length` "cut off by the token
  limit" continue-prompts, i.e. the agent narrates/probes without converging.
- Verified (Read of `task_000010_644ab1c2.messages.json`):
  - Task requires writing `/home/user/operator.py`. The agent spent all 80
    steps debugging the port-forward / mock_api / proxy (steps [13]-[69]:
    repeated `pkill -9 socat`, rebuilding `/tmp/proxy.py`, probing ports with
    `data=b'test data'`) and **never wrote `operator.py`**. `final_pytest`
    confirms: `test_operator_script_exists` fails (`/home/user/operator.py`
    absent) and the API log contains only `Applied: test data` — the agent's
    debug payload, never the real manifests.
  - No step in the log re-reads the task to checklist the deliverable; the
    agent has no budget signal (TaskTimeReminderProcessor is inert — see
    below) so it never pivots from debugging to producing output.
  - Cross-check `task_000958_4bb2b05d` / `task_001321_658ce4a8` message logs:
    both = 70 messages, `budget_exceeded` at step 80, 4–5 length-truncation
    continues — same thrash-without-converging shape on entirely different
    tasks (C++ HTTP microservice; C stream filter).
- Why Control not Instruction: the sibling `TaskTimeReminderProcessor` already
  encodes exactly this discipline in the *prompt-side* nudge text, but it is
  **dead code in this eval** — its `on_step_start` returns early unless a
  `timeout_seconds` is configured, and the config sets only `warn_at`. The
  benchmark caps by *step count*, not wall time, so no budget signal reaches
  the agent regardless of prompt wording. A static system-prompt rule
  ("converge before the budget runs out") cannot self-trigger at the right
  moment — the agent has no visibility into how many steps remain. A Control
  hook that reads `task.max_steps` and fires at a fraction of it is the only
  lever that delivers the reminder *at the decisive moment*. It also carries
  no task-specific literal, so it generalizes to every task in the suite.
- Why Control not Configuration (tune the existing time reminder): the time
  reminder is time-driven and `timeout_seconds` is injected by the recipe
  layer, not settable in this config; even if set, wall-clock is the wrong
  axis — the cap that bites is `max_steps`. A new step-driven processor is
  required.
- Retroactive check (A-corrective): yes — in `task_000010` the agent was still
  cycling debug commands at steps 60-80 with `operator.py` unwritten; a nudge
  at step 48 (60%) and step 68 (85%) explicitly redirecting "write the required
  output file now, then `ls -l` it" targets exactly the missed action. The
  simplest operator.py (backup tarball + socat forward + pexpect apply) is well
  within the model's shown capability — it demonstrably wrote working proxies
  and hit the API 200; it simply never assembled the deliverable because
  nothing pulled it out of the debug loop.
- expected_global_gain: closes the `budget_exceeded` cluster (4 tasks across 4
  domains: system_administration, data_querying, file_operations,
  data_processing). Generalizes because the mechanism — no budget signal on a
  step-capped run — is benchmark-wide, not task-specific.
- regression_risk: LOW. The processor only *appends* at most two extra user
  messages late in a run, and only when the run is already ≥60% through its
  budget — tasks that finish well under budget (the 30 passing tasks, mostly
  <60 steps) never see it fire. It cannot block or alter tool calls. Worst
  case is a few hundred extra prompt tokens on long runs. Contract-safe: mirror
  of `TaskTimeReminderProcessor`'s `on_step_start` message-append form
  (appends to both `messages` and `raw_messages`).
- cost_shift: negligible-to-slightly-positive. Adds ≤2 short user messages on
  runs that reach 60%+ of budget. If it converts thrash-to-cap runs into
  earlier stops with a written deliverable, net token/cost *drops* (a
  budget_exceeded task burns the full 80 steps today).
- Rollback trigger: if the next round shows a regression on any
  currently-passing task attributable to premature convergence (agent stops
  before finishing when it had budget), or the four target tasks remain
  `budget_exceeded`, revert this processor.
