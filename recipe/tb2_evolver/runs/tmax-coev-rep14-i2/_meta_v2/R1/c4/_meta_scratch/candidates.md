# Candidates

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Promote an explicit "independently cross-validate every computed
numerical result, and re-examine spec ambiguities (indexing, seed
draw-order, exact formula/rounding) before declaring done" strategy
into the agent's system prompt, for computational/data tasks.

- Tasks affected (corrective, ≥2 distinct, same mechanism):
  task_000111_cbada64a, task_001330_f5aff1f5, task_001653_c4cafa73,
  task_001937_ac874115, task_001035_26564093
- Signal: `exit_reason=done`, `reward=0`, short step counts (7–15),
  `final_pytest` shows a *self-consistent but numerically wrong*
  answer that is close-but-outside tolerance. The agent writes one
  plausible implementation, runs it once, and self-verifies only
  the output **format**, never the **value**.
- Verified (Read of message bodies):
  - task_000111 step "The program compiled and ran successfully...
    the values make sense" — agent's whole verification is a
    requirements checklist + "This is in the format ... as required";
    result m=2.5056 vs expected 2.5997 (deterministic OLS — a second
    method, e.g. numpy.polyfit reading the same file, would have
    disagreed and exposed the bug). No independent recomputation.
  - task_001330 final assistant turn: "The results make physical
    sense - the particle moves in a nearly vertical trajectory";
    m=0.048 wrong. The Monte-Carlo answer is sensitive to the *order*
    of the two `np.random.normal` draws per iteration — an ambiguity
    the agent never surfaced or tested; it accepted the first
    interpretation as obviously correct.
  - task_001653 `final_pytest`: `Centroid: 36.3687,... Distance:
    18.6199` vs expected `42.0095,... 11.3444` — wrong computation
    committed as done.
  - task_001937 `final_pytest`: `Optimal Grid 60` vs expected `50`
    — off-by-a-step search reported as final.
  - task_001035 `final_pytest`: optimal primer `GCCT` vs `GCAT` —
    one-character search error committed without re-check.
- Why Instruction not Control: the required capability is fully
  present (Bash → Python/numpy/C++ can recompute anything). The
  harness cannot mechanically inject the correct number without
  embedding per-task answers (forbidden). The `CustomSelfVerifyProcessor`
  self-verify prompt already fires, but its guidance is generic
  ("confirm the values are semantically correct") and the agent
  discharges it with a hand-wave ("makes physical sense"). The fix
  is a *strategy* the agent must apply itself — recompute a different
  way, and probe spec ambiguities — which is Instruction, not a
  mechanical hook. A Control hook cannot decide what "a different
  method" is for an arbitrary task.
- Why Instruction not Configuration: no existing knob controls
  verification depth; the self-verify text is baked into read-only
  `benchmarks/terminal_bench_2/harness.py`. The evolvable surface for
  this guidance is the system prompt (Instruction).
- Retroactive check (A-corrective): yes — for task_000111 a second
  independent OLS on the same file (numpy) would have produced 2.5997,
  contradicting the C++ 2.5056 and forcing the agent to find its
  read/precision bug before exit. For task_001330/001937/001035 an
  explicit "list the ambiguous choices and test the alternative
  interpretation" step targets exactly the draw-order / off-by-one /
  search-boundary errors that produced the wrong committed value.
  The guidance describes *how to verify*, never the answers.
- expected_global_gain: flips the recurring "done-but-numerically-
  wrong" cluster (scientific_computing / data_science / data-processing
  compute tasks) — ≥5 failing tasks this round share the mechanism.
  Generalizes to any task with a checkable computed output.
- regression_risk: adds a verification step that costs extra tool
  calls near the end of compute tasks; on already-passing tasks it is
  a no-op confirmation (they recompute, agree, exit). Low risk of
  flipping a pass to fail — cross-checking a *correct* answer confirms
  it. Slight risk of extra steps pushing a near-budget task over, but
  the affected cluster all finished in ≤15 steps with budget to spare.
- cost_shift: modest increase (+1–4 tool calls) on compute-heavy
  tasks that reach the exit; negligible on non-compute tasks (the
  guidance is scoped to "when you produce a computed/numeric result").
  Net expected positive: recovered passes outweigh the extra calls.
