# Candidates — R2

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Wire the in-tree `LoopDetectionProcessor` into the pipeline with a warn-early /
raise-high configuration to break degenerate identical-tool-call loops that R1
did not close — warn at 3 consecutive identical calls, hard-raise
`LoopDetectedError` at 10 (safely above the one passing task's run of 7).

- Tasks affected (corrective, all FAIL, degenerate loop is the budget blocker):
  - task_000264_ab8c7253 — data_querying — 80 steps, budget_exceeded, maxrun 26
  - task_000958_4bb2b05d — data_querying — 80 steps, budget_exceeded, maxrun 26
  - task_001090_c61c71f2 — system_administration — 80 steps, budget_exceeded, maxrun 26
  - task_001089_220cc46b — debugging — 70 steps, done/no_tool_calls, maxrun 26
  - task_001031_a8f0eb37 — scientific_computing — 71 steps, done, maxrun 16, 1830s
  - task_000015_89886d8d — software_engineering — 80 steps, budget_exceeded, maxrun 20
- Signal: `maxrun` (longest run of *identical consecutive* tool-call argument
  strings) cleanly separates the loop-failure cluster (16-26) from the entire
  passing cluster (all <=7). Frontmatter: `exit_reason=budget_exceeded` on 4 of
  these; the other two grind to `done` at 70-71 steps with reward 0. The harness
  currently has NO loop-detection processor in the pipeline (checked
  `config.yaml`), so `exit_reason=loop_detected` never fires — these loops
  consume the full step/wall budget instead of terminating early.
- Verified (Read of `.messages.json` bodies):
  - task_000264: msgs 2-49 are ~26 verbatim repeats of
    `sqlite3 /home/user/company.db ".indexes employees" > /t...` each returning
    the identical 28-char output. Pure degenerate loop.
  - task_000958: 26 identical consecutive tool calls at the tail; 80 steps.
  - task_001090: run of 26 identical `cat > monitor.go` rewrites interleaved
    with the same failing `go build`.
  - task_001089: longest identical run 26 (empty-arg `{}` calls).
  - task_001031: longest identical run 16; 1830s wall-clock — the single most
    expensive task in the round.
  - task_000015: 20 identical narration/tool cycles on the same URL-encoding
    debug; also the R1 length-recovery collapse fires but the runloop's
    persistent "continue" nudge re-primes the loop, so it never breaks on its own.
- Why Control (wire existing processor) not Action/Instruction: the failure is a
  mechanical control-loop misfire — the runloop keeps re-invoking the model on an
  identical state. An `@tool` cannot express a cross-turn guard; an Instruction
  ("don't repeat yourself") is exactly what the model already fails to self-apply
  under this small-model degeneracy. The correct lever is a mechanical
  cross-step guard that (a) injects a recovery warning at 3 repeats and
  (b) hard-terminates at 10 so budget is preserved for `_recover_best_output`.
  Using the already-tested in-tree `LoopDetectionProcessor` avoids authoring
  bespoke code and keeps the trigger purely structural (fingerprint of
  name+inputs) — benchmark-agnostic.
- Why not just tune an existing knob: there is no existing loop-detection
  component in the pipeline to tune; this adds the missing mechanical guard.
  Threshold choice (raise=10) is set by evidence, not default (default raise=5
  would clip the passing task_001701 whose run=7).
- Retroactive check (A-corrective): partial-yes. Hard termination alone does not
  hand these tasks a correct answer, but two mechanisms help: (1) the warn at 3
  ("you are stuck in a loop — try something fundamentally different") is injected
  into the tool result *before* the loop consumes the budget, giving the agent
  ~60 recovered steps to attempt a different approach (the loops start early:
  task_000264's loop begins at step ~2, task_000015's at step ~4); (2) on the
  tasks that do exhaust budget, early loop_detected termination + best-output
  recovery is strictly better than 80 wasted steps. The net-positive claim rests
  on the recovery nudge, not the raise. Grade `yes` for "removes the blocker that
  consumes the budget", with the caveat noted in Uncertainty.

- expected_global_gain: Attacks the dominant *remaining* failure shape after R1
  (degenerate identical-call loops), 6 failing tasks across 4 domains. The warn
  nudge is the flip mechanism; the raise is a budget/cost safety net. Generalizes
  because the trigger is a pure structural fingerprint, not task content.
- regression_risk: One passing task (task_001701_95e3bbcb) has a run of 7
  identical `jshon` calls and still passed at 78 steps. raise_threshold=10 sits
  above it, so it will only receive the harmless warn-at-3 nudge, not a
  termination. name_warn_threshold=8 is warn-only (never raises). Compaction-aware
  reset prevents stale fingerprints. Residual risk: the warn nudge slightly
  perturbs an otherwise-passing long task's context; mitigated by warn text being
  advisory and appended only to a tool result.
- cost_shift: Strongly negative (savings). Six tasks currently burn 70-80 steps /
  up to 1830s in loops; hard-raise at 10 caps them near ~10 identical repeats and
  triggers best-output recovery, collapsing the most expensive tail of the round.
