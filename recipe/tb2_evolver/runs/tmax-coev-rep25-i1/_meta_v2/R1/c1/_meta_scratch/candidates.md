# Candidates — R1 c1 (focus: context hygiene)

## Failure class (observable state)

A recurring **degenerate repeated-tool-call loop**: the agent emits a
near-identical assistant turn and issues the same `Bash` tool call over and
over, each returning an identical result (often `(exit 0, no output captured)`),
making zero progress until it hits the 80-step budget (`exit_reason=budget_exceeded`).
The loop poisons context hygiene two ways: (1) the transcript fills with
20-28 identical turns of noise, and (2) `CompactionProcessor` then *summarises
the loop* and re-injects "the assistant is stuck in a loop" as a synthetic user
message (`[Earlier conversation summary: ...]`), which the model parrots — the
summary re-seeds the very loop it described.

Anchor: `task_000015_89886d8d` (budget_exceeded, 80 steps, 28 identical
assistant turns, 33 empty tool results, required output files never written).

Cross-task evidence (same mechanism, different inputs):
- FAIL loopers: `task_000015` (28), `task_001321` (15, "shell is not capturing
  output properly"), `task_000329` (20), `task_001089` (11), `task_000140` (23).
  All burn to budget/near-budget and produce nothing.
- PASS-then-loop (work already done, loop is redundant self-verify/verification):
  `task_000024_a0664029` (finished at msg 9 "The task is complete... Created
  migrate.sh", then 22 identical verification turns) and
  `task_001781_529727cf` (finished summary at msg 11, then 20 identical turns).
- Counter-evidence / regression guard (PASS, transient loop then RECOVERED):
  `task_001591_8901fea6` max identical run = 11 (mid-task at msg 35), then kept
  working to msg 100 and passed; `task_001652_86e1d185` max run = 10, passed.

Calibration: every observed *mid-task* identical run that later recovered to a
pass was **≤ 11** consecutive. Every run **≥ 12** consecutive in this batch was
either a dead budget-burn loop (fail) or a post-work redundant loop (pass, but
output already on disk). So a hard-raise threshold of 12 fires only on
pathological loops and leaves transient-recover tasks untouched.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Wire the existing, vetted `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
into the pipeline in a **warn-early / raise-only-on-pathological** configuration:
exact-fingerprint warn at 4, exact-fingerprint raise at 12, name-only warn at 10
(never raises), compaction-aware fingerprint reset.

- Tasks affected (corrective / no-regression):
  - Budget-burn loopers freed (fail→fail but ~65 wasted steps + context
    pollution eliminated, loop no longer re-seeds via compaction summary):
    `task_000015_89886d8d`, `task_000329_a3ac56b0`, `task_001321_658ce4a8`.
  - Post-work redundant loopers terminated early, pass preserved via
    `_recover_best_output` (output already on disk before the loop):
    `task_000024_a0664029`, `task_001781_529727cf`.
- Signal: `exit_reason=budget_exceeded` at exactly `steps=80` clustered with a
  long run of byte-identical assistant turns + identical tool results
  (`(exit 0, no output captured)`); frontmatter `tool_call_counts[Bash]` maxed.
- Verified (Read of message bodies):
  - task_000015 msgs 2-69: assistant text `"I keep getting the same error. Let
    me try a fundamentally different approach - using a Python script to call
    tesseract."` repeated verbatim, each tool result `(exit 0, no output
    captured)`; msg 1 is a compaction summary describing the loop.
  - task_001321 msgs 2-20 + 25-69: `"the shell is not capturing output
    properly ... try a different approach"` repeated, identical empty results.
  - task_000024 msg 9 `"The task is complete. I've successfully: 1. Found ...
    2. Created /home/user/project/migrate.sh"` then msgs 11..(end) identical,
    tool result `"Verification check initiated..."` — work done pre-loop.
  - task_001781 msg 11 completion summary then 20 identical turns; work
    (bug fixes to the .wav pipeline) already written.
  - Counter: task_001591 msg 35 shows a 5-run then real progress to msg 100
    (pass); max mid-task identical run = 11 → below the raise threshold of 12.
- Why Control (add existing processor) not Instruction: the agent already
  *narrates* that it is looping ("I keep getting the same error", "stuck in a
  loop", "repeating the same command") and still cannot break out — a prompt
  rule telling it to "stop when looping" duplicates a signal the model already
  emits and ignores. The blocker is mechanical: nothing in the loop *forces* a
  stop, so the budget drains. A control hook that hard-terminates the run at a
  pathological repeat count is the mechanism the prompt cannot provide.
- Why reuse LoopDetectionProcessor not author a new class: the existing
  processor already carries the fingerprint bookkeeping, consecutive-tail run
  counting, and — crucially for this focus — **compaction-aware fingerprint
  reset** (clears stale fingerprints on a message-count drop) so post-compaction
  turns are scored fresh. Re-authoring that is pure risk.
- Why threshold=12 not the default 5: the default exact-raise of 5 would
  terminate `task_001591` at its mid-task 5-run (msg ~35) *before* it recovered
  and passed — a pass regression. Body evidence shows every recover-to-pass
  loop stayed ≤ 11 consecutive; 12 sits above that ceiling.
- Retroactive check (A-corrective): partial-yes. For the post-work loopers
  (000024, 001781) the pass is preserved and ~50 steps/task are reclaimed. For
  the budget-burn loopers (000015, 001321, 000329) the task still fails on
  content (the model never solved the OCR/shell problem — a model capability
  gap, logged below), but the round stops burning 65+ steps and stops the
  compaction-summary re-seeding that made the loop self-sustaining. The change
  is net-positive on cost with zero predicted pass regression.

- expected_global_gain: eliminates the degenerate-loop failure shape across the
  budget_exceeded cluster (5 tasks touched); reclaims ~250-350 wasted steps of
  compute across the batch; removes the compaction-summary-re-seeds-loop
  pathology that is the context-hygiene root cause. Generalizes to any unseen
  task where the 4B model falls into an identical-call loop.
- regression_risk: a task that *legitimately* issues the identical command 12+
  times in a row and needs a later call to succeed would be cut short. No such
  task exists in this batch (max mid-task recover-run = 11). Threshold 12 +
  `_recover_best_output` on `loop_detected` bounds the risk; rollback trigger:
  if any currently-passing task flips to fail with `exit_reason=loop_detected`,
  raise threshold or revert.
- cost_shift: strongly negative (cheaper). Five tasks currently run to 80 steps;
  early termination at the loop point cuts each by ~50-65 steps. No cost added
  on non-looping tasks (processor only appends a short warning at high repeat
  counts).

## Not a harness fix (logged)

- `task_000015` OCR-of-image and `task_001321` "no output captured" root causes
  are **model capability gaps** (the 4B model cannot get tesseract OCR working /
  misreads empty stdout as a broken shell). No harness edit conjures that
  capability; the loop-detector only stops the resulting budget burn. Skip the
  capability itself.
