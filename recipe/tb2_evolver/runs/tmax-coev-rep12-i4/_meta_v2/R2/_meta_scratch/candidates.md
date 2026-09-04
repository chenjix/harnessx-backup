# Candidates — R2

Round context: R1 pass_rate = 0.20 (10/50), unchanged from R0. R1 shipped
`BashLoopBreakerProcessor` (control). Post-hoc inspection of R1 trajectories
shows the loop-breaker fired on only 4/11 budget_exceeded tasks, and on those
the model **ignored the BLOCKED redirect and re-emitted the byte-identical
command 5–33 more times** (task_000032: 33 consecutive BLOCKs, never once
tried the required deliverable). Mechanical redirects are being ignored — the
control lever has hit a capability wall on the loop cluster.

Dominant failure shape this round (evidence below): the agent runs to
`exit_reason=done`, confidently declares success, but the verifier fails on
**wrong computed content** (28 done-but-fail tasks; 16 clear value/content
mismatches). In several, the agent *did* run a "verify" step but the verify
never round-tripped its answer against the constraint the task actually states.

## Candidate C-002
[lens: failure | lever: instruction | intent: corrective]

Deploy the general solver workflow the current config's own header comment
claims it ships (survey → plan → implement → **round-trip verify** → confirm
deliverables), which is in fact NOT deployed — the live `system_prompt.txt`
beside R1's config is the bare 5-line default. Center it on a general
**check-your-answer / round-trip verification** discipline and a
**preserve-partial-credit** rule for multi-step tasks.

- Tasks affected (corrective, >=2 distinct, same mechanism — commit a wrong
  answer that a round-trip against the stated constraint would have caught):
  - task_000763_7713d5ae — reversed a weak-XOR token to PIN `2778`, wrote it,
    "verified" only by running the forward algo on 2778 and eyeballing output;
    never confirmed the algo on 2778 reproduces the *given* token `1A3B`.
    Ground truth was `2394`.
  - task_000567_d082f802 — wrote PCA sums; expected `PC1_Sum: 7.7709`, produced
    `6.9499`; committed without re-deriving against the stated determinism/seed
    requirement.
  - task_000142_47a40a0c — symlink task; wrote link pointing at `file_A.dat`
    where the spec required `financial_records_2021.dat`; never cross-checked
    each produced link target against the task's stated target.
  - Secondary cluster it also touches: budget_exceeded tasks with partial
    passes (task_000032: 2/3 subtests pass) where the agent burned the whole
    budget on ONE doomed subgoal instead of banking the deliverables it had.
- Signal: `exit_reason=done` + verifier `AssertionError` on a *value*
  (not a missing file) across >=3 distinct tasks; agent's final assistant
  message asserts success. Distinct from budget_exceeded loop cluster.
- Verified (Read):
  - task_000763 body — command block computes `PIN = TOKEN ^ KEY` then runs
    `/home/user/token_algo.sh 2778` as its only check; final `ls`/`cat`
    confirms files exist but never re-checks the token round-trips. Verifier:
    `Expected PIN '2394' ... but found '2778'`.
  - task_000567 body — verifier: `assert 'PC1_Sum: 7.7709' in 'PC1_Sum:
    6.9499...'`; agent declared done.
  - task_000142 body — verifier: `Symlink primary_record_link.dat points to
    file_A.dat, expected financial_records_2021.dat`; agent declared done.
- Why Instruction not Control: a mechanical post-hook cannot compute the
  correct answer or know the task's intended value — it has no ground truth.
  The gap is that the agent *has* the tools to round-trip-verify (it can run
  the forward function on its own output and diff against the given constraint)
  but does not know to do so as a discipline. This is a "knows-when/in-what-
  order" gap = Instruction. It is strategy-only: no task constants, no answers,
  no per-task code — the rule ("when you derive a value from a given target,
  re-run the forward process on your value and confirm it reproduces the
  target; when a task lists an exact expected string/path/target, diff your
  output against it verbatim before committing") generalizes to any unseen
  compute-and-verify task.
- Why Instruction not Action: the agent already has Bash; it needs no new
  capability, only the discipline to use it to self-check.
- Retroactive check (A-corrective): partial-yes. For task_000763 the round-trip
  rule directly catches the error: running the forward algo on 2778 does NOT
  produce token `1A3B`, which the agent would have observed and been forced to
  keep searching. For task_000142 a verbatim target-diff catches the wrong
  symlink target. For task_000567 the seed/determinism cross-check is weaker
  (may still need capability). Honest expectation: flips a subset, not all —
  but the mechanism is correct and general, and regression risk is low.

- expected_global_gain: Targets the largest failing cluster (28 done-but-fail,
  16 value-mismatches). Even a modest hit rate (2–4 flips) moves pass_rate
  materially from 0.20, and the discipline generalizes to any compute-verify
  task, not the training set.
- regression_risk: LOW. The prompt is strategy-only and additive; it cannot
  remove a capability. Small risk: added verification steps consume a few extra
  steps per task, marginally raising budget pressure on tasks already near the
  step cap. Mitigated by pairing the verify rule with an explicit
  "abandon a doomed subgoal and bank completed deliverables" rule that REDUCES
  wasted steps on the budget_exceeded cluster. Passing tasks already survey +
  verify informally, so the rule codifies existing good behavior for them.
- cost_shift: Roughly neutral-to-down. +a few verify steps on short tasks;
  −many wasted steps on the budget_exceeded/loop cluster (abandon-doomed-
  subgoal rule). Net expected slightly down given the 80-step runaways.
- rollback_trigger: If R3 pass_rate < 0.20 (below incumbent) OR a previously-
  passing task regresses with the agent visibly over-verifying into the step
  cap, revert to the 5-line prompt.
