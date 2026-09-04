# Candidates — Round 1 (baseline R0 = 30/50 = 60%)

Baseline domain scores: system_administration 0/5, software_engineering 2/5,
scientific_computing 2/5 are the weakest. Cross-cutting behavioural signal:
5 failures hit `exit_reason=budget_exceeded` at the 80-step cap
(task_000010, task_000118, task_000264, task_001031, task_001321),
spread across sys_admin, data_querying, data_processing, sci_computing.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatCommandGuard` processor that detects an *exact-identical* Bash
command re-run >= N times and injects an escalating break-out advisory into the
tool result (redirect first, hard-stop on further repeats).

- Tasks affected: task_000264_ab8c7253, task_001321_658ce4a8,
  task_000118_3043e92d, task_001031_a8f0eb37, task_000010_644ab1c2
  (all `exit_reason=budget_exceeded`, all failed).
- Signal: `agent.exit_reason=budget_exceeded` + 80 steps on all five; command
  histograms show verbatim-command repetition: task_000118 ran one command 14×,
  task_001031 9×, task_001321 7×, task_000264 4× (same `sqlite3 ... RECURSIVE`
  and `cat top_managers.csv` pair), task_000010 5×.
- Verified (Read, messages.json tails):
  - task_001321 last turns: assistant repeats verbatim "I've been stuck in a
    loop. Let me just run the pipeline..." then re-issues the same command;
    tool returns the identical `15 /home/user/clean_codes.txt` each time.
  - task_000264 last turns: assistant re-issues the identical
    `sqlite3 company.db "WITH RECURSIVE subordinates ..."` command; tool returns
    "(exit 0, no output captured)" repeatedly; agent narrates "I'm still getting
    no output captured... very strange" and repeats the same command again.
  - task_001031 last turns: assistant loops on the same mpi4py edit; tool result
    unchanged; even after the existing edit-limit warning it re-issues the same
    command.
- Why Control not Instruction: the agent *already knows* it is looping — it
  literally narrates "I've been stuck in a loop" — yet re-issues the identical
  command. A prompt rule cannot fire at the decisive moment (it is static
  context the model has already ignored). A mechanical hook that appends a
  targeted, escalating advisory *to the exact tool result of the repeated
  command* injects the signal precisely when and where it is needed.
- Why Control not Configuration: no existing knob covers this. The
  `CustomEditToolProcessor` only counts file-*writes* to the same path (a
  read/query/build repeated verbatim slides past it); `LengthTruncationRecovery`
  only fires on `finish_reason=length`. Neither detects an exact-repeat command
  loop, so re-parameterising them cannot close this gap.
- Retroactive check (A-corrective): yes (probabilistic). On task_000264 and
  task_001321 the loop begins well before step 80; a hard-stop advisory keyed to
  the exact repeated command, delivered on the ~3rd identical execution, gives
  the model an explicit break-out at the moment it is stuck, freeing steps to
  either fix the command or write outputs. It is not guaranteed to solve the
  underlying task, but it converts "burn all 80 steps on one dead command" into
  "get redirected with steps still on the clock", which is the actual blocker.
- expected_global_gain: targets the 5-task budget_exceeded cluster that spans 4
  domains; the mechanism (verbatim-command loop) is domain-agnostic so it
  generalises beyond these five to any future stall-and-repeat.
- regression_risk: LOW. Fires only on *exact* identical commands (whitespace-
  normalised) at count >= 3. Healthy high-step passers iterate through diverse
  commands: task_001818 (passed, 69 steps) had 31 distinct commands; task_001089
  (passed, 80 steps) had 23 distinct — neither repeats a single command 3×+ in a
  way that would fire the hard stop meaningfully. The guard is purely additive to
  the tool result (never blocks execution, never mutates message history), so it
  cannot break the contract or short-circuit a legitimate retry.
- cost_shift: slight net *decrease* expected — breaking 80-step loops earlier
  means fewer wasted steps/tokens on the affected cluster; the appended advisory
  string is a few hundred bytes and only on repeated commands.
