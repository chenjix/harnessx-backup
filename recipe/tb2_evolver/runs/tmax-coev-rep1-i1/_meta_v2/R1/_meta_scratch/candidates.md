# Candidates — Round 1 (tmax-coev-rep1-i1)

Baseline R0: 29/50 = 0.58. Nine domains; `system_administration` 0/5,
`security` 2/5, `software_engineering` 2/5, `scientific_computing` 2/5.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `LengthTruncationRecoveryProcessor` that detects a no-tool-call
assistant turn whose `content` is oversized (>=40000 chars) OR whose
`finish_reason == "length"`, collapses the runaway blob to a head+tail
excerpt so it cannot re-seed the loop, and injects one escalating
user nudge redirecting the model to a single concrete Bash command.

- Tasks affected (failing, same mechanism):
  task_000264_ab8c7253 (data_querying), task_000015_89886d8d
  (software_engineering), task_001818_b251e5ea (data_processing),
  task_001031_a8f0eb37 (scientific_computing),
  task_001032_1adaccb9 (file_operations), task_001321_658ce4a8
  (data_processing), task_001701_95e3bbcb (security),
  task_000010_644ab1c2 (system_administration).
  (8 distinct failing tasks across 7 domains.)
- Signal: every `exit_reason=error` task (11/11) contains >=1
  "Your previous response was cut off by the token limit" runloop
  nudge. Max assistant-turn size is bimodal: passing cluster tops
  out at ~14.8K chars; the runaway cluster is 104K-329K chars.
  A 40K threshold sits in the empty band between the two, so it
  cannot fire on any legitimate turn seen in R0.
- Verified (Read, message bodies):
  - task_001031_a8f0eb37 (144.7K-char assistant turn): final msgs
    show "I keep making the same mistake... looking at the
    conversation history, I see that the assistant has been
    repeatedly trying to write the same script... stuck in a loop",
    followed by "Your previous response was cut off by the token
    limit. Please continue from where you left off." — 74 steps,
    964s, exit=error, output file never written.
  - task_000264_ab8c7253 (328.9K-char turn): repeated subordinate-
    count re-derivation narration, "Your previous response was cut
    off..." x2, 20 steps, 485s, exit=error.
  - task_000015_89886d8d (286.6K-char turn): OCR-schema narration
    loop, "...cut off..." x2, 14 steps, 435s, exit=error.
  - task_001032_1adaccb9 (128.4K-char turn): tar-header hex-dump
    narration loop, "...cut off..." x2, exit=error.
- Why Control not Instruction: the failure is mechanical — the
  runloop's passive "continue from where you left off" nudge
  (harnessx/core/runloop.py:734) re-primes the exact runaway
  generation and the giant blob pollutes the next context window,
  re-seeding the loop. A system-prompt rule ("be concise") cannot
  intercept an in-flight truncated turn nor collapse the already-
  emitted 300KB blob before it re-enters context; only an
  `on_after_model` + `on_before_model` hook pair can. The trigger
  keys purely on structural signals (content length / tool_calls /
  finish_reason), never on task text — benchmark-agnostic.
- Why Control not Configuration: no existing knob caps assistant
  output verbosity or replaces the runloop's counterproductive
  continuation message; the mechanism does not exist in the pipeline
  and must be authored.
- Retroactive check (A-corrective): yes — on all cited tasks the
  agent still had budget and was mid-task when the loop consumed it;
  collapsing the blob + forcing one concrete Bash action breaks the
  re-seeding cycle and returns the agent to productive tool use with
  budget to spare. The same processor lineage (tmax-ev9b50 R6) was
  built against the identical bimodal signature.
- expected_global_gain: up to 8 failing tasks across 7 domains can
  flip; the mechanism is the single dominant failure shape
  (accounts for every `exit_reason=error` in the round). Also
  reduces wall-clock/cost on 2 currently-passing tasks
  (task_000338_27d6a1be 471s, task_000818_315382d9 437s) that
  survived the loop expensively.
- regression_risk: very low. No passing task has a max assistant
  turn in [14.8K, 104K); the 40K trigger cannot fire on any R0
  passing trajectory's normal turn. The only passing tasks it
  touches (338, 818) already tripped the loop and merely wasted
  time; a redirect toward a concrete action is unlikely to flip a
  near-miss pass to a fail. Counter clears on any normal / tool-
  calling turn, so one-off long turns are tolerated.
- cost_shift: strongly negative (savings). Collapses 100K-330K-char
  blobs before they re-enter context and breaks 400-960s loops
  early, cutting both token spend and wall-clock on the affected
  tasks.
