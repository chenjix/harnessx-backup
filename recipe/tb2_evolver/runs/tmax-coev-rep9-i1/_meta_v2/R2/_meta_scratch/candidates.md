# Candidates — R2

## Candidate C-001
[lens: failure | lever: configuration | intent: preservative-lock]

Keep R1's `LoopDetectionProcessor` (tool-call fingerprint, warn=4/raise=6)
untouched — it is doing its containment job and must not regress.

- Tasks affected (protect): task_001031_a8f0eb37, task_001321_658ce4a8,
  task_000863_7acceb19 (R1 flip), task_001090_c61c71f2 (R1 flip)
- Signal: R1 exit reasons show identical-tool-call loops now terminate as
  `loop_detected` (task_001031: was `error`/5868s, now `loop_detected`/230s)
  instead of runaways. Net R0->R1 was +1 (29->30).
- Verified (Read): task_001031 msgs 43-55 — identical `cat > analyze.py`
  Bash args repeated with `[LoopDetection]` warnings then clean termination.
  task_001321 msgs 53-63 — identical `echo ... | extractor` repeated 6x,
  terminated.
- Why Configuration not Control: no code change — the existing processor's
  kwargs are already correct; this candidate only asserts "do not touch".
- Retroactive check (B-preservative-lock): yes — removing the loop detector
  reopens the 98-min `exit_reason=error` runaway that degrades run stability.

## Candidate C-002
[lens: failure | lever: control | intent: corrective]

New `TruncationLoopGuard` processor: detect a run of long, tool-call-free
(token-limit-truncated) assistant turns via a sliding window; inject one strong
"stop narrating, issue ONE Bash command" nudge at the warn threshold and raise
`LoopDetectedError` at the raise threshold. Closes a loop the existing
`LoopDetectionProcessor` cannot see (it fingerprints TOOL CALLS only).

- Tasks affected: task_000958_4bb2b05d, task_001032_1adaccb9,
  task_000010_644ab1c2 (all fail with a no-tool-call truncation thrash).
- Signal: repeated run-loop message "Your previous response was cut off by the
  token limit. Please continue from where you left off." — 17x (task_000958),
  10x (task_001032), 13x (task_000010). Each corresponding assistant turn has
  NO tool call and content pinned at ~1964 chars (the 4096-token output cap).
  Sliding-window(8) count of long-no-tool turns: 7 / 7 / 6 on these three;
  passing tasks top out at 2; the one heavy-but-passing task (task_000740)
  peaks at 4.
- Verified (Read):
  - task_001032 msgs 27,33,37,39,43,45,47,49 — assistant turns, no tool call,
    each 1964 chars, near-identical narration ("The user is right - I've been
    stuck in a loop..."), interleaved with a few tool turns; final pytest:
    "Extracted file /home/user/published/docs/doc1.md is missing" — output
    never written.
  - task_000958 msgs 45-58 — same shape ("The user is telling me I'm stuck in
    a loop and hitting token limits..."), `exit_reason=budget_exceeded`.
  - task_000010 msgs 9-51 — same shape ("The user is right - I've been stuck
    in a loop..."), terminated `loop_detected` only after the tool-call loop
    detector caught an unrelated identical Bash call.
  - The existing `LengthTruncationRecoveryProcessor` corrective nudge
    (`_NUDGE_FIRST`/`_NUDGE_REPEAT`) NEVER appears in any transcript — only the
    passive run-loop text does — so its `finish_reason=="length"` gate is not
    firing in this pipeline. The new guard keys off observable content instead.
- Why Control not Configuration: the gap is a MISSING mechanic, not a mistuned
  knob. `LoopDetectionProcessor` fingerprints tool calls only, so no threshold
  change to it can ever see a tool-call-free loop; and the existing
  length-recovery processor's nudge demonstrably does not reach the model, so
  re-tuning its kwargs would not help. A new `on_after_model`/`on_before_model`
  hook that counts content-truncation turns is required.
- Why Control not Instruction: a system-prompt rule ("don't narrate, act")
  cannot fire mid-loop — the model already narrates "I've been stuck in a loop"
  and keeps narrating; only a runtime intercept that replaces the passive nudge
  with a directive (and hard-stops on no progress) breaks the cycle.
- Retroactive check (A-corrective): partial-yes. Containment (raise=6) reliably
  stops the budget/step burn on all three cited tasks (a stability + cost win
  even if they still score 0). The warn=3 corrective nudge — injected robustly
  regardless of finish_reason, unlike the dead existing nudge — gives the model
  the one directive it never received to stop narrating and write output; this
  is the flip path for task_001032 (which was one write away from doc1.md).
- expected_global_gain: eliminates a 3-task no-tool truncation-thrash cluster
  (10-17 wasted turns each) that is currently invisible to all guards; frees
  ~1500s+ of wall-clock/budget and gives a plausible flip on task_001032-style
  "one write from done" cases via the corrective nudge.
- regression_risk: a false hard-stop on a legitimate long-reasoning task.
  Mitigated: passing tasks peak at 2 long-no-tool turns in an 8-window and the
  heaviest passing task (task_000740) peaks at 4 — a 2-turn margin below
  raise=6. The nudge only replaces an existing trailing user message (contract-
  safe; verified by the auto hook-mutation check).
- cost_shift: strongly negative — truncates 6-17 wasted 4096-token generations
  per affected task; near-zero added cost on passing tasks (one short nudge at
  most, only when the window already shows 3+ truncations).
