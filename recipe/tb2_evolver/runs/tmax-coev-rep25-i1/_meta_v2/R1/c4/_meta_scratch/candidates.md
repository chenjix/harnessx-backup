# Candidates — R1 c4 (focus: stopping and verification)

Anchor: `task_000140_01c78b42` (system_administration, reward=0,
finished=`no_tool_calls`, 75 steps / 749s). Final assistant turn was a
text-only message from a repetition loop; the agent exited without
verifying end state. Verifier then failed on a lingering-service check
(`Lingering vm_service processes found: [...]`) and a re-run that hit
`address already in use` — classic "declared done without verifying the
end state" failure.

## Failure class (observable, task-agnostic)

The run loop treats an assistant turn with `finish_reason in {stop,
end_turn}` and **no tool calls** as done. Weak models exit on such a
turn while merely *asserting* completion, or while stuck repeating text,
without executing objective verification of the deliverable.

Cluster evidence (all reward=0, finished=`no_tool_calls`), body-quoted
final assistant turns assert success but the external verifier failed:

- `task_000933_1f27096a`: "Double precision output: `10.8` ✓ / Integer
  precision output: `10` ✓ / **All tests passed ✓**" — reward=0.
- `task_000505_50b5162d`: "The task is complete. I have: ... verified it
  works correctly with test files. All requirements have been met." —
  reward=0.
- `task_000264_ab8c7253`: "### 6. Final Output / Saved results ... All
  operations were completed ..." — reward=0.
- `task_001264_9f4ca84a`: "Created `/home/user/repo_summary.txt` with the
  exact required format ..." — reward=0.
- `task_000118_3043e92d`: "The task is complete. I have created the
  `/home/user/deployment_monitor.py` script that successfully ..." —
  reward=0.
- `task_000140_01c78b42` (anchor): final turn is a repetition-loop text
  block ("I keep hitting the token limit ... I need to stop repeating
  the same command") with no tool call → exit; verifier found lingering
  processes + bound port.

17 of 25 failures are `finished=no_tool_calls` (vs 6 budget_exceeded, 2
error). The passing tasks *also* exit via `no_tool_calls` but after
genuinely running verification commands — so the gate must not penalise
agents that actually verify.

Counter-evidence considered: the stock pipeline already has
`CustomSelfVerifyProcessor`, a one-shot checklist nudge. It fired on
these tasks yet they still failed, because (a) it fires at most once, so
a second bare claim or a later stuck-loop text turn exits unchecked, and
(b) it cannot tell an agent that actually ran verification commands from
one that merely re-asserted success. This is a mechanism deficiency, not
a model capability gap: the model *can* run `ls`/`cat`/re-run tests, it
just exits before doing so.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the stock one-shot `CustomSelfVerifyProcessor` with a bounded,
work-aware completion gate (`IterativeVerifyGate`, `max_nudges=2`) that
re-intercepts a no-tool-call exit ONLY when the agent tried to exit
without having run a real verification command since the previous nudge.

- Tasks affected (corrective, ≥2 distinct, same mechanism):
  `task_000140_01c78b42`, `task_000933_1f27096a`, `task_000505_50b5162d`,
  `task_000264_ab8c7253`, `task_001264_9f4ca84a`, `task_000118_3043e92d`.
- Signal: `finished=no_tool_calls` + reward=0 with a final assistant turn
  that asserts completion ("All tests passed ✓", "task complete") or is a
  repetition-loop text block, while the external verifier fails on the
  end state (missing/incorrect output, lingering processes, bound ports).
- Verified (Read of `.messages.json` final assistant turns): quoted above
  per task — each ends in a bare completion assertion / stuck-loop text
  with `tool_calls: None`.
- Why Control not Instruction: the model already *knows* it should verify
  (the stock checklist is a prompt-level instruction and it ignored / did
  not act on it because nothing forced a second look). The gap is
  mechanical — the loop exits on the first no-tool-call turn regardless of
  whether real verification work happened. Only a hook around the loop can
  observe "exiting without having run a command since the nudge" and hold
  the loop open. A prompt rule cannot condition on that runtime state.
- Why Control not Configuration: the stock processor exposes no knob to
  fire more than once or to condition on tool-call activity; the needed
  behaviour (work-aware, bounded re-nudge) is new logic, not a parameter.
- Retroactive check (A-corrective): yes — on the cluster the agent had
  the capability to verify and fix (weak model, but `ls`/`cat`/re-run are
  trivial Bash). A second, work-conditioned nudge forces the agent that
  just re-asserted success to actually run the checks; the anchor's
  stuck-loop exit is held open for at least one more corrective attempt
  instead of terminating silently. Not every task will flip (some are
  genuine capability gaps), but the mechanism gives the recoverable
  subset a real chance it currently never gets.
- expected_global_gain: targets the largest failing cluster (17/25
  `no_tool_calls` fails). Even a modest flip rate on the recoverable
  subset (premature-exit, stuck-loop-giveup) is net positive; generalises
  because the trigger is pure runtime state, not task content.
- regression_risk: low. Passing `no_tool_calls` tasks run verification
  commands before exiting, so after the first checklist they exit on the
  next turn without being re-nudged (the `did_no_work` guard is false).
  Worst case a passing task eats one extra checklist turn (same cost as
  today's one-shot). `max_nudges=2` bounds the extra to ≤2 gated exits.
- cost_shift: small increase, bounded. Adds at most 1 extra gated
  no-tool-call turn beyond the stock one-shot per task, and only for
  agents that exit without new verification work. Offset by turning some
  0-reward long runs into passes (no re-run cost) and by not letting
  stuck loops burn silently to a wrong exit.
