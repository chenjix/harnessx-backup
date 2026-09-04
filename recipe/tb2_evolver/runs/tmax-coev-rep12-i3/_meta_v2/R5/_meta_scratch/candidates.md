# R5 Candidates

## Candidate C-006 — Hard-blocking identical-command loop breaker

- **Three-axis tag**: lens=failure-mode / lever=control / intent=fix-broken-mechanism
- **Hypothesis id**: h_repeat_command_blocker_v1

### Signal

R4 = 13/50. No `exit_reason=error` remain (R1 fix holds). The dominant
remaining failure family is `budget_exceeded` at exactly 80 steps (14/37
failures). Command-frequency analysis of those trajectories shows the agent
issuing a **byte-identical Bash command** many times in a row and burning the
whole budget:

- `task_001214_f44c0aa2`: `pycdc --decompile /home/user/auth.pyc` issued
  **24 consecutive identical** times, every result the same `usage: pycdc ...`
  error.
- `task_000908_170e5e4e`: `cat > /home/user/graph_analytics.py << EOF ...`
  (same file, same content) **28 consecutive identical** times, every result
  `(exit 0, no output captured)`; the loop hits 4x at step 8 of 80 —
  ~72 steps wasted.
- `task_000506_c13429e7`: identical `printf ... | solver` **9 consecutive**.
- `task_001044_45c70cf1`: `objdump -d /app/perf_oracle` cluster, 4+ consecutive.

### Verified body evidence (root cause: the R2 breaker is a silent no-op)

The R2 `RepeatCommandBreakerProcessor` injected a redirect **user** message in
`on_before_model`. Verified against `harnessx/core/runloop.py`:
- The model input is built from `state.raw_messages` each step (runloop
  line ~304/375); `on_before_model` edits to `event.messages` are passed to
  `provider.complete()` transiently but are **never written back to state**, so
  the nudge disappears the next step.
- `grep "loop detected by the harness"` across **all 50** R4 message logs =
  **0 matches**. The nudge left zero persisted trace and produced zero
  observable behaviour change; looping tasks still ran to `budget_exceeded`.

The fix uses the run loop's supported interception path (verified in
runloop.py lines 600-629): a processor that sets `approved=False` +
`synthetic_result` in `on_before_tool` causes the loop to (a) skip execution of
the useless command and (b) `state.add_raw_message(...)` the synthetic_result
as the tool result — so the corrective text **persists** in context and
compounds. The model receives the block as the command's own output.

### Retroactive check (variant: counterfactual-would-fire + regression-safety)

For every task, computed the max run of **consecutive non-empty identical**
single-Bash commands:

- Tasks reaching >=4: `task_001044` (4), `task_000506` (9),
  `task_001214` (24), `task_000908` (28) — **all four are FAILURES**.
- **Every currently-passing task has max <= 3.** The two passing tasks with
  huge raw repeat counts (`task_000903` x21, `task_001537` x26) repeat the
  **empty** command (self-verify marker turns), which the processor's
  normalization excludes (returns None) — so they are untouched.

⇒ At `block_threshold=4` the guard fires only on stuck failing tasks and
**cannot fire on any currently-passing trajectory**. This is the counterfactual
evidence that the mechanism activates exactly where it should.

### Why control (not instruction / action / configuration)

- **Not instruction**: R2 already tried a prompt-shaped nudge; the model
  narrates "I keep repeating" yet cannot self-break — a mechanical guard is
  required, and the transient nudge provably did nothing.
- **Not action**: the only tool is `Bash` (hard benchmark limit); cannot add a
  tool.
- **Not configuration**: no existing knob detects command-level repetition
  (LengthTruncationRecovery keys on `finish_reason=length`, which these
  well-formed tool calls never set).
- **Control** (a processor that intercepts the tool call) is the only lever
  that can both stop the wasted execution and persist a corrective signal.

### Tasks affected (intent=fix-broken-mechanism)

`predicted_affected`: task_001214_f44c0aa2, task_000908_170e5e4e,
task_000506_c13429e7, task_001044_45c70cf1 (the 4 with >=4 consecutive
identical non-empty commands). These convert from "budget burned in a loop"
into a fair attempt with most of the step budget intact.

### Pareto statement

- **expected_global_gain**: frees ~40-72 steps on 4 loop-bound failing tasks
  (2 exit budget_exceeded, 2 exit done-but-wrong after looping) so the model
  gets a real attempt at the actual task; generalizes to any future task that
  falls into a byte-identical command loop.
- **regression_risk**: near-zero — no currently-passing task reaches 4
  consecutive non-empty identical commands; empty self-verify loops are
  excluded by normalization; a genuinely different next command re-arms the
  guard to silence. Worst case a legitimate 4th identical retry is blocked with
  a corrective message (still recoverable — the model can re-issue a *modified*
  command immediately).
- **cost_shift**: net **down** — every blocked call removes one real Bash
  execution round-trip and truncates ~20+ wasted model turns per looping task;
  synthetic_result text is a few hundred tokens.
- **rollback_trigger**: if R6 shows any previously-passing task regressing
  (T→F) with a "BLOCKED BY HARNESS" marker in its trace disrupting legitimate
  iteration, revert to the R4 config (drop this processor; do NOT restore the
  proven-dead R2 breaker).
