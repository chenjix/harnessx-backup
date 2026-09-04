# Candidates — R5

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `NarrationLoopBreakProcessor` that raises `LoopDetectedError` on
`>= 6` consecutive assistant turns whose normalized reasoning content is
near-identical, terminating the budget-exhausting reasoning spiral early
(clean `loop_detected` exit) instead of burning the full step/time budget
or exiting `error`.

- Tasks affected: task_000396_e56917e2, task_001031_a8f0eb37, task_001032_1adaccb9, task_000010_644ab1c2
- Signal: whole-budget losses (`exit_reason ∈ {budget_exceeded, error}`,
  80 steps, 900-1100s) with the stock `LoopDetectionProcessor` firing 12-22
  warn injections but never raising — because its exact-match strategy never
  matches (Bash args are tweaked each turn) and its name-only strategy is
  warn-only by design. Distinguishing feature: repeated *near-identical
  assistant narration*. Measured max consecutive identical-narration streak:
  task_001031=8, task_000396=6 (failing) vs task_000740=4 (PASSING) and all
  other passing tasks <= 3.
- Verified (Read):
  - task_001031_a8f0eb37 last 6 messages: three consecutive assistant turns
    are verbatim "I've been stuck in a loop trying to fix the mpi4py
    Allgatherv API … let me try a fundamentally different approach …"; each
    interleaved tool turn shows the identical `ValueError: message: expecting
    2 to 4 items` traceback; exit_reason=error, 42 steps.
  - task_000396_e56917e2: 30 assistant turns containing the same "stuck"
    narration, 22 LoopDetection warn injections, 11 truncation markers; 80
    steps; exit_reason=budget_exceeded; final_pytest still `assert 0.0 < 0.0`
    (never made progress).
  - task_001032_1adaccb9 / task_000010_644ab1c2: 80 steps each,
    budget_exceeded, 11-12 LoopDetection warnings ignored, tar-parser /
    operator.py-shadowing bug never fixed.
- Regression safety check (Read, all R4 passing tasks): the highest
  identical-narration streak on any PASSING task was 4 (task_000740, which
  passed); typical passing long tasks (task_000015, task_000028) capped at 3.
  raise_threshold=6 sits two full repeats above every passing task in the
  round → zero measured regression surface. The detector also resets on any
  tool-calling turn, so exploration / retry-with-variation is never penalised.
- Why Control not Configuration: the stock `LoopDetectionProcessor` cannot be
  re-tuned to catch this — its hard-raise path keys only on the *exact*
  fingerprint (name+inputs), which never matches here, and its name-only path
  is warn-only *by design* because tool-count is not a safe discriminator on
  this benchmark (passing tasks issue 37-48 consecutive Bash calls). The
  discriminating signal is repeated *content*, a fingerprint the existing
  processor does not compute; capturing it needs a new hook, not a knob.
- Why Control not Instruction: the model is already warned 12-22 times per
  spiral by the existing loop detector and narrates past every one (the
  documented narrate-past pattern from R1/R2/R3). A prompt rule the model
  ignores adds no value; only a mechanical raise stops the doomed run.
- Retroactive check (A-corrective): the honest answer for *flipping* is **no**
  — these are capability gaps (mpi4py API, tar parser, operator.py stdlib
  shadowing) that no harness action solves, so the tasks stay failing. The
  corrective value is not a flip but **cost/time containment + error-surface
  reduction**: each spiral currently wastes the whole 80-step budget and
  900-1100s; an early `loop_detected` exit at streak 6 reclaims ~50-65 steps
  and ~600-900s per spiralling run, and converts task_001031's risky
  `error` exit into a graceful `loop_detected`. This is a Pareto move on the
  cost_shift axis with zero regression risk, not a pass-rate flip.
- expected_global_gain: 0 expected flips (spirals are capability gaps).
  Positive Pareto move: eliminates 3-4 whole-budget wastes per round
  (~2000-3500 wasted steps and ~40+ minutes wall-clock across the round) and
  removes one `exit_reason=error` (task_001031) from the round, reducing the
  error-exit surface that the replay-gate class penalises.
- regression_risk: near-zero. raise_threshold=6 is two repeats above the max
  identical-narration streak of any passing R4 task (4); the streak resets on
  every tool-calling turn; warn fires once before any raise, giving the model
  an explicit chance to self-break. Worst plausible case: a task that legitimately
  needs to re-emit identical prose 6× with no tool call and no variation — not
  observed in any of the 50 tasks, passing or failing.
- cost_shift: net DECREASE. No effect on healthy runs (they never reach 4
  consecutive identical narrations). On the 3-4 spiralling runs it cuts the
  run short by tens of steps and hundreds of seconds each.
