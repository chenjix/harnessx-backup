# Candidates — Round 1 (c0)

Assigned focus: `task_000010_644ab1c2` fails (`budget_exceeded`, 80 steps).

## Candidate C-001 — Add LoopDetectionProcessor to break identical-repeat loops

**Three-axis tag:** lens=failure-mode / lever=control / intent=corrective

### Signal
`task_000010_644ab1c2` exit_reason=`budget_exceeded` at 80 steps.
Inspecting `task_000010_644ab1c2.messages.json` (post-compaction, 70 msgs):
the agent issued the **exact same** Bash tool call
`python3 -c "import socket ... print('socket module loaded')"` **33 times
consecutively** (distinct fingerprints = 1), each returning the identical
`ImportError: cannot import name 'namedtuple' ... circular import` traceback,
until the step budget was exhausted. Assistant narration was byte-identical
across all 33 turns ("The issue is that `socket` imports `selectors`...").

Root cause the model never diagnosed: it wrote `/home/user/operator.py`,
which shadows the stdlib `operator` module; since python runs from
`/home/user`, every `python3` invocation self-poisons. The model kept
re-probing the same broken command instead of trying something different.

### Verified body evidence
- `task_000010`: `result.json` → `agent.exit_reason = "budget_exceeded"`,
  `steps = 80`, `final_pytest.passed = false`.
- `messages.json`: 33 assistant turns, all with tool_call
  `{"command": "python3 -c \"\nimport socket..."}` — `distinct=1`,
  `max_identical_consec=33`.

### Cluster evidence (this is NOT a single-task fix)
Scanned all 38 `budget_exceeded` trajectories in the round. Consecutive
identical tool-call runs at the tail:
- `max_identical_consec >= 5` on **~24 of 38** budget_exceeded tasks.
- **17 tasks** have `distinct == 1` (same call 33x): task_000010, 000015,
  000264, 000329, 000344, 000396, 000505, 000684, 000956, 000958, 001031,
  001088, 001090, 001652, 001673, 001818, (+ 000578/001498 near-identical).
The dominant failure mode of the whole round is "agent stuck emitting one
identical command until the 80-step budget is gone." The current pipeline
has **no** processor that detects identical repeated *tool calls* — the only
loop guard present (`LengthTruncationRecoveryProcessor`) fires only on
`finish_reason=="length"` with no tool call, which is a different mode
(these calls are well-formed and succeed).

### Intervention
Add the **existing built-in** `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
to the pipeline (no new code authored). Behaviour:
- Strategy 1 (exact name+inputs fingerprint): inject a "you are stuck in a
  loop — try something fundamentally different" warning into the tool result
  at `warn_threshold=3`, and raise `LoopDetectedError` at `threshold=5`.
- Strategy 2 (name-only): warn-only at 8, never raises.
- Compaction-aware: clears fingerprint window on message-count drops so
  post-compaction state doesn't produce spurious counts.

Defaults kept (window 12, warn 3, raise 5, name_warn 8, compaction_drop 5).
Placed near the end of the pipeline (after CustomSelfVerifyProcessor) so its
`on_after_tool` warning is the last thing appended to a tool result.

### Retroactive check (corrective variant)
*Would this have changed task_000010's trajectory?* Yes. At the 3rd identical
`python3 -c "import socket"` call, the agent receives an injected warning
telling it to stop and try a fundamentally different approach — a signal it
never got in the actual run. If it still doesn't break out, the run raises
`LoopDetectedError` at the 5th repeat (`exit_reason=loop_detected`, cleanly
caught in runloop.py:781 — NOT `error`, so it passes the replay gate),
freeing ~28 wasted steps instead of burning all 80.

### Why control, not instruction?
A system-prompt line ("don't repeat commands") is exactly the kind of
static guidance the model already ignored — it re-emitted identical narration
33 times. The failure is a *runtime state* the model cannot self-observe
(it has no memory that it already ran this exact call N times). A control
processor that fingerprints tool calls and injects a state-aware warning /
terminates is the correct mechanism. No new domain knowledge is injected;
this generalizes to any stuck-loop task.

### Pareto
- `expected_global_gain`: attacks the dominant `budget_exceeded` cluster
  (~24/38 tasks show >=5 identical consecutive calls). Even a modest
  break-out rate flips several; terminating early frees budget on the rest.
- `regression_risk`: a legitimately-passing task that *intentionally* runs
  the same command 5x in a row would be terminated. Extremely rare — exact
  name+input match 5x consecutive is almost never productive. Warn@3 is
  non-destructive (appended text only). Passing tasks in this round
  (000344, 000587, 000748, 000912, 001781) either exit `done` early or, for
  000344, do repeat — see uncertainty.
- `cost_shift`: net **negative** (cheaper). Loops terminate at ~5 steps
  instead of 80, saving tokens/steps on the stuck cluster.

### Rollback trigger
If R2 shows pass_rate flat-or-down AND any currently-passing task regressed
to `exit_reason=loop_detected`, revert (or raise `threshold` to 8).
