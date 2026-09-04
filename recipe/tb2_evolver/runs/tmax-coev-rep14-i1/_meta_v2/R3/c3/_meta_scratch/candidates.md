# Candidates — R3 c3 (focus: task_000118_3043e92d)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `StuckTruncationEscalator`: after N consecutive **byte-identical
length-truncated** assistant turns (the passive length-recovery nudge
demonstrably not landing), escalate ONCE to a hard directive to abandon
narration and issue a single Bash command that writes the deliverable
and/or executes it end-to-end against the real inputs.

- Tasks affected: task_000118_3043e92d, task_000010_644ab1c2
- Signal: `exit_reason=done`/`finished=no_tool_calls` at high step counts
  (118: 79 steps; 010: 65 steps) with a run dominated by repeated
  `finish_reason=length` truncations emitting identical assistant content.
- Verified (Read):
  - task_000118 messages.json: 14 "cut off by the token limit. Please
    continue" user nudges; the assistant re-emitted the identical
    "The user is telling me to stop the repetition and just take action…"
    paragraph ~10x (Counter over assistant contents shows the same text
    at counts 4/3/3). The existing `LengthTruncationRecoveryProcessor`
    fired repeatedly (its `_NUDGE_REPEAT` is what the model is quoting
    back) yet the loop never broke; ~50 of 79 steps consumed. At the
    forced self-verify (msg 59) the agent re-read the source (msg 63) and
    declared "looks correct" (msg 64) — it NEVER once ran
    `run_deployment.sh` end-to-end, so the 200 MB peak (final_pytest:
    `Peak log directory size was 209715200 bytes … exceeds 45000000`) was
    never observed.
  - task_000010 messages.json (last turns): identical shape — repeated
    "The user is right - I've been stuck in a loop… Please continue"
    truncation cycle; deliverable never finalized/exercised.
- Why Control not Configuration: the incumbent knob
  (`LengthTruncationRecoveryProcessor.repeat_threshold`) already escalates
  its *wording* on repeats, and it still fired 14x here without breaking
  the loop — re-tuning that knob cannot change the mechanism (a passive
  text nudge the model ignores). The fix is a distinct hook that, on
  demonstrated non-landing, redirects the model from narration to a
  concrete write/execute action — new mechanical behaviour, not a param.
- Why Control not Instruction: a system-prompt rule cannot fire
  *conditionally at the moment the loop is detected*; it would sit inert
  in every prompt while the model is already ignoring the runtime nudge.
  The escalation must be a runtime hook keyed on the observed truncation
  run.
- Distinct from pending R1-c5 `h_stuck_result_breaker_v1` (identical tool
  *result* breaker) and R2 `h_output_contract_verify_v1` (output-format
  audit): this keys on identical assistant *content across length
  truncations* — a different signal, a different hook, and it escalates to
  a write/execute directive rather than a note on a tool result. Not the
  reverted `h_step_budget_reminder_v1` (step-fraction reminder).
- Retroactive check (A-corrective): yes — had the escalation fired at the
  3rd identical truncation (~step 6 in 118, well before the ~50-step loop),
  the model would have been redirected to write the script and RUN it
  against the real `run_deployment.sh`, surfacing the >45 MB peak with
  ample budget left to fix the monitor logic instead of exiting `done` on
  an unexecuted deliverable.

- expected_global_gain: Relieves the verbatim-length-truncation loop
  cluster (>=2 system_administration tasks, 118 + 010) where the passive
  recovery nudge fails to break the loop and the deliverable is never
  exercised end-to-end. Reclaims tens of steps per stuck task and pushes
  the model toward real execution before a voluntary exit.
- regression_risk: Low. Inert on any task with no repeated identical
  length truncation (regex-free, fires only on hashed content-identity
  across `finish_reason=length` turns); append-only on the message list
  (+0 when last role is user, else +1), one-shot per run, never
  terminates, never rewrites/drops messages, no system-prompt mutation.
  Worst case: one extra user message on a task that legitimately truncated
  the same content 3x — which is itself a stuck signal, not a healthy one.
- cost_shift: Net decrease — breaks the loop at the 3rd repeat instead of
  letting it run toward the step wall (118 burned ~50 steps looping);
  the directive is ~120 tokens and fires at most once per run.
