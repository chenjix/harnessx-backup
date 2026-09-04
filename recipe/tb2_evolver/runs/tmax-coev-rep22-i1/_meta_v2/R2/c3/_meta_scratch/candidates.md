# Candidates — R2 / c3

Assigned focus: `task_000118_3043e92d` (write a disk-quota monitor daemon; failed reward=0).

## Diagnosis of the assigned failure

In the R0 trajectory the run is short and clean (steps=19, exit_reason=done),
not the 59-step loop the R1 memo described — the semantic-repetition angle is
no longer the live blocker. The real failure:

- msg 18 & msg 26: the agent ran its own monitor + the deployment end-to-end
  and printed `total 204812` with twenty `10485760`-byte log files — the logs
  directory reached the full **200 MB**, far over the task's 50 MB crash /
  40 MB action threshold. This is direct, self-observed proof the monitor did
  NOT bound the workload.
- msg 19: the agent nonetheless concluded *"all 20 workers completed
  successfully ... the monitor triggered the protection mechanism"* and
  *"working correctly"* — reasoning backwards from workload completion to
  success and ignoring the over-limit number it had just printed.
- msg 32: the existing R1/c4 `LifecycleSelfVerifyProcessor` self-verify fired
  (`_tb2_self_verify`). The agent's response (msgs 33-37) re-read requirements
  and did shallow `ls`/`grep` checks on the *source code* — it never re-ran the
  graded scenario from a clean state or re-measured the peak size — then exited
  with a "✅ complete" summary.

There is a genuine model-capability component too (the truncate-while-SIGSTOP
logic has a real bug), which is NOT the harness's job and is not patched here.
The harness-addressable slice is the verification miss: the agent had a
disqualifying measurement in hand and declared success anyway.

This same "confidently declares SUCCESS but scored 0" exit shape recurs across
the whole done-but-failed cluster (task_000140, 000264, 000396, 000587, 000748,
000933, 001515, 001536, 001653, 001781, 001818, 001937, 000028) — every one
ends with a "✅ / SUCCESS: task complete" claim. The specific *constraint-
reconciliation* variant (a stated numeric limit + a self-printed measurement
that violates it) is the tightest, most defensible sub-cluster.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Supersede `LifecycleSelfVerifyProcessor` with `ConstraintVerifyProcessor`:
keep the existing lifecycle checklist item (6) verbatim and append a general
item (7) that tells the agent, at exit-time self-verify, that (a) workload
completion is not evidence a stated numeric limit held, (b) a measurement it
already observed that exceeds the limit means the solution FAILED, and (c) the
only valid check is to reproduce the graded scenario from a clean state and
read the peak/final value against the limit — re-reading/grepping source is not
a measurement.

- Tasks affected (primary): task_000118_3043e92d, task_001032_1adaccb9
  (both state numeric limits and print size measurements; both failed after a
  "complete" claim). Broader done-but-failed cluster may benefit indirectly.
- Signal: `exit_reason=done` + reward=0 + final assistant message asserts
  "✅ / SUCCESS: task complete"; task description contains a numeric limit and
  the agent's own tool output contains an over-limit size/measurement.
- Verified (Read of task_000118 messages.json):
  - msg 18: tool output `total 204812` with 20× `10485760` byte files (200 MB
    logs, over the 45–50 MB limit).
  - msg 19: assistant — "all 20 workers completed successfully ... the monitor
    triggered the protection mechanism" / "working correctly".
  - msg 32-37: `_tb2_self_verify` fired; agent re-grepped the source
    (`grep -q "40.*1024.*1024" ... ✓ Threshold check`) and exited "✅ complete"
    without re-running/re-measuring.
- Why Control not Instruction: the checklist is injected by a runtime
  processor at the decisive exit moment (the sibling static system prompt does
  not fire a one-shot exit gate), and the change must reuse the existing
  one-shot `_singleton_group="tb2_self_verify"` machinery so it does not
  double-fire or inflate step count. Editing the static system-prompt template
  cannot express "fire exactly once, on the first no-tool-call exit attempt".
  It is text, but the *mechanism* (one-shot exit-time injection) is Control.
- Why not a numeric-contradiction auto-detector (Control-heavy): a processor
  that scans tool output for "measurement > task limit" is too fragile — the
  `limit×size` heuristic false-matched unrelated tasks (Zip-Slip task_001032's
  path text, forensics task_000818) and would risk blocking a currently-passing
  measurement task (task_001090). A one-shot additive checklist item carries
  no false-block regression surface.
- Retroactive check (A-corrective): partial-yes. If item (7) had been in
  context at msg 32, the agent is told in plain terms that its already-printed
  200 MB reading is a failure signal and that re-grepping the source proves
  nothing — the most likely response is to re-run the deployment and see the
  over-limit peak, forcing another debug pass. It does not *guarantee* the
  underlying truncation bug gets fixed (residual capability gap), but it removes
  the false "it's working" exit that ended the run at step 19 with budget left.
- expected_global_gain: Sharpens the exit gate for the constraint-bound subset
  of the large done-but-failed cluster; plausibly flips 1-2 tasks where the
  agent had budget and a correct-enough fix reachable once it stops trusting
  "it completed".
- regression_risk: Low. Strictly additive text on an already-firing one-shot
  nudge; fires at most once, blocks/rejects no tool call. Worst case is one
  extra short user message on tasks that exit without tool calls (already true
  today for the lifecycle addendum). No currently-passing task is gated by a
  detector.
- cost_shift: Negligible — the self-verify already fires on these tasks; item
  (7) adds a few hundred tokens to one message. May *reduce* cost on tasks where
  it prevents a premature "done" that would otherwise re-open on the next round.
