# Candidates — R5

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `SelfDeclaredThrashGuard` MultiHookProcessor that detects the model's
own repeated self-stuck narration ("stuck in a loop", "repeating the same",
"let me try a different approach") and injects an escalating structured-
diagnosis → stop-and-commit nudge, breaking the semantic thrash loop that
burns the entire step budget without changing strategy.

- Tasks affected (corrective, failing cluster, same mechanism):
  task_000348_31fb8c8a, task_000470_f819ab03, task_000669_0ef2d04d,
  task_000891_aa1685c7 (and partially task_000796_828a72cf).
- Signal: assistant-content self-stuck narration phrase counts on R4
  trajectories — 30/28/28/22 occurrences in the four failing tasks above;
  these tasks run 331-1131s to the step ceiling (`elapsed_s` 887/1131/331/712)
  without reaching exit-intent. The SAME phrases appear **0×** in every
  passing task sampled (task_000916, task_000987, task_001968, task_001400,
  task_000840). Raw "max consecutive identical tool call" is NOT a usable
  signal (18-26 in passing tasks too — this is why R1's command-keyed guard
  regressed and was reverted); the model's own stuck-declaration is.
- Verified (Read of .messages.json bodies):
  - task_000470_f819ab03: assistant turns repeat "I keep repeating the same
    approach ... let me try a different approach" then re-run a byte-identical
    probe of the metrics extractor; 33 tool calls, 20 of them identical,
    verifier = ConnectionRefused (service never listening).
  - task_000348_31fb8c8a: 30 "stuck in a loop" turns, 33 identical tool calls,
    converged_gamma returned None to the verifier.
  - task_000669_0ef2d04d: 28 "I keep repeating"/"different approach" turns,
    27 identical tool calls, evil-corpus dir left empty.
  - task_000891_aa1685c7: 22 "stuck in a loop" + 39 "different approach"
    turns; all_scores.csv never written.
  - Passing controls confirmed clean: task_000916/000987/001968/001400/000840
    each show 0 self-stuck phrases despite long runtimes and repeated commands.
- Why Control not Instruction: R4 already ships a strong workflow-discipline
  system prompt (instruction lever) that tells the agent to diagnose before
  retrying and stop looping — yet the model *still* narrates its stuckness
  20-33× and keeps going. A static prompt rule the model has already read and
  is visibly violating cannot fix a runtime behavioural loop; only a runtime
  hook that fires *at the moment the loop is detected* and forces a
  differently-shaped next action / clean exit can. Control also lets the guard
  fire uniformly across every task on a behavioural shape, which a per-call
  tool cannot express.
- Why Control not the reverted R1 guard: different mechanism at the same
  lever (analyze skill explicitly permits this with new evidence). R1 keyed on
  raw identical (command,output) signatures — a shape that ALSO occurs in
  passing long tasks (verified: 18-26 identical tool calls in 5 passing tasks),
  producing regression risk and only appended weak advisory text. C-001 keys
  on the model's self-declared stuckness (0× in passing tasks) and escalates
  to a forced structured diagnosis, then a stop-and-commit-and-exit directive
  that converts wasted budget into either a recovery attempt or a clean early
  exit (which also lets the order-95 verifier-dep-guard fire on exit-intent).
- Retroactive check (A-corrective): partial-yes. For the near-miss members
  (a correct or partial solution is close, e.g. the service just needs a
  different startup path, the output file just needs to be written), forcing a
  differently-shaped hypothesis or a stop-and-commit gives a real flip chance
  the R4 config never got because the run died at the step ceiling mid-thrash.
  For pure capability gaps (000348 gamma algorithm wrong) the guard frees the
  wasted 500-900s of budget and yields a clean exit even if it does not flip —
  a strict Pareto improvement on cost with ~0 regression risk.

- expected_global_gain: Attacks the self-declared-thrash failure cluster
  (4-5 tasks, the dominant budget sink at 331-1131s each). Generalizes to any
  future task where a 9B model recognizes it is looping but lacks an escape
  hatch — a model-behavioural shape, not a task property.
- regression_risk: Near-zero on the passing set. The trigger phrase family is
  absent (0×) from all sampled passing trajectories, and the guard only
  appends/replaces a single trailing user message (never blocks a tool call,
  never touches tool_calls or the system prompt). Worst case on a passing task
  that happens to say "let me try a different approach" twice: one extra
  diagnosis nudge, no state mutation. `diagnose_threshold=2` prevents a single
  incidental phrase from tripping it.
- cost_shift: Strongly negative (cost-saving). The targeted cluster currently
  burns 331-1131s to the step ceiling; a stop-and-commit nudge at
  `exit_threshold=4` cuts those runs short. Only additive cost is a ~1-2
  sentence nudge on the rare turns that trip the guard.
- rollback_trigger: If R6 pass_rate does not improve AND any previously-
  passing task regresses T->F, or median elapsed_s on the thrash cluster does
  not drop, revert the processor.
