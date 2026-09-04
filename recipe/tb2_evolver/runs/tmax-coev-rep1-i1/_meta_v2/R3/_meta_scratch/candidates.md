# Candidates — R3

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `RepeatedActionBreakProcessor`: on ≥5 consecutive identical tool-call
turns, inject one escalating **user-turn** directive that names the repeated
command and forbids re-issuing it, breaking the "echo trap" the existing
tool-result loop warning cannot break.

- Tasks affected (failing, same mechanism): task_000396_e56917e2,
  task_001321_658ce4a8, task_000264_ab8c7253 (all reached 10–11 verbatim
  identical tool-call repeats in R2; secondary: task_001031_a8f0eb37 maxrun=11).
- Signal: exact-fingerprint consecutive-repeat run of 9–11 identical
  assistant tool calls; `exit_reason=loop_detected` on 000264/000396/001321;
  each looping assistant turn's prose acknowledges the loop
  ("The user is right - I've been repeating myself", "let me take a
  fundamentally different approach") yet the next turn re-emits the identical
  command. The in-tree LoopDetectionProcessor's warning is *appended to the
  tool result* and is verbally agreed-with but not acted on.
- Verified (Read of messages.json):
  - task_000396_e56917e2 steps 61→91: the model writes the exact same
    `cat > /app/lib-rk45/rk45.c << 'EOF' …` heredoc ~10 times in a row, each
    time prefaced by "Let me take a fundamentally different approach" /
    "I've been stuck in a loop editing the same file", producing byte-identical
    content. It had already articulated concrete alternative plans
    ("use a fixed step size of 0.01") at step 51 but never executed them —
    the echo cadence reproduced the same generation instead.
  - task_001321_658ce4a8 tail: identical `#include <stdio.h> … int main()`
    C-file rewrite repeated verbatim while narration says "I've been stuck in
    a loop … take a fundamentally different approach".
  - task_000264_ab8c7253 tail: identical recursive-CTE query re-issued at
    LoopDetection counts 7,8,9 with the same "I'm stuck in a loop. Let me try
    a fundamentally different approach" prose each time.
- Why Control not Configuration: lowering the existing
  LoopDetectionProcessor.threshold (a Configuration tweak) only makes the
  *hard raise* fire earlier — it terminates the run with the wrong answer
  already committed and flips nothing; it is a pure cost change. The body
  evidence shows the existing warning channel (append-to-tool-result) is read
  and agreed with but ignored. The corrective lever is a *different delivery
  channel* — a user-turn directive at the tail of the prompt that breaks the
  assistant→tool→assistant echo cadence — which no knob on the existing
  processor exposes. This is the same channel that made R1's length-recovery
  nudge effective against the max_tokens variant of the same degeneracy.
- Why Control not Instruction: the failure is a mechanical context-pollution
  echo that recurs uniformly across tasks; it must fire dynamically at the
  moment the loop forms, keyed on the runtime repeat count. A static system-
  prompt rule ("don't repeat commands") is already effectively present via the
  existing loop warning and is demonstrably ignored in-context — the model
  cannot self-apply it once trapped. A runtime hook that injects a fresh,
  unburiable user message at exactly the repeat boundary is the only lever that
  reaches the trapped generation.
- Retroactive check (A-corrective): yes — in 000396 and 001321 the model had
  already stated a concrete alternative approach before the echo trap closed;
  a forceful user turn that (a) evicts the "re-run the same thing" reflex by
  naming and forbidding the exact command and (b) lands at the end of the
  prompt would have freed the next generation to execute the stated
  alternative, before the wrong file was the committed final state. Where the
  underlying task is a genuine capability gap the change is at worst a cost
  win (fewer wasted identical steps) — never a regression.
- expected_global_gain: closes the dominant remaining harness-shaped failure
  after R1/R2 — degenerate exact-repeat loops across ≥3 domains
  (scientific_computing, data_processing, data_querying). Plausibly flips
  000396 / 001321 (model had a live alternative plan) and reduces wasted budget
  on 000264 / 001031.
- regression_risk: Very low. The only passing R2 task with any identical
  repetition (task_001536_acfe6c35) topped out at a run of 4; repeat_threshold=5
  sits strictly above it, so no passing task is perturbed. The nudge is a single
  +1 user-message insertion (contract-verified), fires only inside a proven
  degenerate loop, and resets on any non-identical or no-tool-call turn.
- cost_shift: Negative (savings). Six R2 tasks burn 9–11 identical steps plus
  the wall-clock of re-running the same command (001031 = 2579s, the round's
  most expensive task); breaking the loop 4–6 steps earlier trims the most
  expensive tail. Worst case (nudge ignored) it adds one short user message per
  fire — negligible.
- rollback_trigger: If R4 pass_rate is flat/down AND task_001536_acfe6c35
  regresses to F, or any previously-passing task newly exits
  loop_detected/max_steps after a repeat_break nudge fires, revert or raise
  repeat_threshold.
