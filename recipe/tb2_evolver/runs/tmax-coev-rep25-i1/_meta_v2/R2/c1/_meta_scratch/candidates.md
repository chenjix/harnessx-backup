# Candidates — R2 synthesis (c4 ⊕ c1)

## Context: why the pivot scores were misleading

The pivot table reports c4=0.460 and c1=0.380 vs R0=0.500, which reads
as "both interventions regressed". Reading `result.json` `status` fields
shows most of the reported "unique losses" are `status: error` — docker
container-name conflicts from parallel scheduling (`RuntimeError: docker
run failed ... container name ... already in use`), NOT harness/agent
regressions. Filtering to `status: ok` (real agent outcomes) flips the
picture:

- **c4 (IterativeVerifyGate)**: +5 real gains (740, 1264, 1498, 1673,
  1706), −1 real loss (965). Net **+4** — the strongest lineage.
- **c1 (LoopDetectionProcessor)**: +4 real gains (338, 740, 1498, 1673),
  −2 real losses (1536, 1652). Net **+2**.

Synthesis start point = **c4** (best real net). c1 contributes one
genuine unique real solve c4 lacks (338) via its *exact-repeat* loop
guard; c1's two real losses trace to a *different, noisy* sub-mechanism
(name-only warning) that must be neutralised, not copied.

---

## Candidate C-001
[lens: capability-gap | lever: control | intent: preservative-transfer]

Add the stock `LoopDetectionProcessor` (exact-repeat strategy only) to
the c4 lineage, with the name-only Strategy-2 warning disabled.

- Tasks affected:
  - passing under c1 (habit fired, mechanism helped): task_000338_27d6a1be
    (exact-repeat warn broke a byte-identical Bash loop and the agent
    committed the correct kernel).
  - failing under R0 where the same shape recurs (loop absent-guard):
    task_000015_89886d8d (R0: 33 identical `tee ... << PYEOF`, fully
    stuck), task_000338_27d6a1be (R0: ended on a repeated identical
    call with the wrong OCR result).
- Signal: exact-identical consecutive Bash fingerprints repeated ≥10×
  in stuck runs; `[LoopDetection]` warn text present in c1 trajectories
  at 338 immediately before the correct commit.
- Verified (Read):
  - task_000338 (c1) final steps: tool result carries
    `[LoopDetection] ⚠️ The exact same tool call(s) have been issued 10
    times in a row (Bash)`; the very next assistant turn stops looping
    and commits `1 2 1 / 0 0 0 / -1 -2 -1` → reward 1. R0 run of 338
    ended on the repeated call with `-1 2 -1` (wrong) → reward 0.
  - task_000015 (R0, from R1 journal + traj): 33 byte-identical
    `tee /tmp/ocr.py << 'PYEOF'` calls, 32 dups — a stuck loop the
    exact-repeat raise (threshold 12) would have broken.
- Why Control not Instruction: the failure is a *mechanical* runaway —
  the model re-emits a byte-identical call and cannot see it is looping;
  a prompt rule ("don't repeat yourself") does not fire reliably inside
  the loop. A hook that fingerprints tool calls and injects a warning /
  raises `LoopDetectedError` is the only mechanism that acts on the
  observable repeat state. Not Action: no new capability is needed; Bash
  already suffices.
- Why disable Strategy-2 (name-only warn): this benchmark is Bash-only,
  so name-only fingerprints match on *every* consecutive Bash call
  regardless of arguments. Strategy 2 therefore fires on normal
  long-horizon exploration, not loops. Verified: c1's two real losses
  (1536, 1652) both show `[LoopDetection] ⚠️ You have called Bash N times
  consecutively with different arguments` (name-only warn) firing on
  legitimate multi-step work, after which the agent prematurely declared
  "task complete" / "let me proceed efficiently" and abandoned remaining
  requirements — R0 solved these in 76/88 msgs, c1 cut them to 38/64 and
  failed. Setting `name_warn_threshold` above any realistic step count
  keeps Strategy 1 (exact-repeat, high-precision) and removes Strategy 2.
- Retroactive check (C-preservative-transfer): yes — 338 shows the
  exact-repeat warn firing at the decisive step and unblocking a correct
  commit; 015's byte-identical loop would have been raised at threshold
  12. Both ends grounded (habit fires on 338; absent-and-needed on 015).
- expected_global_gain: recovers the degenerate exact-repeat loop
  cluster (338, 015, and R0's dup-command-heavy FAIL cluster the R1
  journal flagged: 190 dup-commands on FAIL vs 96 on PASS) that
  IterativeVerifyGate alone does not touch.
- regression_risk: exact-repeat raise could cut a passing task that
  legitimately repeats an identical call ≥12×. Mitigated: threshold 12
  is above the max observed transient-recover run (11, per c1's
  calibration note); all five c4 gains have max exact-identical run ≤9
  (verified: 740=1, 1264=1, 1498=1, 1673=7, 1706=9). Name-only warn
  disabled removes the mechanism that caused c1's real losses.
- cost_shift: net DECREASE — stuck loops are cut early instead of
  burning the full step budget; no added per-step cost (warning is
  appended to an existing tool result).

## Candidate C-002
[lens: success | lever: control | intent: preservative-lock]

Adopt `IterativeVerifyGate(max_nudges=2)` in place of the stock one-shot
`CustomSelfVerifyProcessor` as the completion gate (lift the strongest
lineage's mechanism into the synthesis).

- Tasks affected:
  - passing under c4 (gate fired and held): task_000740_59416444,
    task_001264_9f4ca84a, task_001498_df8254c9, task_001673_86224c91,
    task_001706_24462a09 (all R0=0 → c4=1, real `ok`).
- Signal: c4 trajectories show the verification checklist injected on a
  no-tool-call exit, followed by real verification commands, then a clean
  pass. R0's stock one-shot gate fired at most once and these tasks
  exited unverified → reward 0.
- Verified (Read): c4 965 trajectory shows the gate's checklist
  ("Before finishing, do NOT just assert completion — actually verify")
  injected as a tool result; c4's 5 gains all pass after the bounded
  re-nudge. R0 gate is `CustomSelfVerifyProcessor` (one-shot) — the five
  gains were R0 failures.
- Why Control not Instruction: the gate must *intercept the exit event*
  and re-inject work; a prompt rule cannot block a `finish_reason=stop`
  turn. Why this specific processor (bounded, work-aware) not the stock
  one: the stock gate nudges once and cannot tell a re-asserted "done"
  from real verification, so a second bare exit slips through.
- Retroactive check (B-preservative-lock): yes — removing the gate (i.e.
  reverting to stock one-shot self-verify, = R0) demonstrably fails all
  five cited tasks (they are R0 zeros).
- expected_global_gain: locks in c4's +5 verified-completion cluster.
- regression_risk: the gate can over-nudge an already-correct solution
  into churn — this is exactly c4's one real loss (965: agent had correct
  variance, gate re-nudged, agent over-edited Welford and broke it).
  `max_nudges=2` bounds it; C-001's exact-repeat guard also catches any
  re-edit loop the extra nudge induces. Accepted as a known, bounded cost
  against +5 gains.
- cost_shift: small INCREASE on tasks that get re-nudged (bounded by
  max_nudges=2); net positive given the 5:1 gain:loss ratio.
