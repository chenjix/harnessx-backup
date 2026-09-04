# R5 Candidates

## Candidate C-001

**Three-axis tag:** lens=cross-round-regression / lever=configuration / intent=preservative-lock (revert a net-negative knob)

**Signal.** R4 changed exactly one thing vs the R3 incumbent:
`LoopDetectionProcessor.name_warn_threshold` 8 → 999 (disabling the
Strategy-2 tool-*name*-repetition warning). The measured outcome:

- R3 (name_warn=8): **32/50** — the incumbent, best config to date.
- R4 (name_warn=999): **30/50** — a net **−2**, driven by a chaotic
  ±swing (+5 gained / −7 lost), not a coherent cluster.
- R4's own `gating_attribution` records `score 0.6000 >= incumbent(mean)
  0.6400 - tol 0.0400` — it squeaked past the gate on tolerance, not on
  merit, and R4's declared `rollback_trigger` was: *"If R5 pass_rate <
  32/50 … revert name_warn_threshold to 8."* R4 delivered 30/50, so the
  rollback condition is met.

**Verified body evidence.**
- `history/R3_to_R4.diff`: the sole config delta is
  `name_warn_threshold: 8 → 999`. Every processor, kwarg, and the
  system-prompt sidecar are otherwise identical.
- `history/R3_per_task.json` vs `history/R4_per_task.json`:
  GAINED R3→R4 = {536, 1032, 1089, 1090, 1818}; LOST R3→R4 = {028, 264,
  748, 1264, 1321, 1515, 1536}. The gained set and lost set are unrelated
  domains (data_querying, file_operations, debugging, data_processing vs
  system_administration, data_querying, …) with no shared mechanism — the
  fingerprint of run-to-run variance on borderline tasks, not a knob
  effect. Net = −2.
- The truncation-spiral control lever (R1/R3) that produced the 32/50
  incumbent is confirmed landed and exhausted: `task_000396_e56917e2`
  messages show truncated no-tool turns collapsed to ~590 chars and 37/49
  assistant turns now carry tool calls; the task now fails on a genuine
  numerical bug (RK45 max deviation 0.608), not a loop.

**Retroactive check (variant: would-this-have-helped).** Had R5 shipped
name_warn=8 (i.e. never made the R4 change), the incumbent 32/50 config
is restored. The 5 tasks R4 "gained" are borderline and their pass under
32/50 vs 30/50 is within the sub-repeat noise the playbook warns to treat
as noise; the 7 tasks R4 lost include several (028, 264, 1321, 1536,
1515, 748) that R3 passed and are more likely to be recovered by
returning to the incumbent than by keeping the net-negative knob.

**Why configuration, not control/instruction.** The regression is
attributable to a single configuration knob, and the corrective action is
purely to restore that knob's incumbent value. No new mechanism is
warranted: the residual 13 stable failures (numerical algorithms 396/1937,
security bypass 505, CSV/NaN parsing 587, memory-leak/deadlock 1781, log
rotation 118, tarball 933, error-value 1498) are model capability/logic
gaps, not harness-addressable — verified via each task's verifier
`output_tail` (wrong computed values / wrong algorithms, not loops or
truncation or import crashes).

**Tasks affected (preservative-lock rule).** predicted_affected lists the
tasks R3 passed that R4 lost and that returning to name_warn=8 should
re-protect: [task_000028_7fe033ac, task_000264_ab8c7253,
task_000748_c9807703, task_001264_9f4ca84a, task_001321_658ce4a8,
task_001515_eed714e6, task_001536_acfe6c35].

- `expected_global_gain`: Restores the 32/50 incumbent by undoing a
  single net-negative knob whose gains were noise and whose losses hit a
  real 7-task set; protects those 7 R3-passing tasks from the T→F drift
  the R4 knob introduced.
- `regression_risk`: Low. The change is a byte-for-byte return to a config
  that already measured 32/50. The only downside is if the 5 tasks R4
  "gained" were genuinely helped by removing the name warning — but they
  span unrelated domains with no mechanism tying them to Strategy-2
  suppression, so that is unlikely; if it happens, revisit with a
  *middle-ground* threshold rather than 999.
- `cost_shift`: Negligible / slightly up. Re-enabling Strategy-2 at
  threshold 8 re-introduces some name-only warnings (~350 chars each) on
  long runs, but at threshold 8 they fire far less than R4's rationale
  implied for a threshold-8 baseline; net context cost ~flat vs R3.
