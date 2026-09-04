# Candidates — R1 c2 (focus: task_000028_7fe033ac)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatToolCallGuard` `MultiHookProcessor` that detects byte-identical
tool-call repetition (well-formed responses, `finish_reason != length`) and
injects an escalating corrective nudge steering the model to change approach.

- Tasks affected (distinct failing task_ids, same mechanism —
  `exit_reason=budget_exceeded` at the 80-step ceiling with a byte-identical
  tool call repeated many times):
  - task_000028_7fe033ac (assigned focus): `cat /app/nginx.conf` issued 8×
  - task_000010_644ab1c2: same `nc` port-forward test command 23×
  - task_001207_44e97fe1: same `log_sanitizer.elf` invocation 29×
  - task_001717_a9c46d8d: same base64/Crypto python heredoc 26×
  - task_001857_24daeef3: `cat /proc/net/tcp | grep ...` 24×
  - task_001447_8bde38ef: `python3 -c "import openai_whisper..."` 24×
  - (also: task_000015 19×, task_001902 16×, task_001547 14×,
    task_001706 11×, task_000747 10×, task_000106 9×, task_001898 8×)
- Signal: `agent.exit_reason=budget_exceeded`, `agent.steps=80` (hard ceiling);
  per-trajectory `Counter` over `tool_calls[].function.arguments` shows one
  signature dominating with 8–29 occurrences. 16 of 50 tasks hit
  `budget_exceeded`; the majority are dominated by a single repeated command.
- Verified (Read of `task_000028_7fe033ac.messages.json`): steps 56→68 the
  assistant emits the *identical* narration
  ("The conversation is stuck in a token limit loop... Let me start fresh...")
  followed by the *identical* tool call `cat /app/nginx.conf` at msgs
  [60],[62],[64],[66],[68]; the `tool` result at [61],[63],[65],[67],[69] is
  byte-for-byte the same nginx.conf each time. No progress from step 56 to the
  80-step budget kill. Same shape confirmed by argument-`Counter` across the
  other cited tasks (maxrepeat 8–29).
- Why Control not Configuration: no existing knob covers this. The existing
  `LengthTruncationRecoveryProcessor` only fires on `finish_reason=length` with
  NO tool call; here the responses are well-formed and DO carry a tool call, so
  that guard never triggers. `ParseRetryProcessor` handles parse errors, not
  semantic repetition. The gap is a missing mechanical guard, not a mis-tuned
  one → new processor.
- Why Control not Instruction: the loop is a runtime dynamic (the model cannot
  see it is repeating because each turn's context looks locally reasonable); a
  static prompt rule ("don't repeat commands") is not reliably self-enforced
  mid-loop and cannot key on the actual repeat count. A processor that watches
  the emitted-tool-call stream and injects a *targeted, escalating* nudge at the
  moment the loop is detected is the mechanical fix. It also stays task-agnostic
  (keys purely on structural self-repetition, no task literals).
- Why Control not blocking the call: we do NOT drop/replace the repeated tool
  call (that risks starving a legitimate poll loop of its result); we only steer
  the *next* generation, so a genuinely-needed repeat still runs once more.
- Retroactive check (A-corrective): yes — in task_000028 the decisive loop is
  the repetition itself (files were already all present at step 54; the agent
  just needed to fix the socket-permission root cause and restart nginx). A
  nudge at the 3rd identical `cat /app/nginx.conf` that says "you already have
  this output, take a different action / advance the next objective" breaks the
  loop and returns ~24 steps of budget for real progress. Same logic applies to
  every cited task: the repeated command produced no new information, so
  interrupting it and forcing a different action is strictly a budget recovery.
- expected_global_gain: targets the largest failing cluster in the round
  (16/50 `budget_exceeded`, most repeat-dominated). Even partial loop-breaking
  returns tens of steps per task; realistically flips a subset and de-risks the
  rest by preventing total budget waste. Generalizes because it keys on
  self-repetition, not task content.
- regression_risk: low. Fires only after ≥3 byte-identical calls inside an
  8-call window — legitimate short poll loops (e.g. "sleep 1; curl" run twice
  while a service starts) stay under threshold, and even at threshold we only
  append one user nudge (contract-safe: append when last role != user, else
  replace last-user content). Passing tasks in this round top out well below
  the repeat threshold on any single signature. Worst case a genuine 3×-repeat
  poll gets one extra sentence of nudge and continues.
- cost_shift: net negative-to-neutral. Breaking 80-step budget burns earlier
  saves the most expensive tasks' tail steps; the injected nudge adds a few
  dozen tokens per fire, far cheaper than the dozens of wasted repeat turns it
  prevents.
