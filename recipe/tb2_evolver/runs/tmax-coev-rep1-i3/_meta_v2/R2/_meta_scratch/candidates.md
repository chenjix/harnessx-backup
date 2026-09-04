# Candidates — R2

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Escalate the truncation-recovery processor: after N consecutive
length-truncated no-tool-call turns, mechanically inject a real `Bash`
workspace-snapshot tool call (instead of only a text nudge) so fresh
tool output breaks the pure-reasoning spiral; also rewrite the repeat
text nudge to steer toward small-payload edits rather than a single
large heredoc.

- Tasks affected: task_001032_1adaccb9, task_000740_59416444,
  task_000010_644ab1c2, task_000028_7fe033ac (all failing in R1).
- Signal: run loop injects "Your previous response was cut off by the
  token limit" repeatedly; the R1 v4 `LengthTruncationRecoveryProcessor`
  fires (collapsed assistant turns end with its discard marker) but the
  model keeps emitting pure chain-of-thought that hits the `max_tokens`
  cap with `tool_calls == 0`. Truncation counts: 001032=14, 000740=9,
  000010=4, 000028=4. All four failed; tasks with only 1 truncation
  event (000396, 000578, 001673, 001701) all passed.
- Verified (Read):
  - task_001032 msg 22 assistant content ends with the v4 marker
    "...it has been discarded. Do NOT reconstruct or continue it — start
    the next turn with a command.]", `tool_calls=0`, and the very next
    turn truncates again — 14 times over 76 msgs; budget fully consumed
    (1109s), tar parser never fixed.
  - task_000740 msgs 58-71: repeated `[LoopDetection] exact same tool
    call issued 5..9 times` interleaved with "cut off by the token
    limit"; assistant turns preceding each nudge have `tool_calls=0` and
    end with the v4 marker; `exit_reason=loop_detected`.
  - task_000010 msgs 62-69: agent narrates "I've been stuck in a loop
    trying to solve the import conflict" with `tool_calls=0` truncated
    turns; `exit_reason=budget_exceeded` at 80 steps.
  - task_000028 msgs 26,30,34,54 preceding assistant turns `tool_calls=0`
    ending with the v4 marker.
- Why Control not Instruction: the v4 processor already injects an
  escalating *text* directive ("write NO analysis, emit ONE command")
  and the trajectories show the model reads it and still spirals — a
  reminder the model narrates past is not a fix (same lesson the R1
  lifecycle processor learned). The only thing that reliably shifts a
  verbose model from reasoning to acting is fresh tool output it did not
  author, which requires a mechanical `on_after_model` tool-call
  injection — a Control hook, not a prompt rule. The output-token cap
  (4096) that causes the truncation is set via the `TMAX_MAX_TOKENS`
  env var in `harness_runner.py`, a runtime-only slot outside
  `config.yaml`, so a Configuration knob-tune is not available.
- Retroactive check (A-corrective): yes — on all four tasks the decisive
  blocker is that no tool call ever executes across a long truncation
  streak, so the task state never advances and the budget/loop guard
  ends the run. Injecting a real Bash snapshot at the 3rd consecutive
  truncation lands concrete state in context; the injected tool call
  itself clears the streak and gives the next turn something to react to
  with a small command, which is exactly the forward progress the run
  was starved of. It cannot manufacture the correct answer, but it
  removes the mechanical wall that guaranteed failure.
- expected_global_gain: flips the truncation-spiral sub-cluster that
  currently loses whole-budget runs across `system_administration`,
  `data_processing`, and `data_querying`; generalizes to any task where
  a verbose small model over-reasons past the output cap.
- regression_risk: on a run that reaches the 3rd consecutive truncation,
  one extra Bash round-trip is spent and one user message appended. This
  path is only reachable when the model is already in a failing spiral
  (proven by the passing tasks never exceeding 1 truncation), so
  already-passing clusters are untouched. The snapshot command is fully
  generic (`pwd`/`ls`/`find -mmin -30`/`ps`) with `|| true` guards, so it
  cannot error. Same singleton group `tb2_length_recovery` replaces the
  v4 processor — no double-firing.
- cost_shift: near-zero on healthy runs (escalation never triggers).
  On spiralling runs it *reduces* cost by converting empty 4096-token
  reasoning turns into a bounded snapshot that redirects the model,
  rather than letting the spiral consume the full step/token budget.
- rollback_trigger: if R3 pass_rate is flat/down vs R1 29/50 and the
  four cited tasks still show >2 truncation events each, or if the
  injected snapshot causes any `exit_reason=error` in replay/eval,
  revert to the v4 processor.
