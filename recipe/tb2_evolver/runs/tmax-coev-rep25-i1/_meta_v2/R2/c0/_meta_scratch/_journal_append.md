## Round 3 — stuck-reasoning loop guard

<!-- journal:frontmatter
round: 3
timestamp: 2026-09-04T04:20:00Z
hypothesis_id: h_stuck_reasoning_recovery_v1
levers: [control]
predicted_affected: [task_001031_a8f0eb37, task_000015_89886d8d, task_000140_01c78b42, task_000329_a3ac56b0, task_001818_b251e5ea, task_001321_658ce4a8]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the reasoning-repetition failure cluster (6 R0 fails, domain-diverse: MPI/numpy, OCR/tesseract, shell-pipeline, DB), where the agent emits byte-identical assistant narration 14-24 turns in a row even when the tool returns SUCCESS each turn (a shape the length- and error/command-fingerprint guards structurally miss). Two of the six exit agent_error, the hardest failure class."
regression_risk: "Advisory user-message nudge, never blocks/rewrites tool calls, capped max_nudges=3/task, armed once per distinct stuck content. repeat_threshold=12 sits above every passing task's MID-TASK identical run (task_001591 run 11, task_001652 run 10). The only passing tasks with runs at/above 12 (task_000024 run 22, task_001781 run 20) loop AFTER work is banked (post-completion verify-tail) so a nudge there is harmless."
cost_shift: "Net token DECREASE on the loop cluster (cuts 10-20 wasted repeat turns on the failing cluster plus the passing verify-tails); at most 3 short user messages only on tasks that loop 12-plus times; zero change on other passing tasks."
rollback_trigger: "If R4 pass_rate is flat/down AND the longest identical-assistant-run on the six predicted tasks does not fall vs R0, revert the processor."
-->

### Why

Assigned focus: recovery from tool errors. The anchor task_000010 is a
1-task shape (self-created operator.py shadows stdlib operator giving an
ImportError; its "recovery" renamed the required file away, violating the
path requirement) — per the generalization contract that earns a no-op, not a
special case. Tracing the anchor's observable failure ("agent hit an error
and its recovery attempt did not change the outcome") led to a recurring,
domain-diverse cluster: the agent emits the SAME assistant reasoning on
consecutive turns — often explicitly narrating "I keep getting the same
error / I've been stuck in a loop / let me try a fundamentally different
approach" — yet reproduces the identical narration and action. The agent has
recognised it is stuck but cannot self-break; its recovery IS the repeat.

### Changes

- `processors/stuck_reasoning_recovery.py` — new MultiHookProcessor
  StuckReasoningRecoveryProcessor: tracks whitespace-normalised assistant
  content; after repeat_threshold identical consecutive turns injects ONE
  legible change-approach directive before the next model call, with a
  cooldown until content changes and a max_nudges cap.
- `config.yaml` — register it (repeat_threshold=12, max_nudges=3,
  min_chars=24) just after LengthTruncationRecoveryProcessor. Distinct
  trigger from that guard (finish_reason=length) and from any
  error/command-fingerprint guard (keys on repeated reasoning even when the
  tool result is a SUCCESS).
- `system_prompt.txt` — copied byte-for-byte from R0 (SiblingSystemPromptBuilder).

### Evidence

- task_001031 msgs 87-94: assistant emits verbatim "I keep making the same
  mistake. Let me try a completely different approach - use comm.Allgatherv
  with a flat array..." 24 times; each tool reply is "Script created" (exit 0,
  SUCCESS), Bash arg head identical across msgs 71-93; then exit_reason=error.
- task_000015 (33 asst msgs, top repeat 28 times): "I keep getting the same
  error. Let me try a fundamentally different approach - using a Python script
  to call tesseract..."
- task_000140 (top repeat 22 times): "I keep hitting the token limit when
  trying to read the test_pipeline.sh file. Let me try a different approach..."
- task_001321 (top repeat 14 times): "I've been stuck in a loop trying to run
  the same command. The shell is not capturing output properly..."
- Passing-cluster guard: task_001591 (pass) mid-task run 11 at fraction 0.47;
  task_001652 (pass) run 10 at 0.39 — both below threshold 12. task_000024
  (pass) run 22 and task_001781 (pass) run 20 are post-completion verify-tails
  ("the task is complete, let me provide a final summary").

### Uncertainty

The nudge is advisory: a weak model may ignore it as it ignored its own
stuck-state narration. Mitigation is specificity (names that the prior
approach produced no change and demands one concrete different action).
threshold=12 is conservative to protect passing tasks; if R4 shows the
predicted tasks' identical-run lengths unchanged, escalate to blocking the
identical re-execution outright rather than nudging.
