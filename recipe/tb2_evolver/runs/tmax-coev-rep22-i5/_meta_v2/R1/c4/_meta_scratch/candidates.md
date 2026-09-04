# Candidates

Assigned focus: `task_000111_cbada64a` (scientific_computing) fails —
`result.txt` slope `m=2.5056` vs expected `2.5997` (|Δ|=0.094). The
OLS formula in the agent's C++ is textbook-correct; the compiled program
ran without error and produced a plausible-looking 4-field output. The
agent then ran the one-shot self-verify checklist, confirmed the output
*format* and *file existence*, and exited `no_tool_calls` — it never
re-derived the numeric result by an independent route, so it had no way
to catch that its value was wrong.

This is not a one-off. The same decisive-step shape recurs across the
scientific_computing / numeric-output cluster: the agent commits a
computed number after a self-check that validates *form*, never *value*.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add a general "cross-validate computed results" strategy to the system
prompt: when a task's success depends on a numeric / computed output the
agent cannot look up, re-derive the key values by a second independent
method (a different tool or library, or a hand-check on a small subset),
and reconcile any disagreement before committing — treat "code compiled
and printed a number" as unverified.

- Tasks affected: task_000111_cbada64a, task_001048_14335141,
  task_001937_ac874115 (≥2 distinct tasks, same mechanism: committed a
  wrong numeric result after a form-only self-check).
- Signal: `agent.finished=no_tool_calls`, `exit_reason=done`,
  `final_pytest` assertion is a numeric mismatch (`abs(m-2.5997)`,
  `math.isclose(...,161.8028)`, `grid_val==50 got 60`). Self-verify
  fired in every case but only re-read requirements / `ls` / `cat`.
- Verified (Read messages.json):
  - task_000111 final steps: after `_tb2_self_verify`, the agent's
    checks are "Check output file exists and has correct content" and a
    line-by-line re-read of requirements ("✓"), then `ls -lh` — it
    re-affirmed `2.5056,1.2262,...` verbatim without recomputing OLS by
    any other route.
  - task_001048 final steps: post self-verify, the agent runs
    `ls -lh /home/user/results.csv && cat results.csv` and declares
    "✓ Fits 3rd-degree polynomial using numpy.polyfit" — it inspected
    the file but never recomputed the integral independently; value
    `165.7908` is off from expected `161.8028`.
  - task_001937 final steps: post self-verify, the agent greps
    nginx.conf, `ls` the script, `cat report.txt` showing
    `Optimal Grid: 60` — expected `50`. It confirmed the file exists
    and services run, never re-ran the optimisation with a sanity
    cross-check.
- Why Instruction not Control: a processor cannot compute the correct
  answer, and the *content* of a cross-validation is task-specific
  (recompute OLS in Python/awk vs. re-integrate vs. re-search a grid) —
  it must be agent-authored per task. A mechanical hook could at most
  re-nudge, which the existing `CustomSelfVerifyProcessor` already does;
  the gap is the missing *technique*, not a missing trigger.
- Why Instruction not Configuration: the self-verify checklist knob is
  already present and firing. Its step 4 ("validate your verification
  method") is too abstract — the agent reads it as "re-read the spec".
  No existing knob encodes "recompute the number a second way"; that is
  a new strategy, not a tuned parameter.
- Retroactive check (A-corrective): yes — in all three trajectories the
  decisive step is the final form-only self-check. Had the agent
  re-derived the slope/integral/grid by an independent method it would
  have seen the two answers disagree and been forced to debug before
  committing; the tasks are deterministic given their inputs, so a
  correct second computation exposes the bug.
- expected_global_gain: flips the numeric-output sub-cluster of
  scientific_computing (≥3 failing tasks this round) where the answer is
  close-but-wrong and no ground truth is available to the agent; the
  rule keys off "computed value the agent can't look up", so it
  generalises to unseen curve-fit / simulation / optimisation tasks.
- regression_risk: low. The guidance is additive prose appended to the
  existing 5-line prompt; it only asks for a second computation on tasks
  that *produce* a computed value. Non-numeric tasks (file ops, service
  wiring, security) are untouched by its trigger condition. Worst case
  is a few extra Bash calls on numeric tasks.
- cost_shift: small positive on numeric tasks only (one extra
  independent recompute + reconcile, typically <5 Bash calls), ~0 on the
  non-numeric majority. Well within per-round budget; the correctness
  upside dominates.
