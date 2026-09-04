# Candidates — R9

## Candidate C-009
[lens: failure | lever: control | intent: corrective]

Add a durable `on_step_start` processor (`TruncationNudgeEscalator`) that,
once the assembled window holds ≥8 passive "cut off by the token limit"
nudges, drops all but the most-recent passive nudge, replaces it with one firm
terseness directive, and head+tail-trims over-long truncated assistant
narration turns — including ones that carry a tool call, which the existing
consecutive-only compactor cannot touch.

- Tasks affected (failing, interleaved truncation loop, all budget_exceeded @ 80, reward 0):
  task_001267_0acfd3a0, task_000713_b0778d60, task_001214_f44c0aa2,
  task_001028_5bc8bc70, task_000097_d9d1d187, task_000796_828a72cf,
  task_000348_31fb8c8a, task_000910_16cc0daf, task_000908_170e5e4e,
  task_000032_3fb303f6.
- Signal: `messages.json` scan — count of user messages containing
  "cut off by the token limit" (the run loop's passive length-truncation
  nudge). Failing cluster carries 8–23 of them each; the max on ANY passing
  task is 6 (task_000628, PASS). The truncated assistant turns are interleaved
  with tool results / PostCompaction messages, so the existing
  `TruncationLoopCompactor` (needs ≥3 CONSECUTIVE no-tool-call truncated turns)
  never fires on them (its passive-nudge cleanup never triggers), and the R8
  `IdenticalCommandLoopCompactor` (needs ≥10 byte-identical Bash commands) also
  never fires (the commands vary).
- Verified (Read, task_000097_d9d1d187 messages.json): turns 10,14,16,22,26,30,
  34,38,42,54,59,63,67 are user messages "Your previous response was cut off by
  the token limit. Please continue…" (13 total). Assistant turns 15/21/25/29/33/
  37/41 repeat the identical prefix "The service is working correctly based on
  the log output…" / "The Python script is not receiving any data. This is
  strange…"; interleaved tool-call turns (11,17,23,27,31,…) break every
  consecutive run so the R7 compactor's `_is_truncated_narration` (requires
  `not tool_calls`) never assembles a ≥3 run. task_001267_0acfd3a0: 23 passive
  nudges, budget_exceeded @ 80. task_000713_b0778d60: 17 passive nudges,
  max assistant-prefix run 5, budget/exit-wrong. task_001214_f44c0aa2: 17
  nudges. task_001028_5bc8bc70: 18 nudges, assistant narrates "I've been stuck
  in a loop." Root cause verified in source: `LengthTruncationRecoveryProcessor`
  rewrites the nudge in `on_before_model` (ephemeral — never persisted to
  `state.raw_messages`), so the passive wall keeps growing.
- Why Control not Instruction: the model already NARRATES that it is stuck
  ("The model is stuck in a loop. I need to break out…") yet cannot self-break —
  a prompt rule the model is already failing to self-enforce will not help. The
  loop is fed by a mechanical artifact (the persisted passive-nudge wall +
  runaway prose priming re-generation) that only a processor mutating the
  durable history can remove. Distinct from R7 (consecutive-no-tool-call
  collapse) and R8 (byte-identical Bash collapse): this targets the CUMULATIVE
  passive-nudge count across interleaved tool calls, the exact case both miss.
- Why Control not Configuration: no existing knob expresses "cumulative passive
  nudges across interleaved tool calls"; the R7 compactor's `min_run` only
  counts consecutive no-tool turns, so lowering it would still miss the
  interleaved shape and would risk firing on legitimate short iteration.
- Retroactive check (A-corrective): yes — with the passive-nudge wall removed
  and one firm terseness directive persisted (durably, via the SegmentBoundary
  path), the model at task_000097's decisive step is no longer primed by 13
  "please continue" prompts to re-narrate; the trimmed context + short-command
  directive gives it a fair chance to run the concrete test it kept
  half-attempting. Necessary-not-always-sufficient (some tasks also carry a
  correctness gap), but it converts a guaranteed budget-burn-in-a-loop 0 into a
  fair scored attempt at lower cost, on a 10-task cluster.
- expected_global_gain: recovers dozens of wasted steps across a 10-task
  interleaved-truncation-loop cluster (multiple domains: services, decompilation,
  bisect, OCR) that all die budget_exceeded @ 80; generalizes to any future task
  that falls into the interleaved max_tokens loop (structural trigger, no
  literals).
- regression_risk: near-zero — fires only at ≥8 passive nudges; the passing
  set's max is 6 (task_000628), so it provably cannot fire on any currently-
  passing R8 trajectory. Never blocks/drops/fabricates a tool call; only trims
  runaway prose and dedupes the passive-nudge wall, ending on one user directive.
- cost_shift: net down — collapsing the passive-nudge wall and trimming runaway
  prose shrinks the assembled prompt every subsequent step, and the terseness
  directive shortens the loop so tasks stop burning the full 80-step budget.
- rollback_trigger: if R10 shows any previously-passing task regressing T→F with
  the firm-directive marker ("your responses have repeatedly hit the output
  token limit") in its trace disrupting legitimate work, or pass_rate drops vs
  the R7/R8 incumbent mean, drop the TruncationNudgeEscalator registration and
  keep the R8 pipeline.
