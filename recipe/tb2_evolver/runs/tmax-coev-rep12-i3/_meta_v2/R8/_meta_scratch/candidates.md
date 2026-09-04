# R8 Candidates

## Candidate C-008 — durably break identical-Bash-command loops

- **Three-axis tag:** lens=history-window / lever=control / intent=close-failure-cluster
- **Hypothesis id:** `h_identical_command_loop_compactor_v1`
- **Predicted affected:** [task_000730_265f23f6, task_001028_5bc8bc70]
  (loop is present-but-below-threshold on task_001214_f44c0aa2 x6, left untouched by design)

### Signal

R7 draw = 13/50. No `exit_reason=error` remain. The `budget_exceeded`
family is still the largest failure bucket (16/37 failures hit the full
80-step budget). Within it, a subcluster burns the entire budget on ONE
byte-identical Bash command repeated turn after turn:

- `task_000730_265f23f6`: `tesseract /app/bug_report.png stdout 2>&1`
  issued **31 consecutive identical times**; exit `budget_exceeded`.
- `task_001028_5bc8bc70`: `strings /home/user/log_analyzer` issued **19
  consecutive identical times**; exit `budget_exceeded`.

### Verified body evidence

- `_meta_scratch/dupscan.py` (max consecutive identical NON-EMPTY single-Bash
  run per task, run over all 50 R7 traces):
  - Failing loopers: task_000730 = **31**, task_001028 = **19**.
  - Highest PASSING task: task_001537_fbfffb79 = **7** (a capstone-disasm task
    whose identical heredoc re-emits are caused by max_tokens truncation
    `continue` nudges; it goes on to PASS). Next passers: task_000472,
    task_000628, task_001267 = 3 or below.
- `_meta_scratch/probe.py task_000730` / `task_001028`: confirms the repeated
  command is byte-identical AND that **no** "loop detected by the harness"
  redirect appears in either trace — proving the R2 RepeatCommandBreaker
  (registered, order 6) is a silent no-op (its `on_before_model` edit never
  lands in `state.raw_messages`, matching the R5 journal diagnosis).
- `harnessx/core/runloop.py` lines 345-368: an `on_step_start` processor that
  mutates `event.messages` (changing `history_hash`) triggers an auto
  SegmentBoundary that writes the trimmed window into BOTH `state.raw_messages`
  and `state.messages` — the durable-persistence path the R7
  TruncationLoopCompactor already relies on. My processor reuses it.

### Retroactive check (variant: threshold-safety on the passing set)

The processor fires only at `min_run >= 10` consecutive identical-command
turns. The passing set's maximum identical non-empty Bash run is **7**
(task_001537). 10 > 7 with a 3-turn margin, so **the collapse provably cannot
fire on any currently-passing R7 trajectory**. It fires on exactly the two
runaway loopers (19, 31). Contract validator: 0 violations (collapse is a net
message *reduction* ending on a single user directive — same shape as the
already-passing R7 TruncationLoopCompactor).

### Why control (this shape) not the alternatives

- **Not instruction:** the model already narrates "I've been stuck in a loop
  running the same command" (task_001028 last turn) yet cannot self-break — a
  prompt rule is what it is already failing to self-enforce.
- **Not the R2 breaker (kept as-is):** proven ephemeral no-op; the fix is to
  persist via `on_step_start`, not to re-tune R2.
- **Not the reverted R5 blocker (`h_repeat_command_blocker_v1`):** that
  intercepted `on_before_tool` with `approved=False`, BLOCKING execution, which
  disrupted legitimate iteration and caused a net regression
  (lost task_000097, task_000472, task_001267). This candidate is a **distinct
  mechanism and distinct hypothesis id**: it never blocks a tool call (the
  commands already ran), only trims already-produced redundant history and adds
  one directive; and it fires at a much higher, passing-set-safe threshold (10
  vs R5's 4). Not a re-proposal of the reverted bet.

### Pareto framing

- `expected_global_gain`: recovers ~20-30 wasted steps each on the
  identical-command loop subcluster (2 tasks now, generalizes to any future
  task that falls into a byte-identical Bash loop), converting a
  guaranteed budget-loss 0 into a fair scored attempt at lower cost.
- `regression_risk`: near-zero — threshold 10 is provably above every passing
  task's identical-run length (max 7); never blocks execution; contract-clean;
  collapse only removes redundant duplicate turns the model itself produced.
- `cost_shift`: net DOWN — collapsing the repetition wall shrinks the assembled
  prompt and the directive breaks the loop early, so looping tasks stop burning
  the full 80-step budget.
- `rollback_trigger`: if R9 shows any previously-passing task regressing T->F
  with a "[harness: your previous N turns issued the SAME command" note in its
  trace disrupting legitimate iteration, or pass_rate drops vs the R7 incumbent
  mean, drop the IdenticalCommandLoopCompactor registration and keep the R7
  pipeline.
