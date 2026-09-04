# Candidates — R3

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Collapse each length-truncated no-tool-call turn to a short head stub with
**no tail** (drop the re-seeding mid-reasoning continuation) and fire the
collapse on *every* ~2000-char truncated turn, not just the largest —
strengthening the R1/R2 `LengthTruncationRecoveryProcessor` (v3 → v4).

- Tasks affected (corrective, all failing in R2 with the same mechanism):
  task_000010_644ab1c2, task_000028_7fe033ac, task_000118_3043e92d,
  task_001031_a8f0eb37, task_001032_1adaccb9, task_001089_220cc46b,
  task_001321_658ce4a8.
- Signal: `exit_reason ∈ {budget_exceeded, done@high-step}`; per-task passive
  "cut off by the token limit" nudge counts 9-20 and harness truncation
  collapses 11-36; `meta.stop_reason == "length"` with `output_tokens == 4096`
  and no tool call on the truncated turns. These 7 tasks account for the
  longest runs of the round (65-92 messages).
- Verified (Read, R2 trajectory bodies):
  - task_000010_644ab1c2 oh_runs trace: steps 50/53/55-59/61/67-69 all
    `stop_reason=length, output_tokens=4096`, no tool call; input_tokens climb
    24625 → 40018 as full ~2000-char reasoning turns accumulate; the persisted
    user message between them is still the plain passive nudge.
  - task_001032_1adaccb9.messages.json steps 35/37/39/41: assistant reads the
    v3 nudge ("The user is telling me to stop the repetitive analysis and just
    run a single Bash command. I need to write at most TWO short sentences...")
    then spends the whole 4096-token turn narrating ("Wait, I think I see the
    issue now! Let me trace through the code more carefully: 1. ... 2. ...")
    and is truncated again before emitting a tool call. The v3 collapse
    preserved a 600-char tail that is exactly this continuation, re-seeding the
    spiral on the next turn.
  - task_000028 / 000118 / 001031 / 001089 / 001321: same shape — 10-20 passive
    nudges, 11-36 collapses, verbose reasoning turns truncated at the 4096-token
    cap with no tool call.
- Why Control (tune+strengthen the existing processor) not Configuration-only:
  the fix is not just a knob flip — v4 changes the collapse *shape* (head-only,
  no re-seeding tail) and lowers the effective collapse threshold so it fires on
  every truncated turn; that is behavioural, i.e. a Control edit to the
  processor body, expressed through its head/tail kwargs.
  Why not Instruction / raising max_tokens: the model already *receives and
  acknowledges* the actionable nudge (body quote above) but its reasoning
  genuinely exceeds the 4096 output-token cap, so a louder prompt cannot make it
  self-limit; and `max_tokens` is a runtime-only slot injected by
  harness_runner (env `TMAX_MAX_TOKENS`), outside the editable config surface.
  The only lever left that materially changes the loop dynamics is mechanical:
  stop the model's own unfinished spiral from being re-fed to it.
- Retroactive check (A-corrective): yes — if each truncated turn had been
  collapsed to a short head with no continuation tail, the accumulated context
  would stay small (no 24k→40k climb) and the model would not re-read and
  extend the same unfinished thought each turn, converging to a tool call
  within its 4096-token budget before the step cap. The tasks that already
  emit tool calls when they don't over-think (task_000010 steps 60/62/63,
  output 1039-1450 tokens < 4096) show a short-context turn fits.

- expected_global_gain: Rescues the truncation-spiral cluster (7 failing tasks,
  the round's biggest step-burners incl. all remaining `budget_exceeded`
  cases). R1's v3 already flipped 2 of the original cluster (740, 396) with the
  nudge alone; v4 adds the missing mechanical half (kill the re-seeding tail +
  fire on every turn) that the R2 bodies show was the residual blocker.
  Generalises to any max_tokens truncation-spiral, model-agnostic.
- regression_risk: Low. The processor only acts on no-tool-call turns with
  `finish_reason == "length"` (or ≥40000-char content) — a state the 29
  passing R2 tasks never reach (their turns carry tool calls and are far under
  4096 output tokens). The `on_before_model` path is byte-identical to v3
  (already accepted in R1/R2, contract-clean). Worst case a truncated turn's
  head loses slightly more context than v3, but the discarded material was the
  reasoning that caused the truncation, not tool output.
- cost_shift: Net reduction. Collapsing every truncated turn to ~400 chars
  (from ~2000) and killing the accumulating tail cuts input-token growth on the
  most expensive runs (the 65-92-message spirals) and breaks them earlier,
  reducing both tokens and wall-clock. Passing tasks unaffected.
- rollback_trigger: If R4 pass_rate does not improve AND the truncation cluster
  (010/028/118/1031/1032/1089/1321) still shows ≥8 passive nudges per task with
  no flips, the binding constraint is the model's reasoning-length habit, not
  the harness loop — revert to R2's v3 processor and look upstream.
