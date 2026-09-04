# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add an `AssistantReasoningRepeatBreaker` processor that detects a run of
byte-identical (normalized) assistant *reasoning content* across consecutive
model turns, injects an escalating redirect, and — as a hard safety net —
raises `LoopDetectedError` before the run burns to the step cap, so at least
partial work is preserved instead of dying with `exit_reason=error`.

- Tasks affected: `task_000109_09ddd96b` (assigned focus). Mechanism is a
  general model-thrash shape; other tasks that exit `error`/`max_steps` while
  repeating identical reasoning share the same root cause. (Assigned single
  focus; see "Why single-task" note below.)
- Signal: `result.json` `status=agent_error`, `exit_reason=error`, `reward=0`,
  final output files never written. In the messages log, the assistant emits
  the **byte-identical content hash `e41eb308` 8 consecutive times** (msgs
  69,71,73,75,77,79,81,83) while tool outputs alternate — so the existing
  `CyclicLoopBreaker` (which keys on the full (tool_name, tool_input, result)
  triple) never forms a period-k cycle and never escalates. `finish_reason`
  is NOT `length` (the agent emits tool calls each turn), so
  `LengthTruncationRecoveryProcessor` never fires either.
- Verified (Read task_000109 messages.json):
  - msg 71 assistant content begins: "I keep making the same mistake. Let me
    carefully read the debug output one more time:" — verbatim.
  - msgs 73,75,77,79,81,83 assistant content: **byte-identical** to msg 71
    (content hash `e41eb308` repeats 8×; confirmed via histogram).
  - Tool outputs alternate between hashes `df93062e / 1376a4f1 / 246cfad6`
    ("Error parsing WAV file: missing data chunk", exit 1) — 15 msgs carry
    the same "missing data chunk" error, but the varying Go heredoc bodies
    keep the tool-INPUT fingerprint changing, defeating the triple detector.
  - Final: `final_pytest` all 3 tests fail on "Output file ... is missing" —
    the agent never wrote `embeddings.json` / `anomaly.txt` because it spent
    its whole budget re-emitting the same reasoning.
- Why Control not Configuration: this is not a knob tune on
  `CyclicLoopBreaker` — that processor keys on the (call+output) triple and
  *cannot* be re-parameterized to see assistant-content repetition, because
  the tool input differs every turn (different code bytes). The missing
  signal is a genuinely new structural property (assistant reasoning-content
  identity), which needs a new hook, not a bigger window on the old one.
- Why Control not Instruction: the model is already being told (by the
  existing CyclicLoopBreaker warnings in-context at msgs 74/76/78) to stop and
  it keeps repeating verbatim — a prompt rule the model demonstrably ignores
  won't help; a mechanical interrupt that escalates to a hard stop will.
- Retroactive check (A-corrective): partial-yes. The hard-stop net alone
  wouldn't have written the output files, but the *earlier* escalating
  redirect (fires at repeat #3, well before the step cap) breaks the verbatim
  loop far sooner than the current pipeline (which only warned intermittently
  and never escalated), giving the agent back ~15+ steps of live budget to
  abandon the WAV-parsing tangent and write the required files — the same
  recovery-with-budget rationale that motivates the existing CyclicLoopBreaker.
- expected_global_gain: closes the "verbatim reasoning thrash → error with no
  output" failure shape for any task where the model fixates and repeats
  identical reasoning; converts a guaranteed reward-0 `error` into either a
  recovered run or a clean `loop_detected` stop.
- regression_risk: false positive on a task where the model legitimately
  repeats identical short reasoning across many turns. Mitigated by (a)
  normalizing + requiring a minimum content length so trivial/empty content
  can't form a run, (b) a conservative `break_threshold` (>=4 consecutive
  identical), (c) resetting the counter the moment content changes.
- cost_shift: net negative to neutral — cuts short the most expensive failure
  mode (a task spinning its entire step budget re-emitting the same tokens).

Note on single-task focus: the brief assigns exactly `task_000109`. The fix
is authored as a general structural mechanism (no task IDs, paths, or
domain constants) so it serves the class "model repeats identical reasoning
until the run dies", not this one task. This is the smallest defensible edit
that closes the exposed harness gap without drifting onto another proposal's
territory.
