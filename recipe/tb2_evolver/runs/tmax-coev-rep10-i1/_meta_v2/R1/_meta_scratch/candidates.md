# Candidates — Round 1 (tmax-coev-rep10-i1)

Baseline R0: 29/50 pass (58%). Worst domains: system_administration 0/5,
software_engineering 2/5, scientific_computing 2/5, security 3/5.

Failure exit-reason breakdown (21 fails): `done`=14, `budget_exceeded`=6,
`error`=1. The `budget_exceeded` cluster is the strongest *harness* signal:
every one of the 6 ran to the full 80-step budget and the verifier failed
on "required output file missing". The 14 `done` failures are mostly wrong
answers (model capability gaps) — not harness-fixable this round.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a step-budget deadline processor that injects escalating "commit your
best-effort deliverable to its required path NOW, then `ls` to confirm"
reminders as the step budget (`task.max_steps`) is consumed.

- Tasks affected: task_000010_644ab1c2 (system_administration),
  task_000118_3043e92d (system_administration), task_000264_ab8c7253
  (data_querying), task_000958_4bb2b05d (data_querying),
  task_001031_a8f0eb37 (scientific_computing), task_001321_658ce4a8
  (data_processing). All 6 `exit_reason=budget_exceeded` at 80 steps.
- Signal: `agent.exit_reason=budget_exceeded` + `agent.steps=80` on all six;
  `final_pytest.output_tail` in each is a "file missing" assertion
  (`results.json missing`, `top_managers.csv missing`, `operator.py does
  not exist`, etc.) — the agent never wrote the deliverable before running
  out of steps. The pipeline already ships a `TaskTimeReminderProcessor`,
  but the recipe runner never sets its `timeout_seconds`, so its
  `on_step_start` returns early every step (see harness.py:246) — the
  deadline nudge is inert. The budget actually being hit is *steps* (80),
  which the time reminder does not track.
- Verified (Read):
  - task_000264_ab8c7253.messages.json last 6 messages: agent writes
    "I've been stuck in a loop trying the same query" and "I keep getting
    the same result", cycles the same recursive-CTE approach, then three
    consecutive "Your previous response was cut off... continue" turns with
    no file write. Verifier: `top_managers.csv missing`, `query_plan.txt
    missing`, `no index` — zero deliverables on disk at step 80.
  - task_001031_a8f0eb37.result.json: `budget_exceeded`, 80 steps, 2336s;
    verifier `Results file missing at /home/user/results.json`.
  - task_000010_644ab1c2.result.json: `budget_exceeded`, 80 steps; verifier
    `operator.py does not exist`, `api_success.log does not exist` — nothing
    written.
  - task_000118_3043e92d.result.json: `budget_exceeded`, 80 steps; the two
    required scripts were written but tuned wrong (log size threshold) — this
    one is a partial: the reminder still helps by forcing earlier commit +
    verify, freeing steps to fix the threshold instead of exploring.
- Why Control not Instruction: the missing mechanism is a *runtime-timed
  hook* that must fire uniformly across every task as the step budget nears
  exhaustion — a per-task property (`step_id / max_steps`) no static prompt
  rule can observe. The system prompt cannot know when 65%/85% of the step
  budget is gone. This is exactly the `on_step_start` cross-task-guard shape
  Control owns; it mirrors the existing (but inert) `TaskTimeReminder`
  mechanism, keyed on the budget that is actually enforced.
- Why Control not Configuration: I considered just populating
  `TaskTimeReminderProcessor.timeout_seconds`, but (a) the runner exposes no
  wall-clock budget to key it off (only `max_steps`), and (b) wall-clock
  time correlates poorly with steps (a slow install eats seconds without
  eating steps). The step fraction is the correct, directly-available
  signal, and it needs a new hook.
- Retroactive check (A-corrective): yes — on task_000264 the agent was
  already looping by ~step 60; a step-65% nudge ("stop exploring, write your
  current query result to top_managers.csv and query_plan.txt now, ls to
  confirm") lands while ~28 steps remain and there is a runnable (if
  suboptimal) query in hand — enough to produce the two required files and
  flip the file-missing assertions. Same shape on task_001031 / task_000010:
  a best-effort file on disk beats an empty workspace at step 80.
- expected_global_gain: targets the 6-task `budget_exceeded` cluster
  spanning 4 domains (sys_admin, data_querying, sci_computing,
  data_processing) — a general "runs out of steps with nothing on disk"
  mode, so it should generalize to unseen tasks with the same shape. Even a
  1-3 task flip is net positive and it also trims the extreme tail runtimes
  (2336s / 953s / 816s) by discouraging late-stage thrash.
- regression_risk: low. The processor only *appends* user-role reminders
  (never mutates system prompt or existing messages), fires at most twice
  per task, and only past 65% of the step budget — well after the median
  passing task finishes (passing tasks completed in 8-52 steps). Tasks that
  finish before 65% of 80 steps (~step 52) never see the message, so the
  large passing cluster is untouched. Contract check passed clean.
- cost_shift: negligible-to-negative. Two short (~60-token) injections only
  on long-running tasks; by curbing late thrash it should *reduce* tokens on
  the tail tasks that currently burn the full 80 steps.

## Not pursued this round (model capability gaps — no harness fix)
- The 14 `exit_reason=done` failures (e.g. task_000015 software_engineering,
  task_001653 data_science 8 steps, task_001515 security 10 steps) declared
  done with a wrong/incomplete answer. These are reasoning/domain-knowledge
  gaps, not harness mechanism gaps — patching them would require embedding
  task-specific knowledge. Skipped per SOUL.md.
- task_001818 `exit_reason=error` (1 task, idiosyncratic) — single
  occurrence, does not meet the >=2-task systemic bar. Watch next round.
