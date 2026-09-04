# R5 Candidates

## Candidate C-001

**Three-axis tag**: lens=failure-cluster / lever=control / intent=corrective

**Schema**: New `MultiHookProcessor` `NoProgressRepeatBreaker`, registered in
`config.yaml` after `CustomEditToolProcessor` (`_order=32`, singleton group
`noprogress_repeat_breaker`). Params: `soft_threshold=4`, `hard_threshold=6`,
`timeout_block_threshold=3`.

**Mechanism**: Track consecutive identical (normalized command, normalized
result) Bash pairs per task.
- `on_before_tool` stashes the normalized command by `tool_call_id`; if the
  same command has already reached `hard_threshold` identical-result repeats OR
  `timeout_block_threshold` consecutive `exit 124` timeouts, it is refused
  (`approved=False` + provider-safe `synthetic_result`) instead of executed.
- `on_after_tool` pairs the result with its command, maintains the streak and
  timeout-streak counters (using `_norm_result` to strip sibling-processor
  advisory suffixes so an intermittent `[EditDetection]` nudge cannot break the
  identical-result match), and appends a one-shot soft redirect nudge at
  `soft_threshold`.
- Any change in command OR in underlying output resets the streak; a different
  command instantly disarms the block, so a run can never be permanently wedged.

**Retroactive-check (corrective variant)**: If this processor had been active in
R4, would the predicted-affected failing tasks have been redirected off their
zero-progress loop and reclaimed step/wall-clock budget, while every passing
task remained untouched?

**Tasks affected (corrective intent → failing tasks that thrash)**:
- Simulated over all R4 trajectories: breaker fires (nudge and/or block) on
  `task_000015`, `task_000118`, `task_000185`, `task_000358`, `task_000408`,
  `task_000686`, `task_001017`, `task_001035`, `task_001229` — **all
  reward=0**. **Zero passing tasks touched** (required safety property).
- Block counts confirm the death-spiral cluster: 000118 (20 blocks, done-exit
  loop), 000358 (18, budget_exceeded), 000408 (15), 000686 (24, done-exit),
  001035 (15), 001017 (9, the `error`-exit + 3830s wall-clock timeout thrash).

**Evidence**:
- No R4 *passing* task exceeds 3 consecutive identical (cmd,result) pairs
  (max passer = task_000020 at 3). Failing budget_exceeded tasks reach
  5/6/10/19/24 — thresholds (soft=4/hard=6) sit strictly above the passing
  ceiling.
- `task_001017_9f6adf16`: `exit_reason=error`, ~3830s wall-clock, 15 consecutive
  `(exit 124, ...)` timeouts of a brute-force compile/run that the R4 nudge-only
  guards never stopped → timeout-streak block at 3 caps this at 9 refusals.

**Distinct from reverted R1 `h_repeated_command_guard_v1`**: R1 counted *global*
occurrences of a command string (swept benign echo-spam, regressed passers).
C-001 counts *consecutive identical (cmd,result) pairs* and blocks only above the
empirical passing ceiling → zero-passer-impact in simulation.

**Pareto**: Global gain = reclaim budget on a ~6-task death-spiral cluster + cap
the worst wall-clock/cost outlier (001017). Regression risk = low; blocks are
scoped to the exact repeated command and disarm on any different command; sim
shows 0 passers touched. Cost shift = strongly negative (fewer wasted steps,
caps the 3830s outlier).

**Rollback trigger**: If R5 pass_rate drops vs R4 (17/50) OR any previously
passing task regresses to F with the breaker firing on it, revert.
