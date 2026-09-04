# Candidates — Round 1 (tmax-coev-rep1-i2)

Baseline: 30/50 pass (0.60). 20 failures. Exit-reason breakdown of the
failing set: 4 `budget_exceeded` (all @ 80 steps), 1 `loop_detected`,
15 `done`-but-wrong. The dominant *harness-addressable* cluster is a
small-`max_tokens` truncation-repetition loop that burns dozens of steps.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Rewrite the `LengthTruncationRecoveryProcessor` (v2 → v3) so it (a) reliably
triggers on `finish_reason == "length"` for this small-`max_tokens` model and
(b) in `on_before_model` *overwrites the run loop's passive "continue from
where you left off" last-user message* with an actionable "stop narrating,
run ONE command" directive, instead of appending (which the before_model
contract drops when the last role is already `user`).

- Tasks affected (failing, same mechanism):
  task_000740, task_001032, task_000396, task_000028, task_001653,
  task_000015, task_000958 (and partially task_000010).
- Signal: repeated persisted user messages containing "cut off by the token
  limit" + consecutive no-tool-call assistant turns of ~2000-3100 chars with
  `exit_reason ∈ {budget_exceeded, done}`. Count of passive "cut off" nudges
  per failing task: 740→11, 1032→17, 396→10, 028→5, 1653→5, 015→3, 958→3.
- Verified (Read of `task_000740_59416444.messages.json`):
  - Assistant turns [11],[13],[35],[39],[43],[47],[51],[58],[62],[66] are all
    exactly 2001 chars, no tool call (measured content lengths).
  - After each, a persisted user message [12],[36],[40],[44],[48],[52],...
    reads verbatim: "Your previous response was cut off by the token limit.
    Please continue from where you left off." (95 chars).
  - Assistant [37],[41],[45],[49],[53] re-emit the *identical* paragraph
    "The user is right - I've been stuck in a loop for a very long time. I need
    to stop and try a completely different approach..." — i.e. the passive
    nudge re-primes the same runaway text. budget_exceeded @ step 80.
  - v2's actionable nudge (`_NUDGE_FIRST` / `_NUDGE_REPEAT`, 300+ chars) never
    appears in the transcript — consistent with it being contract-dropped
    because the last role was already `user` when v2 tried to *append*.
  - v2 char-branch (`content_char_threshold=40000`) can never match: max
    assistant content across the whole run is 2001 chars.
- Why Control not Instruction: the failure is a *mechanical* interaction
  between the run loop's passive nudge and the model's narration habit — the
  model cannot be told, via the static system prompt, to react to a message
  the harness injects mid-loop. The fix must intercept the assembled context
  at `on_before_model` and replace the counter-productive last message; only a
  processor can see and mutate that per-call message tuple. A system-prompt
  rule ("keep responses short") does not remove the passive re-priming message
  that is literally the last thing the model reads each loop iteration.
- Why Control not Configuration: v2 is already wired with tunable kwargs, but
  no kwarg value fixes the bug — the `content_char_threshold` branch is
  irrelevant here (content is ~2000 chars, not 40000) and the real defect is
  the *append-vs-rewrite* logic in `on_before_model`, which is code, not a
  knob. Lowering `content_char_threshold` alone would still leave the
  contract-dropped append and the surviving passive nudge.
- Retroactive check (A-corrective): yes — on task_000740 the model repeatedly
  wrote a correct-enough plan but was cut off and re-primed to "continue". Had
  the last user message instead read "STOP narrating; issue ONE short Bash
  command that writes the output file", the model would have been pushed to a
  single decisive action within its token budget instead of regenerating the
  same 2000-char narration. This directly removes the step-burn that caused
  budget_exceeded; at minimum it recovers the wasted step budget so the model
  reaches a committing action.

- expected_global_gain: Flips / rescues the small-`max_tokens` truncation-loop
  cluster (7-8 failing tasks, 4 of them pure budget_exceeded step-burns). Even
  where the underlying answer is also wrong, freeing the wasted step budget
  gives the model turns to self-correct rather than grinding to the cap.
- regression_risk: Low. The trigger only fires on no-tool-call
  `finish_reason=="length"` turns (or 40K+ blobs); passing tasks in r0 never
  hit `finish_reason=="length"` (their max assistant content is <1600 chars
  and every turn carries a tool call). The `on_before_model` path only mutates
  the last message when a truncation streak is armed, so normal turns are
  untouched. Contract-checked clean (rewrite-last = len_delta 0; append only
  when last role != user).
- cost_shift: Net *reduction* expected — the target tasks currently burn to 80
  steps / 300-1350s in the loop; breaking the loop earlier cuts tokens and
  wall-clock on exactly the most expensive runs. Slight per-turn increase from
  the longer directive text, dwarfed by the saved loop iterations.
