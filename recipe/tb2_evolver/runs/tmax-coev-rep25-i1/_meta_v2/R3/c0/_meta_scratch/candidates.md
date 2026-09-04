# Candidates — R3/c0 (assigned focus: recovery from tool errors)

## Candidate C-001 — recurring hard-error recovery guard (on best lineage)

- **Three-axis tag:** lens=failure-recovery / lever=control / intent=close-failing-cluster
- **Base:** best-scoring live lineage `n1_06551c3b` / `n2_b44cc6da` (0.580),
  which is R0 + `StuckReasoningRecoveryProcessor`. Current_config is R0 (0.500),
  so adopting this base is itself an evidence-backed +0.08 already demonstrated.
  The new mechanism is added on top.

### Reusable failure class (observable, task-agnostic)

The agent keeps hitting the **same underlying hard error** (Python/compiler
traceback, `parse error`, `*Error:` line, DB `OperationalError`) but **varies
the surrounding command each attempt** — recompile, edit, re-invoke with
slightly different args. Because no two consecutive turns are byte-identical,
every existing guard structurally misses it:
- `LengthTruncationRecoveryProcessor` → only `finish_reason=length`.
- `StuckReasoningRecoveryProcessor` → only *consecutive identical* narration
  (observed max consecutive run on the thrash tasks is 3-4, far below its
  threshold 12).
- `CustomEditToolProcessor` → write-count, ignores exit status.

The observable state is: **one normalised hard-error fingerprint recurs
`recur_threshold` times across the whole session** while the agent makes no
progress and eventually hits `budget_exceeded` / `exit=error`.

### Signal (verified body evidence)

Anchor `task_000010_644ab1c2` is a **1-task shape** (a self-created
`operator.py` shadows the stdlib `operator`, breaking the interpreter; its
"recovery" renamed the required file away then back, re-poisoning the final
state). The shadow-module signature appears in **exactly one** trajectory, so
per the generalization contract it earns a no-op, not a special case. Tracing
the anchor's *observable* class ("hit an error; recovery did not change the
outcome") to the wider set surfaced the real, domain-diverse cluster:

- `task_001701_95e3bbcb` (security, budget_exceeded): `parse error: type
  'object' is not simple/printable` recurs **10×** across msgs 3–69 with varied
  jshon/make/edit commands between; agent never changes model of the problem.
- `task_001818_b251e5ea` (data_processing, exit=error): `error: could not
  compile ticket_processor` (rust) recurs **9×** across recompile cycles.

Both key on the recurring *error signature*, not the command — the R2
`(command,error)` fingerprint and the consecutive-narration guard both miss
them.

### Retroactive check (which tasks flip / stay)

Reconstructed the processor's exact `_fingerprint` counting over all 50 R0
trajectories:
- **Would FIRE (recur≥5):** only `task_001701` (10) and `task_001818` (9) —
  both currently reward=0. No other task reaches 5.
- **Passing-task guard:** max recurring hard-error count on ANY passing task is
  **3**; zero passers reach even 4. `recur_threshold=5` sits strictly above the
  passing ceiling and below the failing cluster → the guard cannot fire on the
  observed passing set. Benign lines (`no output captured` successes, `warning`,
  one-off `command not found` probes, self-verify probes) are excluded so the
  high-recurrence PASS false-positives (task_001652 13× benign, task_000344 7×
  self-verify probe, task_001591 5× intentional JSONDecodeError handling) do NOT
  count.

### Why control, not instruction/configuration

Instruction (system-prompt) can't observe runtime error recurrence and would
add tokens to all 50 tasks for a 2-task signal. Configuration (tuning existing
knobs) can't express "same error fingerprint across non-consecutive turns" — no
existing processor tracks it. Only a `control` processor hooking `on_after_tool`
can detect the state and inject a bounded, legible redirect.

### Pareto statement

- **expected_global_gain:** the error-thrash-with-variation cluster (`budget_exceeded`
  / `exit=error` fails whose identical-consecutive run is too low for the
  reasoning guard). Generalises across domains (rust build loops, JSON-parse
  loops, DB-query loops) because it keys on a domain-independent recurring-error
  fingerprint. Adopting the 0.580 base also captures the already-proven
  reasoning-loop gains vs the R0 (0.500) current_config.
- **regression_risk:** advisory user message only, never blocks/rewrites tool
  calls, `max_nudges=2`, per-fingerprint cooldown, `recur_threshold=5` above the
  passing ceiling (3). Worst case: 1–2 extra short messages on a task that hits
  the same hard error ≥5× and was about to self-correct. `on_before_model`
  replaces a trailing user message rather than +2-inserting (contract-clean).
- **cost_shift:** net token DECREASE on the thrash cluster (cuts ~15–30 wasted
  retry cycles on 2+ tasks); +≤2 short messages only on tasks that thrash ≥5×;
  zero change on every passing task (guard never fires there).
- **rollback_trigger:** if the next round's pass_rate is flat/down AND the
  recurring-hard-error count on `task_001701` / `task_001818` does not fall vs
  R0, revert this processor (keep the 0.580 reasoning-guard base).
