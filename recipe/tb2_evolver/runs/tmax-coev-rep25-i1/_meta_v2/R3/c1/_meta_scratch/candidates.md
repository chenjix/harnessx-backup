# Candidates — R4 (synthesis slot)

## Summary of investigation

The pivot table lists three lineages: `n0` (R0, 0.500), `n1` (R2/c0, 0.580),
`n2` (R2, 0.580). **`n1` and `n2` config.yaml are byte-identical** (verified by
Read) and share the same trajectory dir (`r1-fe-c0-traj`). So there are really
only TWO distinct harnesses: R0, and "R0 + StuckReasoningRecoveryProcessor".
`current_config` in the brief points at R0.

Per-task diff (verified from `*.result.json`, status=ok for all): R0 = 25/50,
r1-fe-c0 = 29/50. The 8 differing tasks:

| task | R0 | r1-fe-c0 |
|---|---|---|
| task_000329_a3ac56b0 | 0 | 1 |
| task_000338_27d6a1be | 0 | 1 |
| task_001032_1adaccb9 | 0 | 1 |
| task_001264_9f4ca84a | 0 | 1 |
| task_001498_df8254c9 | 0 | 1 |
| task_001673_86224c91 | 0 | 1 |
| task_001089_220cc46b | 1 | 0 |
| task_001536_acfe6c35 | 1 | 0 |

**Attribution finding (decisive):** the StuckReasoningRecoveryProcessor
(`LOOP DETECTED` / `STILL LOOPING`) fired **zero times across all 50 tasks** in
the r1-fe-c0 run — verified by grepping every `*.messages.json`. It never
fired on any of the 6 gained tasks NOR on either of the 2 lost tasks. Therefore
the +6/-2 (net +4) swing between R0 and r1-fe-c0 is **entirely run-to-run
variance**, not a causal effect of the added processor. The processor's
`repeat_threshold=12` is never reached (the benchmark's max identical-assistant
run is exactly 12, and LengthTruncationRecovery at threshold=2 breaks the
common loops first).

**Why the two regressions are not repairable at the harness layer:**
- task_001089: in r1-fe-c0 the agent ran to a clean completion (exit 0, full
  summary) but the verifier scored 0 — a correctness difference, not a loop.
  In R0 it actually looped ("cut off by token limit", identical narration) yet
  banked reward=1. Stochastic.
- task_001536: in r1-fe-c0 the agent ended mid-debugging the cyclic-symlink
  logic (65 msgs, still failing); in R0 it happened to finish correctly (76
  msgs). Depth/correctness difference. The nudge was inert in both.

No complementary mechanism from any pivot repairs a mechanism-attributable
regression, because there is no mechanism-attributable regression. The
synthesis focus (graft a complementary mechanism to fix the winning lineage's
unique losses) is **unsupported by the trajectories**.

**Threshold sweep considered and rejected this round:** lowering
`repeat_threshold` to 6 would engage the real failing loop cluster
(6 failing tasks with identical-runs >=6: 000118, 000015, 001031, 001818,
000740, 000958) — but the only two PASSING tasks with long runs, task_000344
(run 12 @ frac 0.45) and task_001591 (run 11 @ frac 0.76), loop MID-TASK and
then self-recover to reward=1. A lower threshold would nudge them during
productive recovery → real 2-task regression risk, to chase an advisory-only
fix whose expected yield is low (the model already narrates that it is stuck
and still cannot break). That is a Pareto-negative trade on current evidence,
so the threshold stays at 12. If a future round wants to sweep the threshold it
should be its own gated bet with its own rollback trigger.

## Candidate C-001
[lens: success | lever: control | intent: preservative-lock]

Adopt the best-scoring live lineage as the shipped config: R0 pipeline +
`StuckReasoningRecoveryProcessor` (repeat_threshold=12, max_nudges=3), made
self-contained by copying the processor into this proposal's `processors/` and
pointing the `file://` path at the local copy; sibling `system_prompt.txt`
copied byte-for-byte from R0.

- Tasks affected (preservative-lock — passing cluster whose guard we keep in
  place): the r1-fe-c0 lineage passes 29/50 vs `current_config` (R0) 25/50.
  The guard is the *only* structural difference between the incumbent 0.580
  lineage and R0. Representative long-loop tasks the guard is designed to
  protect against regressing: task_000118_3043e92d, task_000015_89886d8d,
  task_001031_a8f0eb37, task_001818_b251e5ea (all failing loop shapes with
  identical-assistant runs 6-12).
- Signal: identical consecutive assistant narration; observed max run = 12
  across the batch (computed by reconstructing whitespace-normalised assistant
  content runs from every `*.messages.json`). The guard keys on this exact
  observable state (repeated assistant content) which the two existing loop
  guards structurally miss (LengthTruncationRecovery keys on
  finish_reason=length; edit/command-fingerprint guards key on tool calls).
- Verified (Read):
  - Config byte-identity of n1 (R2/c0) and n2 (R2): both are R0 + the
    StuckReasoning processor at threshold=12 — read both YAMLs in full.
  - Zero-fire attribution: `grep -c "LOOP DETECTED\|STILL LOOPING"` = 0 on all
    50 r1-fe-c0 message logs, including the 6 gained and 2 lost tasks.
  - task_000344 (pass) msgs 84, longest identical-assistant run 12 ending at
    fraction 0.45, repeated text "I see the issue now. The program format is
    `[op, value, ...]` ... Let me try a simpler program" — a mid-task loop that
    self-recovered to reward=1 (this is why threshold stays >=12, not lower).
  - task_001591 (pass) msgs 88, run 11 @ frac 0.76, repeated text "I've been
    repeating the same command ... use base64 encoding to avoid shell" —
    mid-task loop, self-recovered to reward=1.
- Why Control not Configuration: the incumbent value we adopt is the pipeline
  member itself, not a knob re-tune of an existing member. We explicitly
  reject the Configuration move (lowering repeat_threshold) this round because
  the body evidence (two passing tasks self-recovering at runs 11-12) shows a
  lower threshold carries a concrete 2-task regression risk that outweighs the
  advisory-only upside.
- Retroactive check (B-preservative-lock): The guard did not fire on the
  differing tasks, so removing it would NOT have flipped any observed task —
  by the strict Variant-B test the guard is presently "free" (no measured
  flips either way). This candidate therefore does NOT claim a flip; it claims
  parity with the 0.580 incumbent and keeps the smoke-tested guard wired for
  the loop shapes it is designed to catch (max-run tasks sit exactly at the
  threshold, so the mechanism is on the boundary of engaging as loops
  lengthen). Given the two live lineages are tied at 0.580 and the brief allows
  ties to stand, shipping the incumbent's config (self-contained) rather than
  regressing to R0's 0.500-labelled baseline is the smallest defensible edit
  for the synthesis slot.
- expected_global_gain: Holds the 0.580 incumbent lineage (29/50) rather than
  reverting to R0's 25/50 label. No new flips claimed — the honest reading is
  that the guard is inert on the current batch, so the realistic global gain is
  "parity with the best live lineage, self-contained config, no regressions
  introduced."
- regression_risk: Effectively zero relative to the incumbent — the config is
  functionally identical to n1/n2 (same processor, same threshold, same
  prompt), just relocated to a self-owned `file://` path. Because the guard
  never fires on this batch it cannot alter any task's outcome vs the incumbent.
- cost_shift: Zero vs the incumbent (identical pipeline; the guard adds no
  per-step cost and fires zero times). Slightly cheaper than R0 only in the
  stochastic sense reflected in the 25→29 label difference, which we do not
  claim as causal.
