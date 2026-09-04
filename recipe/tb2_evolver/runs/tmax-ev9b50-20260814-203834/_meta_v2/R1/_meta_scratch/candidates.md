# Candidates — Round 1 (tmax-ev9b50)

Baseline R0: 30/50 pass (60%). 7 of the 20 failures share one exact
mechanism (see below). The remaining 13 failures are heterogeneous
capability gaps (wrong SQL, wrong output path, domain logic) with no
common harness lever — logged to the journal, not patched here.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `LengthTruncationRecoveryProcessor` that (a) collapses the runaway
`content` of a `finish_reason=length` no-tool-call response to a head+tail
excerpt, and (b) replaces the run loop's passive "please continue" nudge
with an escalating instruction to stop narrating and issue exactly one
concrete Bash command.

- Tasks affected: task_000010_644ab1c2, task_000015_89886d8d,
  task_000118_3043e92d, task_000264_ab8c7253, task_001032_1adaccb9,
  task_001321_658ce4a8, task_001652_86e1d185
  (all 7 `status=agent_error` failures; exactly the 7 tasks whose
  transcripts contain the "cut off by the token limit" nudge — perfect
  correlation with agent_error).
- Signal: `result.json` `agent.exit_reason="error"`, `status="agent_error"`,
  very high `elapsed_s` (352–1160s). Transcript contains the run loop's
  "Your previous response was cut off by the token limit. Please continue
  from where you left off." user message, 1–2 times, each followed by
  another over-length assistant turn.
- Verified (Read):
  - task_000015_89886d8d step 25: assistant `content` length = 286,609
    chars, `tool_calls=0`. Body is the *same* paragraph
    ("Actually, let me try a different approach - I'll try to use the sample
    URLs ... Let me try to create the migration script ...") repeated
    hundreds of times until the token cap — a degenerate repetition loop.
    Followed by the "cut off by the token limit" nudge, then another
    over-length turn (step 27), then error.
  - task_000264_ab8c7253 step 37: assistant `content` length = 328,921,
    `tool_calls=0`; steps 37/39 both no-tool-call; two "cut off ... continue"
    nudges precede the error.
  - task_001032_1adaccb9 step 45: assistant `content` length = 166,154,
    `tool_calls=0`; same nudge-then-relapse pattern → agent_error.
- Why Control not Instruction: the pathology is mechanical, not a
  knowledge gap. The model already "knows" it should act — the run loop's
  own nudge tells it to continue, and it relapses into the same loop. A
  system-prompt rule ("be concise") cannot intercept an in-flight
  `finish_reason=length` turn, cannot truncate the 300 KB blob that
  re-primes the loop on the next turn, and cannot escalate on repeat. Only
  an `on_after_model` / `on_before_model` hook pair can (1) prevent the
  runaway content from re-entering context and (2) inject an actionable
  redirect at exactly the moment the loop is detected.
- Why Control not Configuration: no existing processor exposes a
  finish_reason=length handler to re-tune; the run loop's continuation
  nudge is hard-coded in `runloop.py` and is not a config knob. A new
  processor is the minimal surface that can override it.
- Retroactive check (A-corrective): yes. On all 7 tasks the decisive
  failure is the model relapsing into the same over-length generation after
  the passive "continue" nudge. Collapsing the runaway content removes the
  re-priming context, and an escalating "one Bash command only" nudge
  redirects the model to act instead of narrate — breaking the loop before
  it consumes the step budget and crashes the container. At least the tasks
  where the underlying work was nearly complete (e.g. task_000010, whose
  script had already run and produced a backup + applied manifests before
  the loop began) have a plausible path to a passing final state once the
  loop is broken.

- expected_global_gain: Targets the single largest homogeneous failing
  cluster (7/20 failures, spanning system_administration, data_querying,
  software_engineering, security, data_processing — cross-domain, so the
  mechanism generalizes). Even partial recovery (loop broken, agent resumes
  productive tool calls) converts multi-hundred-second crashes into normal
  runs; some fraction should flip to pass.
- regression_risk: Low. The processor is a no-op on every turn that ends
  normally or with a tool call (the streak counter resets), so the 30
  passing tasks — none of which exhibit `finish_reason=length` no-tool-call
  turns — are untouched. Worst case on a legitimately long single turn: one
  extra "be concise / issue one command" user message, which is benign. The
  content collapse only fires when content already exceeds ~1.8 KB AND the
  turn was length-truncated with no tool call, i.e. only in the pathological
  regime.
- cost_shift: Strongly negative (cheaper). The current failure mode burns
  hundreds of thousands of output tokens per crashed task (300 KB+ blobs)
  across multiple relapse turns. Collapsing runaway content and redirecting
  to a short command turn eliminates the repeated max_tokens generations,
  cutting both tokens and wall-clock on exactly the tasks that were most
  expensive.
- rollback trigger: If R2 shows any net regression on the 30 R0-passing
  tasks, or the 7 agent_error tasks do not reduce in count, revert this
  processor.
