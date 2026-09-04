# Candidates — Round 1 (c3), focus task_000118_3043e92d

## Diagnosis of assigned focus (task_000118_3043e92d)

`system_administration` task: write a daemon that keeps `/home/user/logs/`
under a size threshold while 20 workers each write 10 MB. `reward=0`,
`exit_reason=done`, `finished=no_tool_calls`, 29 steps. Final grader:
`Peak log directory size was 209715200 bytes ... exceeds threshold of 45000000`.

Root cause has two layers:
- **Model capability gap (NOT harness-fixable):** the agent's truncate-then-
  SIGCONT design cannot bound size, because workers hold open file handles at a
  high offset; after truncation the next write re-extends the file to that
  offset, so `getsize` climbs monotonically to 200 MB. That is a reasoning bug
  in the solution. Per SOUL: capability gap — not patched via prompt.
- **Harness-fixable self-verification blind spot (the real lever):** the agent's
  OWN final test output (step ~28) literally printed the size climbing
  `52428800 -> 62914560 -> 73400320 -> ... -> ~200 MB` while it kept SIGSTOP/
  CONT-ing — an unmistakable violation of the task's core invariant — and the
  agent still exited declaring success. The existing self-verify checklist
  covers "files exist / script ran / service alive" but never "restate the
  task's numeric/state invariant and confirm your own test observed it holding."

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the stock one-shot `CustomSelfVerifyProcessor` with an
invariant-aware variant (`InvariantSelfVerifyProcessor`) that adds two
general checklist items: (4) restate the task's runtime/state invariant as
a measurable check and prove your own end-to-end test observed it holding;
(6) account for and clean up any background/helper processes you started.

- Tasks affected: task_000118_3043e92d, task_000140_01c78b42
  (both `exit_reason=done`, `finished=no_tool_calls`, `reward=0`; both fail on
  a runtime/process invariant, not on a missing file).
- Signal: `reward=0` with `exit_reason=done` on tasks whose grader asserts a
  runtime/end-state invariant (`max_size <= 45000000`; `pgrep -f vm_service`
  must return nothing). Existing self-verify message (harness.py
  `_SELF_VERIFY_MSG`) checks files/services but has no invariant-restatement
  or spawned-process-cleanup step.
- Verified (Read):
  - task_000118 messages step ~28: agent's own Bash output prints
    "WARNING: Log size (52428800 bytes)..." then 62914560, 73400320, 83886080,
    ... climbing to ~200 MB, and the agent's next assistant turns declare the
    script "complete and working" and exit with no further tool calls. The
    failure was in its own context and ignored.
  - task_000140 result: `AssertionError: Lingering vm_service processes found:
    ['347','572','849','1061']`; trajectory shows the agent starting the service
    in the background to test and exiting without a final process accounting.
- Why Control not Configuration: the stock `CustomSelfVerifyProcessor` takes no
  constructor args and its checklist string is hardcoded in read-only
  `harness.py` — there is no knob to tune, so a new Control processor is the
  only evolvable path. It reuses the identical fire-once exit-intent mechanism
  and shares `_singleton_group="tb2_self_verify"` so exactly one self-verify
  fires (no double injection, no +2 message contract violation).
- Why Control not Instruction: the nudge must fire *mechanically at exit
  intent* regardless of what the system prompt says, and only once — the same
  hook shape the stock processor already relies on. A static prompt rule would
  be diluted across the whole session and doesn't guarantee it lands at the
  decisive last-turn moment.
- Retroactive check (A-corrective): yes — if item (4) had fired on task_000118,
  the agent was staring at its own output showing size climb past the limit;
  the checklist forces it to read that value against the stated bound and
  conclude "not solved," blocking the premature exit. On task_000140, item (6)
  forces a `ps`/cleanup pass that removes the lingering processes the grader
  checks for.
- expected_global_gain: closes the "declared done on a solution whose own test
  output / final state violates the graded invariant" cluster (>=2 tasks here;
  a recurring TB2 shape). Generalizes to any task with a threshold/process/
  state invariant.
- regression_risk: low. Same fire-once mechanism and singleton_group as the
  processor it replaces; it never fires more than once and only appends a single
  user message at exit intent. Worst case it adds one extra verification turn on
  already-passing tasks (the stock processor already does this). No new tools,
  no schema change. Contract auto-check passed.
- cost_shift: ~neutral-to-slightly-positive. It replaces (does not add to) the
  existing self-verify turn; the message is marginally longer (two extra
  checklist items). One extra Bash verification round-trip on tasks with a
  runtime invariant — cheap relative to a failed task.

Rollback trigger: if the next round shows pass_rate drop on the previously-
passing `done` cluster or a rise in `budget_exceeded` attributable to extra
verification loops, revert to the stock `CustomSelfVerifyProcessor`.
