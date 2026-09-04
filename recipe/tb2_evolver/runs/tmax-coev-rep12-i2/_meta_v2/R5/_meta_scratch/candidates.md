# Candidates — R5

## Candidate C-005
[lens: failure | lever: configuration | intent: corrective]

Lower `LengthTruncationRecoveryProcessor.repeat_threshold` from 2 to 1 so
the hard escalation nudge ("STOP … respond with a SINGLE short Bash tool
call that writes the required output file(s) to the path named in the
task") fires on the **first** max_tokens truncation instead of waiting for
a second consecutive one.

- Tasks affected (corrective, ≥2 distinct, same mechanism):
  - task_000730_265f23f6 — 18 truncation events; final_pytest fails on
    `os.path` / `test_solution_file` = the required solution file was
    never created (agent burned budget on truncated analysis, exited
    empty-handed).
  - task_001028_5bc8bc70 — 7 truncation events; final_pytest fails on
    `test_success_file_exists_and_correct` = required file absent.
  - (secondary, cost-only) task_000032 (16), task_000796 (15),
    task_001016 (8) — same truncation-loop mechanism, capability-bound
    content so cost-savings not flips.
- Signal: run-loop injects "Your previous response was cut off by the
  token limit. Please continue…" once per `finish_reason=length &
  no tool_calls` step. Grep of R4 messages.json:
  - FAILING tasks carry 2–18 of these events each
    (task_000730=18, task_000032=16, task_000796=15, task_001016=8,
    task_001028=7, task_001044=4, task_000908=3, task_000506=2).
  - PASSING tasks carry essentially zero (only task_000267 = 1).
  So truncation-without-tool-call is a near-perfect FAIL discriminator;
  a nudge gated on it cannot meaningfully touch the passing set.
- Verified (Read, R4 transcripts):
  - task_001044 messages.json indices 3/7/14/22 are user turns with the
    raw "cut off by the token limit … continue" text; each is preceded by
    an assistant turn with `tool_calls=False` and a long analysis tail —
    i.e. the exact `finish_reason=length & not tool_calls` condition.
  - task_000730 final_pytest tail: `os.path` AssertionError on
    `test_solution_file` — file never written before the cap.
  - task_001028 final_pytest tail: `test_success_file_exists_and_correct`
    AssertionError — file absent.
  - Current escalation (`repeat_threshold=2`) means truncation #1 gets
    only the SOFT nudge; the hard "write the required output now" nudge is
    delayed to #2. On tasks that reach the step cap empty-handed, pulling
    the file-write directive one turn earlier is the cheapest shot at
    landing a best-effort partial before budget_exceeded.
- Why Configuration not Control/Instruction: the mechanism (detect
  length-truncation, collapse content, inject an escalating file-write
  nudge) already exists in `LengthTruncationRecoveryProcessor`; only its
  firing point is mistuned. A new Control processor would duplicate it; an
  Instruction rule ("write a partial before exiting") already exists in
  the R4 system prompt and did not fire at the decisive step — the gap is
  *when* the existing mechanical nudge escalates, a pure knob.
- Why not touch the command/content-repetition guards instead: full-command
  repeat counts show passing iterative tasks reach 5–6 identical commands
  (task_000197=6, task_000716=5) — indistinguishable from the failing
  loops at any threshold that would fire, so tightening them carries real
  regression risk. The truncation signal has NO such overlap with the
  passing set, making it the safe lever.
- Retroactive check (A-corrective): partial-yes — for task_000730 /
  task_001028 the file was never written; firing the "write the required
  output to its exact path now" directive on truncation #1 (instead of #2)
  gives ~1 extra early action turn aimed squarely at creating the file,
  which is the single failing assertion. Not guaranteed (content may still
  be wrong) but it is the specific missing step. For the capability-bound
  truncation tasks it is cost-savings, not a flip.
- expected_global_gain: recovers the slice of the truncation-loop /
  budget_exceeded cluster whose only failing assertion is
  "required output file does not exist" (≥2 tasks: task_000730,
  task_001028) by pulling the existing file-write escalation one turn
  earlier; hardens every truncation-heavy task toward writing a
  best-effort partial before the step cap.
- regression_risk: the escalated nudge is stronger and now fires one turn
  sooner, but ONLY on `finish_reason=length & no tool_calls` — a condition
  that appears ~0 times on passing tasks (task_000267=1 event, still
  passed). No passing task reaches the step cap. Worst case: one extra
  short, action-forcing user turn on a task that truncated once and would
  have recovered anyway — bounded, non-blocking, +0 message-contract
  insertions (it replaces the run-loop's continue message).
- cost_shift: net negative (cheaper). Forcing a single short Bash call on
  the first truncation removes the runaway re-generation turns that
  currently burn 7–18 steps per truncation-heavy task; pure no-op on the
  non-truncating (all passing) tasks.
- rollback_trigger: R6 pass_rate below R4 (22/50) beyond run-to-run noise,
  OR a previously-passing task regresses with the length-recovery nudge
  visible in-transcript, OR synthetic replay shows exit_reason=error
  attributable to the earlier-firing nudge. Revert `repeat_threshold` to 2.
