# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace `CustomSelfVerifyProcessor` with a subclass whose exit checklist adds an
explicit step: independently re-derive any COMPUTED NUMERIC output with a
different method/tool (and confirm all input rows were consumed / seed / index /
precision applied) before finishing.

- Tasks affected: task_000111_cbada64a, task_001048_14335141, task_001937_ac874115
  (all scientific_computing, all reward=0)
- Signal: `final_pytest` assertion failures on a *numeric value* while the code
  ran cleanly (`exit_reason=done`, `finished=no_tool_calls`, low step counts).
  Not crashes, not wrong paths — plausible-but-wrong numbers.
- Verified (body-quoted):
  - task_000111 messages.json: agent writes textbook-correct OLS+bootstrap C++,
    runs `g++ ... && ./analyze && cat result.txt` ONCE -> `2.5056,1.2262,...`,
    then self-verifies only format/existence ("in the format `m,c,ci_lower,
    ci_upper` with 4 decimal places ... correct"). Verifier: `Expected m to be
    approx 2.5997, got 2.5056` (abs err 0.094 >> 1e-3). No independent recompute
    of `m` was ever attempted.
  - task_001048 final_pytest: `Chunk 0 integral mismatch. Expected 161.8028,
    got 165.7908` — single un-cross-checked numeric path.
  - task_001937 final_pytest: `Expected Optimal Grid to be 50, but got 60` —
    plausible optimum, never re-evaluated against the objective independently.
- Why Control not Instruction: the fix is a mechanical hook that must fire
  uniformly on the exit boundary of *every* task (the existing self-verify hook
  is exactly that mechanism); it is not new knowledge the static system prompt
  could carry, because it must be injected at the decisive exit moment after the
  agent believes it is done — the same reason `CustomSelfVerifyProcessor` is a
  processor and not a prompt line. We reuse that processor's tested one-shot
  interception verbatim and only extend its message. Why not Action: the agent
  already has Bash (the only tool TB2 exposes) — it can already run an
  independent `python3 -c` cross-check; the gap is being prompted to do so at
  exit, not a missing capability.
- Retroactive check (A-corrective): yes — on task_000111 a numpy.polyfit
  cross-check of `m` would have disagreed with 2.5056 and forced the agent to
  find its data/precision bug before exit; the injected step names exactly that
  action for computed numeric outputs.
- expected_global_gain: flips the failing scientific_computing numeric-accuracy
  cluster (0/7 -> plausibly a few); the step generalizes to any task whose
  deliverable is a computed value (data_science, data_processing numeric tasks).
- regression_risk: low. Same singleton_group -> drop-in replacement, no double
  message insertion (contract-checked). Adds one extra checklist step read once
  per run; non-numeric tasks are told to skip it. Worst case: a few extra Bash
  cross-check calls -> slightly longer runs, no correctness regression to
  already-passing tasks (they still pass format/existence steps unchanged).
- cost_shift: small increase — a handful of extra verification Bash calls on
  numeric tasks near exit; negligible on non-numeric tasks (one longer prompt
  read once). Well within budget given most tasks finish far under the wall
  clock.
