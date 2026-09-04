# Candidates — R2

## Candidate C-002
[lens: failure | lever: control | intent: corrective]

Add a `RepeatCommandBreakerProcessor` that injects a firm redirect user message
after the agent issues the **exact same Bash command 3 times in a row**, breaking
identical-command loops before they consume the whole step budget.

- Tasks affected (all `exit_reason=budget_exceeded`, reward=0, same mechanism —
  same command repeated consecutively until the 80-step budget is exhausted):
  task_000796_828a72cf, task_000325_3fbc5745, task_000506_c13429e7,
  task_000677_1de47eed, task_000730_265f23f6, task_000910_16cc0daf,
  task_002033_f03df97e, task_001267_0acfd3a0, task_000032_3fb303f6,
  task_000437_8450eef7, task_000713_b0778d60 (exit=done but same 26x loop).
- Signal: 16/39 failures are `exit_reason=budget_exceeded` at exactly 80 steps.
  Command-level analysis of assistant `tool_calls` shows `maxConsecDup` (longest
  run of byte-identical consecutive Bash commands) of 21–29 on the tasks above,
  with `mostCommonCmd` counts matching — i.e. the agent runs one command dozens
  of times. `LengthTruncationRecoveryProcessor` does not fire because these turns
  emit a well-formed tool call (finish_reason != "length").
- Verified (Read of `*.messages.json`):
  - task_000796_828a72cf steps 4–32: the assistant emits the byte-identical
    command `# The video is only 13KB - let's look at the raw data ... od -c
    /app/traffic_monitor.mp4 ...` **29 times in a row**; every tool result is
    `(exit 0, no output captured)`. The loop consumes steps 4→32 and the task
    hits `budget_exceeded`.
  - task_000730_265f23f6 / task_000506_c13429e7 / task_000677_1de47eed /
    task_002033_f03df97e: maxConsecDup 26–27 — same shape, same command repeated
    ~all remaining steps.
  - task_000713_b0778d60: 26 consecutive identical commands then exits `done`
    with reward 0 — the loop displaced the real work.
- Why Control not Instruction: the agent already "knows" it is looping — several
  trajectories literally narrate "I keep repeating the same command" / "let me
  try a different approach" (task_000910 step body; `diffApproach` counts of
  23–33 across the cluster) yet the model cannot break out on its own. A prompt
  rule telling it "don't repeat commands" is exactly what it is already failing
  to self-enforce; a mechanical hook that detects the repeat and forcibly injects
  a redirect *at the moment it happens* is the reliable fix. A new tool (Action)
  is impossible — the benchmark exposes only `Bash`.
- Why Control not Configuration: there is no existing knob for "consecutive
  identical tool call" detection — `LengthTruncationRecoveryProcessor` keys on
  `finish_reason=length`, and `CompactionProcessor` keys on token/message counts;
  neither observes command identity. A new processor is the minimal surface.
- Retroactive check (A-corrective): yes — in task_000796 the agent had already
  gathered enough to move on by step 4 (the `od` output was empty and would stay
  empty); a redirect at step 6 (3rd repeat) frees ~26 steps for it to actually
  extract frames / decrypt the key. The redirect does not solve the task by
  itself, but it converts a guaranteed 0-step-budget loss into a fair run for a
  large failing cluster.
- expected_global_gain: 11+ tasks currently lose their entire budget to identical
  command loops. Breaking the loop returns those ~25 wasted steps to real work,
  giving the model a genuine chance on a recurring failing cluster spanning 6
  domains (security, data_querying, data_processing, scientific_computing,
  debugging, system_administration). Even a modest conversion rate flips multiple
  tasks; at worst it makes several currently-hopeless tasks fairly attempted.
- regression_risk: Near-zero. The processor never blocks or kills a tool call —
  it only appends one user message after a 3x consecutive-identical run, then
  re-arms. Passing tasks essentially never repeat one command 3x (the one passing
  looper, task_001537, ran an identical `pip install` 4x that also returned
  identical failing output — a redirect there would have *helped* it recover
  sooner, and it passed anyway on unrelated later steps). The nudge is a no-op on
  any run that never hits 3 consecutive identical commands (the common case).
- cost_shift: Net down. Loops that previously burned ~25 redundant model+tool
  round-trips are cut short after 3 repeats; the single injected user message is
  tiny. Expected reduction in total tokens/steps on the affected cluster.
