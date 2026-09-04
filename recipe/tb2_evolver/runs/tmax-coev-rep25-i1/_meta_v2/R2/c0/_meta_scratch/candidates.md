# Candidates — R2/c0 (focus: recovery from tool errors)

Anchor `task_000010_644ab1c2` is a **1-task** shape (self-created
`operator.py` shadows Python's stdlib `operator`, ImportError; the agent's
"recovery" was to rename the required file away, violating the task path
requirement). Per the generalization contract, a single-task shape earns a
no-op, not a special case. So I traced the anchor's *observable* failure —
"agent hit an error and its recovery attempt did not change the outcome" —
to a recurring, task-agnostic cluster.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `StuckReasoningRecoveryProcessor`: after N consecutive **identical
assistant reasoning turns** (whitespace-normalised), inject one legible
change-approach directive before the next model call; cooldown until the
content changes; bounded by `max_nudges`.

- Tasks affected (failing, same mechanism — identical consecutive assistant
  narration, run length in parens):
  - `task_001031_a8f0eb37` (24) — exits `agent_error`
  - `task_000015_89886d8d` (22) — `budget_exceeded`
  - `task_000140_01c78b42` (22) — `done`, no progress
  - `task_000329_a3ac56b0` (17) — `budget_exceeded`
  - `task_001818_b251e5ea` (16) — `agent_error`
  - `task_001321_658ce4a8` (14) — `budget_exceeded`
- Signal: longest run of byte-identical consecutive assistant `content`.
  On the 6 tasks above the run is 14-24; the modal repeated message
  literally narrates the stuck state ("I keep getting the same error / I've
  been stuck in a loop / let me try a fundamentally different approach") yet
  the very next turn reproduces the same content and action. Crucially the
  tool result is often a SUCCESS (`"Script created"`, exit 0), so no
  error-fingerprint or non-zero-exit signal exists.
- Verified (Read):
  - `task_001031` msgs 87-94: assistant emits verbatim "I keep making the
    same mistake. Let me try a completely different approach - use
    `comm.Allgatherv` with a flat array..." 24× in a row; each tool reply is
    "Script created" (success) — the Bash arg head is identical across
    msgs 71-93. Then `exit_reason=error`.
  - `task_000015` (33 assistant msgs, top repeat 28×): verbatim "I keep
    getting the same error. Let me try a fundamentally different approach -
    using a Python script to call tesseract. The context was compressed..."
  - `task_000140` (top repeat 22×): verbatim "I keep hitting the token limit
    when trying to read the test_pipeline.sh file. Let me try a different
    approach..."
  - `task_001321` (top repeat 14×): verbatim "I've been stuck in a loop
    trying to run the same command. The shell is not capturing output
    properly. I need to try a fundamentally different approach..."
- Why Control not Configuration: no existing knob detects reasoning
  repetition. `LengthTruncationRecoveryProcessor` only fires on
  `finish_reason == "length"` (none of these six truncate; they emit full
  turns then loop). This is a missing mechanical guard that must fire
  uniformly across tasks → a new `MultiHookProcessor`.
- Why Control not a tool/error-fingerprint guard (differentiation): an
  error- or command-fingerprint guard keys on a *failing* tool result or a
  byte-identical tool call. `task_001031`'s loop returns SUCCESS every turn,
  so those guards never fire — the failure is in the repeated *reasoning*,
  not a repeated error. Keying on identical assistant content catches this
  orthogonal shape.
- Retroactive check (A-corrective): yes — in each cited task the agent had
  already recognised it was stuck (the repeated text says so) but could not
  self-break. A directive naming that "the previous approach produced no
  change" and requiring a concrete different action, injected at the loop
  point (well before budget/error exit), gives the agent the explicit push to
  change invocation / inspect real state that it failed to give itself.
- expected_global_gain: closes the reasoning-repetition failure cluster (6
  R0 fails, spanning MPI/numpy, OCR/tesseract, shell-pipeline, and DB tasks —
  domain-diverse, so the mechanism generalizes to unseen tasks that loop on
  the same self-recognised-but-unbroken pattern). Two of the six
  (`001031`, `001818`) currently exit `agent_error`, the hardest failure
  class.
- regression_risk: nudge is advisory (a user message), never blocks/rewrites
  tool calls, capped at 3/task, armed only once per distinct stuck content.
  `repeat_threshold=12` sits ABOVE every passing task's *mid-task* identical
  run (`task_001591`=11, `task_001652`=10) so productive-but-repetitive work
  is untouched. The only passing tasks with runs ≥12 (`task_000024`=22,
  `task_001781`=20) loop AFTER the work is banked (post-completion
  verify-tail, fraction ≥0.79 / repeated text = "the task is complete, let me
  provide a final summary") — a nudge there is at worst harmless and likely
  ends wasted budget.
- cost_shift: net token DECREASE on the loop cluster (cuts 10-20 wasted
  repeat turns on ≥6 tasks and the passing verify-tails); ≤3 short user
  messages only on tasks that actually loop ≥12×; zero change on all other
  passing tasks.
- rollback_trigger: if R3 pass_rate is flat/down AND the longest
  identical-assistant-run on the six predicted tasks does not fall vs R0,
  revert the processor.
