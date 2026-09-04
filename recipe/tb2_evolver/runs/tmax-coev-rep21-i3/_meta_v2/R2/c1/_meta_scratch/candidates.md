# Candidates — rep21-i3 R2 / c1

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the stock v4 `LengthTruncationRecoveryProcessor` (text-collapse +
text-nudge only) with a v5 forced-action variant that, after
`force_action_threshold` consecutive `finish_reason=length` no-tool-call turns,
mechanically injects a real generic Bash workspace-snapshot tool call so fresh
tool output the model did not author lands in context and breaks the narration
spiral.

- Tasks affected (assigned focus + systemic cluster, all reward=0):
  - task_000015_89886d8d — 11 truncation/continue turns, `budget_exceeded`
  - task_001717_a9c46d8d — 16 truncation/continue turns, `budget_exceeded`
  - task_001706_24462a09 — 9 truncation/continue turns, `budget_exceeded`
  - task_000936_2a78f3ca — 7 truncation/continue turns, `budget_exceeded`
  - task_001898_471c0535 — 6 truncation/continue turns, `budget_exceeded`
  - (also seen: task_000456_91b022e3=4, task_001547_8cde5da2=6)
- Signal: `result.json` `exit_reason=budget_exceeded` + a high count of the run
  loop's passive `"Your previous response was cut off by the token limit.
  Please continue from where you left off."` user messages, interleaved with
  no-tool-call assistant turns whose stored content is collapsed to exactly the
  v4 head+tail budget (1964 chars) — proving v4's `on_after_model` collapse
  fired but did not break the loop.
- Verified (Read of `.messages.json`):
  - task_000015 msgs 2–13: user "cut off by the token limit" → assistant
    "I need to stop the repetitive loop and take a different approach... Let me
    try using a more advanced OCR tool" (byte-identical narration, no tool call)
    → repeat ×5 before the first real Bash call at msg 14. ~half the step
    budget gone before any work; final `budget_exceeded`, test errors on a
    broken hypothesis property test.
  - task_001717 msgs 9,13,19,23,27,31,33: assistant "I'm stuck in a loop with
    the same Python script..." collapsed content len=1964, no tool call, each
    followed by the passive continue nudge; 16 such turns → `budget_exceeded`.
  - Audit across ALL 29 `.messages.json`: the v4 nudge strings
    ("issue exactly ONE concrete Bash", "You have now hit the output token
    limit") appear **0 times** in any final transcript despite 40+ passive
    continue messages — the text nudge is either overwritten in the stored
    transcript or narrated past; either way it is not breaking the loop.
- Why Control not Instruction: the v4 already delivers escalating instruction
  TEXT and the 4B model demonstrably narrates past it (zero effective nudges
  across the round). Another prompt rule conjures nothing — the fix must be a
  mechanical hook that changes what is in the model's context (a real tool
  result it did not author), which only a `MultiHookProcessor`
  (`on_after_model` tool-call injection) can do. The tool registry is fixed to
  Bash (playbook), so this is not an Action change; the snapshot rides the
  existing Bash tool.
- Why Control not Configuration: no existing knob turns text-only recovery into
  a forced action; `repeat_threshold` only picks between two nudge strings, both
  of which the model ignores. A new hook is required.
- Retroactive check (A-corrective): yes. On every cited task the loop is the
  actual blocker — the model has already located the workspace and a workable
  approach (task_000015 already OCR'd the schema; task_001717 has the token
  files) but keeps re-narrating instead of acting. A forced fresh tool result
  after 3 consecutive truncations gives the model concrete state to react to,
  clearing the streak and returning it to acting well before the step budget is
  exhausted, in place of the 6–16 wasted round-trips observed. Mechanism proven
  in this run loop: post-`on_after_model` `model_event.tool_calls` are dispatched
  (runloop.py L436–497) and the injected call flips `_length_truncated` False so
  no further passive nudge is added.

- expected_global_gain: Flips the `budget_exceeded` + high-continue-count
  cluster (>=5 tasks this round) by reclaiming the ~30–60% of the step budget
  currently burned in zero-progress narration spirals, letting these tasks reach
  and complete real work. Generalizes to any task where the 4B model enters a
  max_tokens narration loop — a model-shape failure independent of task domain.
- regression_risk: Fires only after 3 CONSECUTIVE truncations (a state that does
  not occur on any passing task this round — every passing task has
  maxNoToolStreak <= 1). On healthy runs the streak never reaches 3, so the
  injection never fires and behaviour is identical to v4. The injected Bash
  command is read-only (pwd/ls/find/ps, all `|| true`), cannot mutate the
  workspace, and clears the streak after one round-trip. Text ladder (repeat/
  first nudge) is unchanged for streaks < 3.
- cost_shift: Net DECREASE on the affected cluster — one bounded Bash
  round-trip replaces 3–13 full 4096-token narration generations plus their
  continue round-trips. Zero cost change on tasks that never spiral (the
  injection path is never entered).
