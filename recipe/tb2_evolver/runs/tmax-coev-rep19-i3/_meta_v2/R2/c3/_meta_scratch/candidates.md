# Candidates — R2 c3

Assigned focus: `task_000111_cbada64a` (scientific_computing) fails.

## Diagnosis

`task_000111` asks for a C++ OLS regression + bootstrap CI on a fixed CSV.
The agent's OLS math is textbook-correct and self-consistent, it ran once,
got `m=2.5056`, verified only that `/home/user/result.txt` exists and is
formatted, and exited in 8 steps (`exit_reason=done`, `finished=no_tool_calls`).
The grader expected `m=2.5997`. A ~3.7% slope gap on deterministic OLS is not
float noise — it is a **specification-convention deviation** the agent never
audited.

This is NOT idiosyncratic. **All 7 scientific_computing tasks in the batch
scored 0**, and the verifier tails show the *same mechanism* — a
self-consistent implementation that is off by a spec convention, committed
after a single run:

- `task_000111` — OLS slope 2.5056 vs 2.5997.
- `task_001330` — spec says "regress **x on y** (not y on x)", seed 42, exact
  frame/brightest-pixel tie-break; agent got m=0.048 vs 0.050. Grader message:
  "Ensure you used numpy.random.seed(42) and the correct parameters."
- `task_001048` — chunk integral 165.79 vs 161.80 (integration endpoint/bin
  convention).
- `task_000017` — primer `GCTAGCGCGCTAGCT` vs `AGCTAGCGCGCTAGC` (off-by-one
  window offset — the string is shifted one char).
- `task_001035` — optimal primer `GCGG` vs `GCAT` (optimization convention).
- `task_001937` — Optimal Grid 60 vs 50 (search/stopping convention).

The failure mechanism is **convention misread**, not arithmetic error. R1
already ships `NumericCrossCheckNudge`, but its content over-weights
"re-derive by a SECOND INDEPENDENT METHOD and compare" — which is *weak*
against convention bugs: two independent methods that share the same misread
convention (same off-by-one, same seed order, same axis choice) agree with
each other and both stay wrong. The nudge needs to lead with an *adversarial
line-by-line convention audit against the spec text*, treating "close but not
exact" as a failure signal, before falling back to the second-method check.

## Candidate C-010
[lens: failure | lever: control | intent: corrective]

Supersede `NumericCrossCheckNudge` with `SpecConventionAuditNudge`: same
one-shot exit-intent firing mechanism, but content reoriented to force an
adversarial re-read of every value-changing spec convention (axis/order,
seed & RNG-call order, indexing/off-by-one, endpoint/boundary, rounding,
units, sample size) and to treat a "close-but-not-exact" number as a
convention-mismatch signal — keeping the magnitude sanity-check and the
second-method reconciliation as secondary steps.

- Tasks affected: task_000111_cbada64a, task_001330_f5aff1f5,
  task_001048_14335141, task_000017_fed73abc, task_001035_26564093,
  task_001937_ac874115 (whole scientific_computing cluster, all reward 0).
- Signal: `domain=scientific_computing`, `reward=0`, `exit_reason=done`,
  short/medium step counts (7-20 for 5 of 7); verifier tails show
  self-consistent-but-off-by-a-convention values, not crashes or format
  errors.
- Verified (body-quoted):
  - task_000111 step 6 (assistant): "The task is complete ... The output is:
    `2.5056,1.2262,3.9742,6.2925`" — committed after a single run, only
    file/format re-checked, never re-read the percentile-index / RNG-call
    conventions against the produced number.
  - task_001330 spec quotes "you must regress $x$ on $y$ ($x = my + c$)" and
    "numpy.random.seed(42)"; final assistant: "The task is complete ...
    `m=0.048, c=40.237`" — committed with no convention audit; grader message
    explicitly blames seed/parameter convention.
- Why Control not Instruction: the guidance must fire **at the decisive
  pre-exit moment** and only once, keyed on the same exit-intent signal
  `CustomSelfVerifyProcessor` uses — a static system-prompt rule would be
  read at step 0, before the agent has any number to audit, and drowned out
  by the task text. This is a mechanical, uniformly-fired pre-commit hook,
  which is exactly the Control lever's shape. It also must *replace* the
  weaker R1 nudge in the same pipeline slot to avoid double-injecting two
  competing pre-exit reminders.
- Why not Configuration: R1's nudge is not merely mis-tuned by a kwarg — its
  *message content* points the agent at the wrong remedy (second method)
  for the dominant bug class (convention misread). Fixing content requires a
  new processor body, not a knob.
- Retroactive check (A-corrective): partial-yes. For the convention-explicit
  tasks (task_001330 x-on-y/seed, task_000017 off-by-one, task_001048
  endpoint) an adversarial re-read at the pre-exit moment plausibly surfaces
  the exact deviation the agent had all the spec text to catch. For
  task_000111 the win is less certain (its convention deviation is subtler),
  so this is scoped as a cluster-level nudge, not a guaranteed single-task
  flip. No task-specific knowledge is embedded — pure general strategy.
- expected_global_gain: targets a 7-task all-zero cluster with a single
  recurring mechanism; even a modest hit-rate flips more than one task and
  generalizes to any exact-computed-value deliverable.
- regression_risk: Low. Replaces (not adds to) the R1 nudge, so no
  double-injection. Fires at most once, only on an exit-intent turn, appends
  one user message, emits no tool call — cannot interfere with the
  self-verify keepalive or with non-computational tasks (agent ignores it if
  irrelevant). No previously-passing cluster depends on the old nudge wording
  (R1 nudge attribution is still pending, no confirmed passes attributed).
- cost_shift: Negligible-to-slightly-up. One extra user message on the
  pre-exit turn of tasks that reach exit-intent; may add a few verification
  steps on computational tasks (intended — buys correctness). No effect on
  runs that hit the step cap or never reach exit-intent.
