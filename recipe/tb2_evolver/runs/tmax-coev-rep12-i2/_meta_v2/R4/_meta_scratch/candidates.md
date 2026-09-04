# Candidates — R4

## Candidate C-004
[lens: success | lever: instruction | intent: preservative-transfer]

Install a real general-workflow system prompt (survey → plan → diagnose-before-retry
→ verify each deliverable at its EXACT path/format the way the external checker will
→ keep required services alive) in place of the shipped 5-line default stub, and
transfer these habits to the failing `exit_reason=done`-but-wrong cluster.

- Tasks affected:
  - passing (habit fired / short clean finish): task_000381_97a0c6c6 (6 steps),
    task_000707_f163b6d5 (9 steps), task_000798_a3824955 (6 steps),
    task_000457_afcbea72 (7 steps) — scoped, verified, clean exits.
  - failing (habit absent at the decisive step, `exit_reason=done` yet wrong or
    missing output): task_002146_0bc2994c (declared "TASK COMPLETE" while the
    checker got ConnectionError on the required service port), task_000032_3fb303f6
    (wrote report.txt but with the wrong value; never independently recomputed the
    expected line), task_000885_d1c1007b (deployed model service that crashed under
    the checker's own request), task_000348_31fb8c8a (server endpoint returned
    `null` for a required field — never exercised its own endpoint before exit).
- Signal: `system_prompt.txt` beside R3/config.yaml is still the 5-line
  `DEFAULT_TMAX_PROMPT` stub even though the R3 config comment claims a
  general-workflow prompt was installed — the intended Instruction change was
  documented but never actually written. Meanwhile 15 of the R3 failures are
  `exit_reason=done` (agent called end_turn believing it was finished) with a
  wrong-value / missing-output / dead-service pytest tail, not a loop.
- Verified (Read):
  - passing end — task_000381 finishes in 6 steps with a scoped survey→act→confirm
    sequence; task_000707 in 9; task_000798 in 6; task_000457 in 7 (per-task
    result.json `agent.steps`, all `exit_reason=done`, all reward=1).
  - failing end — task_002146 final tool output literally prints
    `=== TASK COMPLETE ===  Manager is running: 1 process(es)` and the assistant
    stops, but the checker fails with
    `ConnectionError HTTPConnectionPool(host='127.0.0.1', port=8080)`; the agent
    never exercised the served endpoint before declaring done.
    task_000348 final_pytest tail: `TypeError: unsupported operand type(s) for -:
    'NoneType' and 'float'` — the `/process_spectrum` endpoint returned a null
    field; the agent never POSTed to its own server to check the response shape.
    task_000032 final_pytest tail: report line 1 is `admin123`, expected
    `network2023` — no independent recomputation before exit.
- Why Instruction not Control: the three prior rounds (R1/R2/R3) all shipped
  Control-lever loop breakers; direct transcript inspection shows the R3
  command-repetition nudge armed **0 times** across all 50 tasks and the R2 content
  nudge is not present either, so the Control lever on this cluster is exhausted /
  unverifiable. The remaining failures are `exit_reason=done` (not loops) — a
  mechanical hook cannot decide, per arbitrary task, whether the produced value is
  correct or whether the right service is up; that requires the agent to reason
  about the task's own success criteria. This is a knowledge-of-when gap
  (when/how to verify), the canonical Instruction case. It is strategy-only with no
  task literals, so it does not embed domain knowledge.
- Why Instruction not Configuration: no existing knob encodes "verify each
  deliverable at its exact path before exit" — the prompt builder already reads a
  sidecar, so the change is a content edit, not a knob tune.
- Retroactive check (C-preservative-transfer): partial-yes. Passing end is grounded
  (scoped clean finishes already exhibit survey→verify→confirm). Failing end is
  grounded where the blocker is a *skipped verification the agent could have done
  with Bash* (task_002146 never hit its own port; task_000348 never POSTed to its
  own endpoint; task_000032 never recomputed the expected line) — an explicit
  "exercise the endpoint / recompute and compare before declaring done" rule would
  have surfaced the defect while the agent still had steps. It will NOT flip
  purely capability-bound tasks (e.g. OCR policy extraction, Go circular-import
  refactor); those are logged as capability gaps, not claimed here.
- expected_global_gain: recovers a slice of the ~15-task `exit_reason=done`-but-wrong
  cluster where the defect is a *skipped self-check* (dead service, wrong endpoint
  response, un-recomputed value) rather than an unreachable capability — plausibly
  2-4 flips, and structurally hardens every task against premature-exit-with-empty-
  output going forward.
- regression_risk: a longer prompt could over-verify and inflate steps on already-
  short passing tasks, risking a borderline budget regression. Mitigated: the prompt
  is strategy-only and explicitly scopes verification to *required* deliverables;
  passing tasks finish in 6-40 steps with wide headroom under the 80-step cap. No
  task-specific literals, so no single-task memorization. If a currently-passing
  short task regresses with visible over-verification thrash, revert to the stub.
- cost_shift: mildly positive (a few extra verification Bash calls per task on
  tasks that previously exited early); bounded by the same step cap, and partly
  offset because clearer up-front planning reduces mid-task flailing.
