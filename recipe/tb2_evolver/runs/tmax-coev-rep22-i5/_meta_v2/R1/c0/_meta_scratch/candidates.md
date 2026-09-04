# Candidates — Round 1 (focus: task_000010_644ab1c2)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `LengthLoopBreaker` processor that escalates a *repeated* content-only
length-truncation loop: after N consecutive `finish_reason=length` turns with
no tool call, prune the accumulated runaway narration from the context tail and
inject one hard "emit ONE Bash call" directive; hard-stop with `LoopDetectedError`
if the loop persists, to reclaim the remaining step budget.

- Tasks affected (all reward=0, same mechanism):
  - task_000010_644ab1c2 (assigned focus)
  - task_000958_4bb2b05d
  - task_001857_24daeef3
  - task_001116_4c65c2f5
  - task_001098_f5acdd79
  - task_001032_1adaccb9
  - task_001837_deaf31cb
- Signal: `exit_reason=budget_exceeded` at the 80-step ceiling; in the message
  log ~30–50% of assistant turns are content-only with the run-loop's passive
  `"Your previous response was cut off by the token limit. Please continue from
  where you left off."` user nudge repeated between them. Programmatic count:
  task_000010 = 16/33 assistant turns truncated; task_000958 = 16/33;
  task_001857 = 13/33; task_001116 = 10/48.
- Verified (Read of `.messages.json`):
  - task_000010: msgs 40–69 are a strict alternation of the passive
    "cut off by the token limit … continue" user nudge and an assistant turn
    that opens with "The user is telling me to stop repeating myself and just
    run a command. … Actually, the best solution is to just keep the script at
    /home/user/k8s_operator.py" — 5 byte-identical prose openings, ZERO tool
    calls, all 80 steps consumed. The agent had actually diagnosed the true bug
    (naming the script `operator.py` triggers a stdlib circular import) and a
    working script existed at `k8s_operator.py`; a single `mv` + rename-of-import
    would have passed, but the loop never let a tool call out.
  - task_000958: assistant turns repeat "The user is telling me I'm stuck in a
    loop and hitting token limits. I need to s…" 8× with the same passive nudge
    interleaved; server was running but returning 500s and the agent never got
    a corrective command out. 80 steps, reward=0.
- Why Control not Instruction: the sibling `LengthTruncationRecoveryProcessor`
  already *does* the Instruction-shaped thing (rewrites the passive nudge into a
  "be concise, issue one Bash call" instruction) — and it demonstrably fails,
  because the model still generates >4096 tokens of prose and truncates again.
  The gap is not a missing rule the model can read; the gap is a structural loop
  that no in-context wording breaks. The fix must (a) physically remove the
  re-priming narration from the assembled context and (b) hard-terminate a
  non-recovering loop to reclaim budget — both are mechanical hook actions
  (`on_before_model` message mutation + `LoopDetectedError`) that only a
  processor can express, not a prompt rule.
- Why Control not Configuration: no existing knob controls "prune truncation
  narration" or "hard-stop after K length-truncations". `LengthTruncation
  Recovery.repeat_threshold` only switches which *nudge text* is used; it cannot
  prune context or terminate. A genuinely new mechanism is required.
- Retroactive check (A-corrective): yes — on task_000010 the working script
  already existed; pruning the runaway narration after 3 truncations removes the
  self-reinforcing "keep it as k8s_operator.py" prose that was crowding the
  model's generation, and the hard directive forces a single Bash call, giving
  the model the turn it needed to `mv`/fix the import. Even where the model
  cannot recover (task_000958's 500s), the hard-stop at 6 truncations reclaims
  ~74 wasted steps — it stops the loop from consuming the whole round. The loop
  is the actual blocker here (not a symptom of a deeper miss): the model had the
  information and a partial solution but the harness never let it act.

- expected_global_gain: closes a 7-task failing cluster whose common shape is
  "stuck in a content-only max_tokens loop, 80 steps burned, reward=0". At least
  the 2–3 tasks where a working artifact already existed (task_000010) can flip;
  the rest stop wasting the full budget, which frees wall-clock/step budget and
  can only help. Generalizes because it keys on a structural property present in
  any future task that enters the loop.
- regression_risk: Low. The processor is fully inert unless the agent produces
  ≥3 *consecutive* content-only length-truncations — a pathological state that
  never occurs on healthy trajectories (any tool call resets the counter to 0).
  Pruning only removes assistant prose turns + their passive nudges from the
  tail, never tool results, never the first message, never assistant turns that
  carry tool calls; the contract validator confirms no message-mutation
  violation. The one residual risk is a false hard-stop on a task that would
  have self-recovered at truncation 6+; mitigated by `hard_stop_after=6` (the
  R0 loops ran 13–16 truncations without recovering, so 6 is well past the point
  nudging has proven futile) and `keep_recent_pairs=1` (model still sees it was
  just told to stop).
- cost_shift: Net negative (cheaper). Pruning shrinks the assembled context on
  looping turns (fewer input tokens); the hard-stop caps wasted generations at
  ~6 instead of ~16–50. On non-looping tasks the processor adds one cheap
  counter check per turn and nothing else.

Rollback trigger: if the next round's per-task history shows any previously
passing task newly failing with `exit_reason=loop_detected` AND < ~10 steps of
real tool activity, the hard-stop is firing too eagerly — raise `hard_stop_after`
or revert.
