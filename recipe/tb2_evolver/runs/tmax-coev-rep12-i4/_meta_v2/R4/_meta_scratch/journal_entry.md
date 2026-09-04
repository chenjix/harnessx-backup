
## Round 4 — hard-terminate degenerate assistant-turn loops

<!-- journal:frontmatter
round: 4
timestamp: 2026-06-04T00:00:00Z
hypothesis_id: h_degenerate_turn_terminator_v1
levers: [control]
predicted_affected: [task_000438_fee5a792, task_000032_3fb303f6, task_000938_6d7bdc5c, task_001044_45c70cf1, task_000761_072f9e93, task_001028_5bc8bc70, task_002138_2e85672e, task_002146_0bc2994c, task_000348_31fb8c8a, task_000908_170e5e4e]
cited_candidates: [C-004]
gating_outcome: pending
expected_global_gain: "Eliminates the exit_reason=error crash cluster (4 tasks: 000032, 000438, 000938, 001044) by converting the identical-turn runaway into a clean done that guarantees verifier scoring and protects the post-flight replay gate (which hard-fails on exit_reason=error). Recovers 20-70 wasted step-generations across the full 16-task identical-assistant-turn-loop cluster and lets exit-intent processors (verifier-dep guard) fire on tasks that previously died at the step cap. Generalizes to any future task that falls into byte-identical assistant repetition (Bash OR no-tool-call), a shape the R1 BashLoopBreaker structurally cannot cover."
regression_risk: "Near-zero on pass-rate. All 10 R3 passing tasks have a max consecutive identical-assistant run of at most 2; terminate_threshold=6 (warn at 4) cannot be tripped by any observed passing trajectory. Force-stop fires only after the run is provably dead (6 identical turns after the R2 prompt AND R1 soft block were both ignored). Small theoretical risk: a legitimate task that must poll one identical action many times could be stopped, mitigated by the high threshold and the fact that the R2 self-verify tool (which repeats legitimately in one passing task) does not produce identical assistant-text runs."
cost_shift: "Net decrease. Truncates 20-70 wasted step-generations (full model calls, some at the 4096-token cap) across 16 tasks including the 900-1900s runaways. The synthetic stop turn replaces a model call rather than adding one; no new model calls."
rollback_trigger: "Revert if R5 pass_rate below 0.20 OR any previously-passing task regresses with a [LoopTerminator] banner implicated in its final steps (threshold too low / disrupted a legitimate repeat)."
-->

### Why

R0-R3 are flat at 0.20 (10/50). Re-reading the R3 transcripts surfaced the true
dominant failure shape that all prior rounds only partially touched: a
DEGENERATE ASSISTANT-TURN LOOP. Measuring the max run of byte-identical
consecutive assistant messages: 16 distinct FAILING tasks have a run of 4 or
more (ranging 4 to 41); every PASSING task's max run is 2 or fewer, a
razor-sharp separator. Four of the 16 (000032, 000438, 000938, 001044) end in
exit_reason=error (agent_error crash driven by the repetition), the rest in
budget_exceeded.

The R1 BashLoopBreaker fingerprints only the Bash tool input, so it is blind
to the no-tool-call half of the loop (e.g. task_000761 repeats "The user is
asking me to verify my solution..." with NO tool call, alternating with the
self-verify ACK, msgs 2-11). Where it does fire, its soft "BLOCKED" redirect is
ignored verbatim: task_000032 shows 20 consecutive BLOCKs with the model
re-emitting the identical assistant turn each time. The R2 system prompt already
instructs the model to stop looping; it ignores that too. Instruction plus soft
mechanical redirect are proven-saturated over three rounds. The missing
capability is termination, plus coverage of the no-tool-call loop shape.

### Changes

- `processors/degenerate_turn_terminator.py` — new
  `DegenerateTurnTerminatorProcessor` (`MultiHookProcessor`, `on_before_model`):
  fingerprints the text of the last assistant turn in the assembled context;
  warns at `warn_threshold=4` (one contract-safe user message), and at
  `terminate_threshold=6` sets `skip_model=True` + `synthetic_output`, which the
  run loop turns into a `finish_reason="stop"` response with no tool calls, its
  own clean-exit condition (exit_reason=done). Strategy-only, no task literals.
- `config.yaml` — registered the new processor immediately after the retained
  R1 BashLoopBreaker (which still trims the Bash-input half early) and before
  CompactionProcessor. Rest of the R3 pipeline plus sibling `system_prompt.txt`
  copied byte-for-byte. R3's ProactiveVerifierDepGuard is retained: R3
  trajectories confirm it works (796/910/2108 no longer hit collection
  ImportError; they now fail with real ConnectionError, a server-capability
  gap, not a harness gap).

### Evidence

See `_meta_scratch/candidates.md` C-004 for body-quoted identical-turn loops on
task_000032 (20x "stuck in a loop" + BLOCK), task_000761 (self-verify no-tool
loop msgs 2-11 + build.rs loop msgs 13-21), and the full 16-task run-length
table (all FAIL, run 4-41) vs the passing set (all 2 or fewer).

### Uncertainty

This is a PARTIAL-yes retroactive check. On the 4 exit_reason=error tasks the
forced clean done is a strict robustness improvement (removes the crash,
guarantees verifier scoring, protects the replay gate). On the budget_exceeded
loopers it does NOT by itself add a missing deliverable; those remain
capability-bound, so the honest gain there is cost/step recovery and a clean
exit that lets the verifier-dep guard's exit-intent path run. Net expectation:
robustness plus a large cost win, with pass-rate upside concentrated on the
crash cluster. If R5 shows the crash tasks still failing with real per-test
assertions (not agent_error) and no cost reduction on the loop cluster, the
remaining wall is pure model capability and the lever should move off loop
control entirely for this benchmark.
