# Candidates

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Register the builtin `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
(exact-fingerprint Strategy 1: warn at 3 consecutive identical tool calls,
raise `LoopDetectedError` at 5) into the pipeline — the current config has
no exact-duplicate loop guard at all.

- Tasks affected: task_000015_89886d8d, task_001031_a8f0eb37
- Signal: `exit_reason=budget_exceeded` (task_000015, 80 steps) and
  `exit_reason=error` (task_001031, 42 steps); both fail with zero required
  deliverables produced. Behaviour signal: the agent emits **byte-identical**
  Bash tool-call arguments in a long consecutive run.
- Verified (Read of `.messages.json`):
  - task_000015_89886d8d: 33 assistant tool calls, **all 33 byte-identical**
    (`distinct=1`, longest consecutive identical run = 33). Every call is the
    same `python3 << 'EOF' ... Image.open('/app/routing_schema.png') ...` OCR
    heredoc; every tool result is the same garbled "Legacy toV2 Schema Mapping
    1 eatalogyitemyitem_id> ...". Narration msg 2/4/.../68 is verbatim
    "The OCR is consistently garbled. Let me try a different approach ...".
    The agent had already inferred a plausible schema in msg 2 but never
    stopped OCRing to write `/home/user/migrate.py` or `/home/user/test_parser.py`.
  - task_001031_a8f0eb37: 42 tool calls, one command repeated **16 times
    consecutively** (longest consecutive identical run = 16), ending
    `exit_reason=error`, reward 0.
- Why Configuration not Control: the exact mechanism needed already exists
  as a battle-tested builtin (`LoopDetectionProcessor`, two-phase warn/raise,
  compaction-aware fingerprint reset). Authoring a new Control processor would
  duplicate it and add regression surface. This is purely a "the right knob/
  component is not wired in" gap → Configuration (add existing processor with
  default kwargs). It is a *different mechanism* from R1's accepted
  `SemanticRepetitionBreaker` (Jaccard token-overlap on narration) — that one
  targets varying-argument semantic loops and is not even present in the R1
  config in play here; this targets exact byte-identical tool-call loops.
- Why not the semantic breaker again: the R1 semantic breaker keys on
  narration Jaccard and would also fire here, but (a) it is not in this config,
  and (b) exact-fingerprint detection is strictly cheaper and zero-false-
  positive on identical args — the correct primitive for `distinct=1` loops.
- Retroactive check (A-corrective): yes. With warn_threshold=3, a redirect
  ("you are stuck in a loop; try something fundamentally different") is
  injected into the tool result after the 3rd identical call — ~5 steps in,
  with ~75 steps of budget still available and the schema already inferred in
  narration, giving a real path to writing the two deliverable files. Even if
  the model ignores the nudge, `threshold=5` raises `LoopDetectedError` and
  exits cleanly at step ~5 instead of burning 80 (task_000015) / erroring at
  42 (task_001031), reclaiming budget for the rest of the round.
- expected_global_gain: closes the exact-duplicate-loop failure cluster
  (≥2 tasks: task_000015, task_001031). Generalizes to any task where the
  agent gets stuck repeating a byte-identical Bash call against a stable
  (garbled/failing) output — the cheapest, highest-precision loop shape.
- regression_risk: Strategy 1 requires 5 *consecutive byte-identical* calls to
  raise; legitimate exploration varies arguments and breaks the run counter,
  so false-positive termination is near-zero. Strategy 2 (name-only) is
  warn-only and never raises. Offline: R1's own replay of a similar detector
  raised on 0/30 passing tasks. Only plausible risk: a task that genuinely
  needs to poll an identical command ≥5× in a row (rare; a poll loop normally
  varies output, not the guard's raise condition, which is on the *call*, but
  identical calls still trip it) — mitigated because warn fires first at 3 and
  the agent can vary its command.
- cost_shift: net negative. Terminates 80-step / 42-step non-converging loops
  at ~step 5; adds at most one short warning string appended to a tool result.
