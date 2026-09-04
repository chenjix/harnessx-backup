# Candidates — R1 / c3

## Assigned focus
`task_000111_cbada64a` (scientific_computing) fails: the agent wrote a
textbook OLS in C++, got slope `m=2.5056`, but the hidden grader expected
`m≈2.5997` (diff 0.094 — far beyond FP error). The code is self-consistent and
correct by its own reading of the spec, so the agent had no internal signal
that the number was off. It self-verified only file existence/format and exited
in 7 steps.

## Honest diagnosis (why this is mostly a capability gap)
The whole `scientific_computing` cluster scored 0 this round, but for
**heterogeneous** root causes, each a domain-reasoning / numeric-spec error the
agent could not detect internally:

- `task_000111` — OLS slope 2.5056 vs expected 2.5997 (numeric/precision or
  data-read convention).
- `task_000017` — primer sequence off-by-one: `GCTAGCGCGCTAGCT` vs `AGCTAGCGCGCTAGC`.
- `task_000117` — PDB column parse `ValueError: could not convert '5 -94.99'`.
- `task_001035` — wrong optimal primer `GCGG` vs `GCAT`.
- `task_001048` — integral chunk mismatch 165.7908 vs 161.8028 (method choice).
- `task_001937` — optimal grid 60 vs 50 (convergence/algorithm choice).

No single harness mechanism *solves* these — each needs the model to match the
reference's exact conventions, which is a **model capability gap**. Per SOUL.md
I will NOT embed task-specific numeric knowledge in the prompt.

The one *generalizable, harness-shaped* observation that recurs across ≥2 tasks
is a **self-verification blind spot**: on computed-value deliverables the agent
verifies format/existence but never independently re-derives the *value*. That
is a strategy gap addressable by a general nudge (not task knowledge). I ship
the smallest defensible edit that targets it and explicitly flag the rest as a
capability gap in the journal.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a one-shot pre-exit processor (`NumericCrossCheckNudge`) that, on the
agent's exit-intent turn, reminds it that a computed-number deliverable must be
re-derived by an *independent second method* and reconciled before committing —
a general strategy, no task literals.

- Tasks affected: task_000111, task_001330 (both: computed value committed on
  first run with no independent cross-check, exited in 7 steps); reinforced by
  task_001048 (numerical-method mismatch that a second method would surface).
- Signal: `agent.finished=no_tool_calls`, `exit_reason=done`, very low
  `steps` (7) on computed-number tasks; `final_pytest` fails on a numeric
  assertion (`Expected m to be approx 2.5997, got 2.5056`) while the agent's own
  self-verify only ran `ls`/`cat` on the output file.
- Verified (Read):
  - task_000111 msgs: step 5 the agent runs `g++ ... && analyze && cat result.txt`
    → `2.5056,1.2262,...`; self-verify turn only runs
    `ls -lh /home/user/analyze.cpp /home/user/analyze /home/user/result.txt` and
    exits — no second-method re-derivation of `m`.
  - task_001330 msgs: single script run yields `Mean slope (m): 0.04776...`; the
    self-verify turn only re-`cat`s `trajectory_fit.txt` and confirms format —
    no independent reconciliation of the polyfit/noise convention.
- Why Control not Instruction: the existing `CustomSelfVerifyProcessor` message
  is hardcoded in `benchmarks/terminal_bench_2/harness.py` (read-only) and the
  system prompt is a sibling `.txt` I do not own here; a Control processor is
  the only in-config surface that can inject an *additional* one-shot reminder
  keyed on the exit-intent signal, composing after the existing self-verify
  without competing with its keepalive tool call. There is also no tool output
  to post-process — the gap is a missing verification step, so Action is wrong.
- Why Control not Configuration: no existing knob toggles a numeric-
  reconciliation reminder; the behavior does not exist in the pipeline yet.
- Retroactive check (A-corrective): **partial / honest no on the assigned task.**
  For task_000111 a second OLS route (`numpy.polyfit`) would *agree* with the
  agent's correct-but-unexpected slope, so the nudge would not by itself flip
  it — the residual gap there is a capability/spec-convention issue logged as
  such. The nudge is expected to help the *method-choice* subset (e.g.
  task_001048, where a scipy vs manual integral disagree) and to raise the
  general reliability of computed-number commits. Shipped as the smallest
  defensible, low-regression edit against a real cluster-wide self-verification
  blind spot rather than drifting to another proposal's territory.
- expected_global_gain: raises the odds of catching spec/method mismatches on
  the computed-number cluster (scientific_computing + data_science/processing
  numeric tasks). Even a 1–2 task flip across the cluster is net positive given
  0/7 sci-computing pass today.
- regression_risk: very low. Fires at most once, only on exit-intent, adds one
  user message and no tool call, and is explicitly a no-op the agent is told to
  ignore on non-computational tasks. Worst case: a few extra tokens on the
  final turn. No change to any passing cluster's control flow.
- cost_shift: +~250 output tokens once per task that reaches an exit-intent
  turn, plus at most one extra verification turn on genuinely-numeric tasks.
  Negligible against the 80-step budget.

## Rollback trigger
If R1 shows any regression on currently-passing clusters (e.g. a previously
`reward=1` task drops), or if the sci-computing cluster does not improve at all,
revert C-001 — the residual is a capability gap that no harness nudge closes.
