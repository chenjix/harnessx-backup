# Candidates — R2/c3

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `NumericCrossCheckProcessor`: on exit-intent for tasks that ran a
deterministic numeric computation AND wrote a numeric result to an output
file, inject one task-agnostic nudge to independently re-derive the key
quantity a second way and reconcile discrepancies before finishing.

- Tasks affected (assigned focus + recurring cluster):
  - `task_000111_cbada64a` (scientific_computing) — assigned focus
  - `task_001653_c4cafa73` (data_science)
  - `task_000587_9862bb19` (data_science)
- Signal: `exit_reason=done` with a *small* step count on numeric-output
  tasks, and `final_pytest` failing on a WRONG NUMBER (not a missing file,
  not a crash): the committed value disagrees with the grader's expected
  value by an amount far larger than any float tolerance.
- Verified (Read of trajectories / result.json):
  - `task_000111` messages.json: agent writes textbook-correct OLS C++
    (`m = (N*sum_xy - sum_x*sum_y)/(N*sum_x2 - sum_x*sum_x)`), runs it
    ONCE → `2.5056,1.2262,3.9742,6.2925`, then step 5 body
    "The program compiled and ran successfully" and step 7 body
    "The task is complete" — no independent recomputation. `final_pytest`:
    `AssertionError: Expected m to be approx 2.5997, got 2.5056`
    (`abs(2.5056 - 2.5997)=0.0941 <= 0.001` fails). The self-verify turn
    only re-read the task and re-`ls`/`cat`-ed files.
  - `task_001653` result.json `final_pytest`:
    `- Centroid: 42.0095, 45.9009, 43.0685 / + Centroid: 37.3120, 47.0664,
    38.0278` and `- Distance: 11.3444 / + Distance: 17.6896` — every
    computed number wrong, committed, exit_reason=done in 10 steps.
  - `task_000587` result.json `final_pytest`: `test_csv_parser_fixed`
    AssertionError on the produced numeric parse (NaN handling) — wrong
    numeric output committed, exit_reason=done in 24 steps.
- Why Control not Instruction: the model already HAS the checklist
  (`CustomSelfVerifyProcessor` fires "confirm values are semantically
  correct" and "validate your verification method"), yet with only one
  implementation it has no independent yardstick to notice a wrong number,
  so it re-reads and exits. A static prompt rule cannot supply the missing
  *runtime trigger* — the nudge must fire exactly when the observable
  compute+numeric-output signals are present and the agent is about to
  commit. That timing is only expressible as an `on_after_model` /
  `on_before_model` Control hook, mirroring the existing self-verify and
  http-verifier hooks. It also stays agent-authored (we ask the agent to
  choose the second method), so it does not hard-code any algorithm.
- Why Control not Action: TB2 exposes only `Bash` and the toolchain to
  recompute (python3/numpy, a hand-loop) is already present in the
  containers — no new capability is missing, only the reach-for-it at the
  decisive step.
- Retroactive check (A-corrective): partial-yes. If the agent had been
  forced to re-derive `m` a second way (e.g. `python3 -c "import numpy;
  print(numpy.polyfit(x,y,1))"`) it would have surfaced whether 2.5056 is
  even the correct OLS slope of the data before committing — the class of
  bug (single-implementation numeric commit) is exactly what the nudge
  targets. Honest caveat: if the agent's OLS were already correct and the
  grader's 2.5997 comes from a task-spec nuance the agent cannot infer,
  the cross-check alone won't flip it — but the recompute costs little and
  the same discipline directly flips `task_001653`-style cases where an
  independent centroid recompute would expose the wrong values.
- expected_global_gain: closes the recurring "committed a wrong number
  from a single implementation" cluster across scientific_computing +
  data_science (>=3 tasks). Generalizes to any deterministic numeric-output
  task (checksums, statistics, geometry) — the nudge names no task, path,
  constant, or algorithm.
- regression_risk: low. Fires at most once, only on tasks matching BOTH a
  compute signal and a numeric-output-file signal, and only on exit-intent
  AFTER the self-verify keepalive has already consumed the first exit
  (self-verify at _order=90 rewrites the first exit-intent into a tool call,
  so this hook stays silent then; it only arms on the genuine second exit).
  Worst case on a matched task is one extra reconciliation round (a few
  hundred tokens) that could in principle re-introduce a bug — but the nudge
  explicitly says "if two methods disagree, the primary result is suspect",
  biasing toward keeping a correct value. Non-numeric tasks (services, text
  edits, config) never match and are untouched.
- cost_shift: mildly positive (small increase) on matched numeric tasks —
  one extra verify/reconcile cycle; zero on the majority of tasks that don't
  match. Bounded by fire-once-per-task and the existing per-call max_tokens
  cap.
- rollback_trigger: if R3 shows any previously-passing numeric task flipping
  F after a spurious reconciliation, or the matched cluster still failing on
  wrong numbers despite the nudge (agent ignores the cross-check), revert
  the processor.
