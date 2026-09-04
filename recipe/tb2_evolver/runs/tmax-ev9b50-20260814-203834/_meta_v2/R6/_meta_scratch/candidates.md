# Candidates — R6

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Re-arm the max_tokens repetition-loop breaker by triggering on the runaway's
observable signature (oversized no-tool-call assistant `content`) instead of
relying on `finish_reason == "length"`, which the eval backend never reports.

- Tasks affected (runaway-loop failing cluster, same mechanism):
  task_001032_1adaccb9, task_000118_3043e92d, task_000010_644ab1c2,
  task_001818_b251e5ea, task_001031_a8f0eb37, task_000958_4bb2b05d,
  task_001701_95e3bbcb, task_000015_89886d8d, task_000264_ab8c7253,
  task_001321_658ce4a8
- Signal: per-task scan of `raw_assistant` events in the episode JSONL —
  every failing runaway task has 1-44 assistant turns with `content` length
  161K-321K chars and **no tool call**, and `finish_reason` is recorded as
  `None` on ALL of them (never `"length"`). The R1/R5
  `LengthTruncationRecoveryProcessor` gates strictly on
  `finish_reason == "length"`, so it is a silent no-op on exactly these loops.
  Perfect bimodal separation: all 28 PASSING tasks have max assistant
  `content` <= 12,000 chars and zero huge no-tool-call turns; the 10 runaway
  failures start at 161K chars. The trigger threshold (40K) sits in the empty
  band between the two clusters.
- Verified (Read of episode JSONL / messages.json):
  - task_001032_1adaccb9: session `3d2fbaac` raw_assistant contentlen sequence
    96741, 94701, 92026, 89871, 87050, ... (13 consecutive huge no-tool-call
    blobs), `finish_reason=None` throughout; messages.json msgs 3-58 show the
    run loop's passive "Your previous response was cut off by the token limit.
    Please continue" nudge appearing ~25x while the assistant re-emits "The
    user is telling me I've been stuck in a loop. I need to stop analyzing..."
    — 7962s / 75 steps, still exit=done reward 0. The R1 processor's own
    corrective nudge language never appears in context (grep for "narrat"/
    "single command"/"[truncat" returns nothing) — confirming it never fired.
  - task_000010_644ab1c2: session `e6341af6` raw_assistant contentlen 161854,
    135357, 131968, 129785, 122699, 161040, ... (9 huge no-tool-call turns),
    finish_reason=None; 2444s. (Ended by writing `/home/user/operator.py`
    which shadows stdlib — a distinct downstream bug, tracked separately.)
  - task_000118_3043e92d: 17 huge no-tool-call turns, max 256861 chars,
    finish_reason=None; 5038s, exit=error.
  - task_001818_b251e5ea: 13 huge turns, max 185757, finish_reason=None; 3456s.
  - task_001031_a8f0eb37: 8 huge turns, max 196360, finish_reason=None; 2288s.
- Why Control not Configuration: the mechanism (collapse runaway content +
  arm a corrective "issue one Bash command" nudge) is already a custom
  processor; there is no builtin knob that changes its detection predicate.
  The fix is a change to the processor's trigger logic (add a content-length
  branch to the `on_after_model` gate), which is a Control-lever edit to an
  existing `MultiHookProcessor`, not a kwarg tune on a builtin. (The new
  `content_char_threshold` kwarg is exposed so the band can be retuned, but
  the substantive change is the OR-branch in the gate.)
- Why Control not Instruction: the model already receives the run loop's
  passive "continue" nudge and ignores it — a prompt rule cannot mechanically
  collapse a 160K-char blob out of context or force the loop to physically
  break. The runaway content must be removed from context and a decisive
  redirect injected at the loop layer.
- Retroactive check (A-corrective): yes for the loop-dominated tasks. On
  task_001032/000118/000010/001818 the model was making genuine progress
  between blobs (task_001032 msg 15: "I see the problem now. The tar file
  structure is unusual...") but burned the entire step/wall budget
  re-priming the same narration because the runaway content stayed in
  context and re-seeded the loop turn after turn. Collapsing the blob on the
  FIRST occurrence and injecting the "one Bash command" redirect (R1's proven
  mechanism — R1 dropped the error cluster 7->2) reclaims that budget for
  real tool calls. Honest caveat: task_001031 (MPI send/recv deadlock) and
  task_000264 (recursive-SQL logic) have upstream capability gaps and may not
  flip even with budget reclaimed — the expected outcome is partial recovery
  of the subset whose loop was the actual blocker, not the whole cluster.
- expected_global_gain: Flip a subset of the 10-task runaway-loop cluster
  (the largest homogeneous cross-domain failing cluster) by converting
  5000-8000s runaway max_tokens loops into bounded runs that retain budget to
  finish. Generalizes because the trigger keys on content shape, not task
  content — any future runaway loop on this backend is caught.
- regression_risk: Effectively zero on the 28 passing tasks. Their largest
  assistant turn is 12,000 chars — 3.3x below the 40K trigger — and none has a
  huge no-tool-call turn. The processor is a strict no-op on any turn under
  40K chars or any turn that carries a tool call. The `finish_reason=="length"`
  branch is preserved, so v1 behaviour is a subset of v2.
- cost_shift: Strongly negative. Eliminates dozens of 100K-320K-char runaway
  generations (task_001032 alone burned 7962s across 3 sessions of repeated
  160K-char blobs) — collapsing them to a ~1.8K-char excerpt slashes both
  tokens and wall-clock on the affected tasks. Passing tasks unaffected.
- rollback_trigger: If R7 regresses any currently-passing task, or the count
  of runaway no-tool-call turns / long-elapsed failures does not drop, the
  trigger is either mis-scoped or the loops are downstream of capability gaps —
  revert to the R5 config.
