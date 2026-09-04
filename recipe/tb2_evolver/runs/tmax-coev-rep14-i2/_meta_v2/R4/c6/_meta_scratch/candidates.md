# Candidates — R4 c6

Assigned focus: `task_000118_3043e92d` (system_administration; log-quota
monitor daemon). Fails reward=0, exit=budget_exceeded at 80 steps.

## Candidate C-001 — compaction-safe loop detector

- **lens / lever / intent**: control-flow mechanism / Control / fix-a-harness-deficiency
- **hypothesis_id**: `h_loop_detection_compaction_safe_v1`

### Signal (verified body evidence)

task_000118 trajectory (`.messages.json`) is dominated by a byte-identical
tool-call loop: the agent runs
`python3 /home/user/deployment_monitor.py &\nsleep 0.5\nps -ef | grep -v grep | grep python3`,
sees `[python3] <defunct>` (its background monitor exits immediately because
its worker-check-and-exit sits at the top of the loop with no startup grace
period), and re-issues the *exact same* command. Measured: the identical run
spans messages 2→20 in the compacted view (≥10 consecutive identical calls),
interrupted by 3 compaction/summary events. The run ends
`budget_exceeded` at 80 steps, reward=0.

Cross-task scan of all 50 result.json / messages.json:
- `task_001098_f5acdd79`: 33 identical consecutive calls, **0 compaction**,
  exit=`error`.
- `task_001207_44e97fe1`: 20 identical, exit=`budget_exceeded`.
- `task_001857_24daeef3`: 15 identical, exit=`error`.
- `task_000313_1dce9844`, `task_000747_424c178b`: identical-call loops,
  exit=`budget_exceeded`.
- **`exit_reason` histogram across the round: `done`=38, `budget_exceeded`=10,
  `error`=2 — `loop_detected`=0.** Despite multiple tasks issuing 15–33
  byte-identical consecutive calls, the wired `LoopDetectionProcessor`
  (threshold=5) NEVER produced a clean `loop_detected` exit.

### Root cause (verified by isolated repro)

`LoopDetectionProcessor.on_step_start` fully clears its fingerprint window on
every message-count drop ≥ `compaction_drop_threshold` (5). A tight identical
loop re-narrates each turn, re-growing context and repeatedly tripping
`CompactionProcessor`, so the reset wipes the in-progress run before the
consecutive counter can reach `threshold` — **and before it can even reach
`warn_threshold`, so the escape-hatch nudge never reaches the model either.**

Reproduced deterministically (`_meta_scratch/repro.py`):
- identical loop, no compaction → raises at call #5 (correct).
- identical loop with per-step compaction resets → **never fires on 40
  identical calls** (the defeat).

### Fix

`processors/loop_detection_compaction_safe.py` —
`CompactionSafeLoopDetectionProcessor(LoopDetectionProcessor)`. Same
`_singleton_group="loop_detection"`, `_order=20`; overrides only
`on_step_start` so a compaction reset **preserves the trailing consecutive
identical run** and drops only the older heterogeneous window (the real
stale-false-positive risk). All other hooks, the raise, warn escalation and
`loop_detected` exit semantics are inherited unchanged.

Recalibrated knobs (`_meta_scratch/repro_calib.py`, `repro_warn.py` verify):
- `warn_threshold=8` — nudge now survives compaction and reaches loop tasks.
- `threshold=30` — raise ABOVE the observed recover-after-loop ceiling
  (task_000936 PASSED after 26 identical calls then recovered), so recovering
  loops are never cut while pathological 30+ non-recovering loops get a clean
  exit.
- `window_size=40` (> threshold so the tail can reach 30).
- `name_warn_threshold=999` — Strategy 2 off (Bash-only agent).

Verified: 26-identical+recover → never cut (PASS protected); 33-identical
non-recovering → clean cut at 30; mid-loops (15–20) → warn only.

### Retroactive check (variant: would-this-have-helped)

Under the CURRENT config the warn at 3 and raise at 5 were both silenced by
compaction on task_000118, so the agent received NO loop signal and burned all
80 steps re-running the identical background probe. With the fix the warn
("you are stuck — try something fundamentally different") fires from identical
call #8 onward *while the agent can still act* — the escape hatch it never
got. Answer: yes, the mechanism plausibly unblocks the focus task and
reclaims budget on the ≥5-task identical-loop cluster.

### Why Control (mechanism), not Configuration (knob) or Instruction

The processor IS already wired with non-default knobs (threshold=5) yet
produced 0 `loop_detected` exits on 15–33-call loops — so a pure knob tweak
cannot fix it; the reset logic is structurally wrong. It is not Instruction:
the warn text already exists and reaches the model *when it fires* — the bug is
that it never fires. The fix is a control-flow mechanism correction.

### Pareto

- `expected_global_gain`: the identical-loop cluster (≥5 tasks:
  task_000118, task_001098, task_001207, task_001857, task_000313,
  task_000747) currently loses its entire step budget with no loop signal;
  the fix reclaims budget via a clean `loop_detected` exit on 30+ loops and
  delivers a real warn-nudge on 8+ loops. Generalizes to any unseen task that
  falls into a compaction-driven identical loop — zero task-specific literals.
- `regression_risk`: threshold=30 sits above the only observed
  recover-after-loop PASS (task_000936, 26 identical → recovered → PASS), so
  it is not cut. Distinct-call sequences never accumulate (verified). Risk: a
  legitimate task issuing 30+ byte-identical consecutive calls would be cut;
  none observed (ceiling 26). Rollback trigger below.
- `cost_shift`: net negative — pathological loops exit ~30–50 steps earlier;
  one processor adds negligible per-call hashing. Warn adds ≤1 short nudge
  string to a tool result on armed loops only; no forced extra model turns.
- `rollback_trigger`: revert if next-round pass_rate drops, OR any
  `loop_detected` exit appears on a task that previously passed (especially
  task_000936_2a78f3ca), OR synthetic replay fails on the processor.
