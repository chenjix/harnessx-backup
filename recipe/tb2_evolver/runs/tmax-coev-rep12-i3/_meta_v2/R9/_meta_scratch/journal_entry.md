
## Round 9 — defuse interleaved max_tokens truncation loop

<!-- journal:frontmatter
round: 9
timestamp: 2026-04-28T00:00:00Z
hypothesis_id: h_truncation_nudge_escalator_v1
levers: [control]
predicted_affected: [task_001267_0acfd3a0, task_000713_b0778d60, task_001214_f44c0aa2, task_001028_5bc8bc70, task_000097_d9d1d187, task_000796_828a72cf, task_000348_31fb8c8a, task_000910_16cc0daf, task_000908_170e5e4e, task_000032_3fb303f6]
cited_candidates: [C-009]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Recovers dozens of wasted steps across a 10-task INTERLEAVED-truncation-loop cluster (services, decompilation, git-bisect, OCR) that all die budget_exceeded at 80 steps; generalizes to any future task that falls into the interleaved max_tokens loop (structural trigger, no literals)."
regression_risk: "Near-zero: fires only when the assembled window holds 8-or-more passive 'cut off by the token limit' nudges; the passing set's max is 6 (task_000628 PASS), so it provably cannot fire on any currently-passing R8 trajectory. Never blocks/drops/fabricates a tool call; only trims runaway prose and dedupes the passive-nudge wall, ending on one firm user directive."
cost_shift: "Net down: collapsing the passive-nudge wall and trimming runaway prose shrinks the assembled prompt each subsequent step, and the terseness directive shortens the loop so tasks stop burning the full 80-step budget."
rollback_trigger: "If R10 shows any previously-passing task regressing T-to-F with the firm-directive marker ('your responses have repeatedly hit the output token limit') in its trace disrupting legitimate work, or pass_rate drops vs the R7/R8 incumbent mean, drop the TruncationNudgeEscalator registration and keep the R8 pipeline."
-->

### Why

R8 = 13/50. No exit_reason=error remain (R1 fix holds); the byte-identical
Bash loop (R8) and the consecutive-no-tool-call narration loop (R7) are both
covered. The dominant UNCOVERED harness-shaped failure is an INTERLEAVED
max_tokens truncation loop: the eval model runs under a hard max_tokens=4096
output cap, emits ~4096 tokens of prose (often with a partial tool call), gets
finish_reason=length, the run loop appends the passive "cut off by the token
limit ... please continue" nudge, a tool result lands, and the model repeats —
accruing 8-23 passive nudges per run, interleaved with tool calls and
PostCompaction messages. Neither existing compactor fires: R7's
TruncationLoopCompactor requires 3-or-more CONSECUTIVE truncated turns with NO
tool call (a single interleaved tool-call turn breaks the run), and R8's
IdenticalCommandLoopCompactor requires 10-or-more byte-identical Bash commands
(the commands vary). Meanwhile LengthTruncationRecovery's firm nudge is written
in on_before_model and is EPHEMERAL — it never lands in state.raw_messages, so
the passive-nudge wall keeps growing and re-primes the runaway generation every
step until budget_exceeded at 80. This is a mechanical artifact the model
narrates but cannot self-break; a durable Control processor is the fix.

### Changes

- `processors/truncation_nudge_escalator.py` — new MultiHookProcessor
  TruncationNudgeEscalator (on_step_start, order 22, after the R7/R8
  compactors). When the assembled window holds nudge_threshold-or-more passive
  "cut off by the token limit" nudges, it drops every passive nudge except the
  most recent, replaces that one with a single firm terseness directive, and
  head+tail-trims over-long truncated assistant narration turns WHETHER OR NOT
  they carry a tool call (tool calls preserved verbatim; only runaway prose
  shrinks). Runs at on_step_start so the edit changes history_hash and the run
  loop auto-generates a SegmentBoundary that writes the trimmed window durably
  into state.raw_messages/state.messages (runloop.py ~345-368) — the same
  durable path the R7/R8 compactors use. Never blocks/drops/fabricates a tool
  call; structural trigger only, no task ids/paths/commands.
- `config.yaml` — copied R8 byte-for-byte; registered the new processor after
  IdenticalCommandLoopCompactor with nudge_threshold=8 (safely above the
  passing set's max nudge count of 6). system_prompt.txt copied byte-for-byte
  from R8.

### Evidence

- task_000097_d9d1d187 messages.json: 13 passive "cut off by the token limit"
  nudges (turns 10,14,16,22,26,30,34,38,42,54,59,63,67); assistant turns repeat
  the identical prefix "The service is working correctly..." / "The Python
  script is not receiving any data. This is strange..."; interleaved tool-call
  turns (11,17,23,27,31,...) break every consecutive run so R7's compactor never
  assembles a 3-or-more run. budget_exceeded at 80.
- Passive-nudge counts on failing budget tasks: task_001267_0acfd3a0=23,
  task_001028_5bc8bc70=18 ("I've been stuck in a loop"), task_000713_b0778d60=17,
  task_001214_f44c0aa2=17, task_000796_828a72cf=11, task_000032_3fb303f6=10,
  task_000348_31fb8c8a=10, task_000910_16cc0daf=9, task_000908_170e5e4e=8.
- Passing set max passive-nudge count = 6 (task_000628_dcc7eb30, PASS); all
  other passers 0-1. nudge_threshold=8 sits above the passing max, so the
  trigger is provably absent from the passing set.
- Root cause verified in source: LengthTruncationRecoveryProcessor rewrites the
  nudge in on_before_model (ephemeral, never persisted); TruncationLoopCompactor
  `_is_truncated_narration` returns False when tool_calls is set; runloop.py
  345-368 confirms an on_step_start message mutation triggers an auto
  SegmentBoundary that durably rewrites state.raw_messages/state.messages.

### Uncertainty

Breaking the loop is necessary-not-always-sufficient: the freed steps may still
end in a content-correctness gap (several of these are hard service-debugging /
reverse-engineering problems). The guaranteed win is converting budget-burned-
in-an-interleaved-loop into a fair scored attempt at lower cost, on a 10-task
cluster the two existing compactors provably miss. Distinct mechanism and
distinct hypothesis id from R7 (consecutive-no-tool collapse) and R8
(byte-identical Bash collapse), and from the reverted R5 blocker (which blocked
execution). If any passer regresses with the firm-directive marker in its
trace, revert per the rollback trigger.
