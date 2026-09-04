# Candidates — R4

## Candidate C-004
[lens: failure | lever: control | intent: corrective]

Add a general degenerate-turn terminator that force-ends the session cleanly
once the model has emitted the byte-identical assistant turn `terminate_threshold`
times in a row — catching BOTH Bash-input loops and no-tool-call / self-verify
loops (a shape the existing BashLoopBreaker cannot see), and converting the
resulting `error` / `budget_exceeded` runaways into a clean `done` that banks
partial work and lets exit-intent processors fire.

- Tasks affected (>=2 distinct, same mechanism):
  task_000438_fee5a792 (41 identical assistant turns, exit=error),
  task_000761_072f9e93 (21, budget_exceeded),
  task_001028_5bc8bc70 (16, budget_exceeded),
  task_002138_2e85672e (12, budget_exceeded),
  task_002146_0bc2994c (11, done-but-looping),
  task_000032_3fb303f6 (10, exit=error),
  task_000348_31fb8c8a (9, budget_exceeded),
  task_000908_170e5e4e (8, budget_exceeded),
  task_001044_45c70cf1 (7, exit=error),
  task_000506_c13429e7 (7), task_000796_828a72cf (6), task_000740_8245be5c (6),
  task_002033_f03df97e (5), task_001201_1340f4e2 (5),
  task_000938_6d7bdc5c (4, exit=error), task_000730_265f23f6 (4).
  16 distinct failing tasks; 0 passing tasks reach the threshold.
- Signal: measured max run of consecutive byte-identical assistant messages.
  16 FAILING tasks have a run >= 4 (4 → 41); every PASSING task's max run is
  <= 2. `exit_reason=error` on 4 of them (agent_error crash driven by the
  identical-turn repetition); `budget_exceeded` on most of the rest.
  The existing `BashLoopBreaker` fingerprints only Bash *tool inputs*, so it
  (a) never sees no-tool-call / `_tb2_self_verify` repetition loops, and
  (b) keeps soft-blocking the same Bash command while the model re-emits it
  17-38 more times (transcripts show 12-38 "[LoopBreaker] BLOCKED" lines that
  the model ignores verbatim).
- Verified (Read, body-quoted):
  - task_000032_3fb303f6 msgs 63-72: assistant repeats verbatim "I've been
    stuck in a loop trying to crack the password. The LoopBreaker has blocked
    my attempts. Let me take a fundamentally different approach..." while the
    tool result is "[LoopBreaker] BLOCKED: this exact command has now been
    issued 17..18..19..20 times consecutively..." — the model never varies the
    turn; run ends exit=error.
  - task_000761_072f9e93 msgs 2-11: assistant repeats verbatim "The user is
    asking me to verify my solution. Let me re-read the task..." with NO tool
    call, alternating with the "Verification check initiated" ACK; then msgs
    13-21 repeat the build.rs `cat` block + "[LoopBreaker] ⚠️/BLOCKED". Neither
    loop is escaped; run ends budget_exceeded at 80 steps. The no-tool-call
    self-verify half of this loop is completely invisible to BashLoopBreaker.
- Why Control not Instruction: three prior rounds already ship a strong
  instruction (R2 system prompt explicitly says "DON'T BURN THE BUDGET ON A
  DOOMED SUBGOAL ... stop repeating it") and a soft mechanical redirect
  (R1 BashLoopBreaker BLOCKED message). Both are proven-ignored — the model
  re-emits the identical turn 12-38 times after being told to stop. A prompt
  rule cannot fix a model that ignores the rule; only a hard mechanical
  termination (skip_model → finish_reason=stop, no tool_calls → clean run-loop
  break) removes the runaway. This is input-independent, must fire uniformly
  across every task, and must act at the model-call boundary — a shape a
  per-call tool cannot express, so Action is not applicable.
- Why not just tune BashLoopBreaker (Configuration): the BashLoopBreaker's
  block is structurally the wrong action — it keeps the loop alive by re-prompting.
  Lowering its threshold makes it block sooner but the model still ignores it.
  The missing capability is *termination*, plus coverage of the no-tool-call
  loop shape it cannot fingerprint. That is a new mechanism, not a knob.
- Retroactive check (A-corrective): PARTIAL-yes. On the 4 `exit_reason=error`
  tasks (000032, 000438, 000938, 001044) a forced clean `done` removes the
  agent_error crash and guarantees the verifier scores the final state through
  the normal exit path — a strict improvement in run robustness (and it
  protects the post-flight replay gate, which hard-fails on exit_reason=error).
  On the budget_exceeded loopers it does NOT by itself add a missing
  deliverable (the filesystem is unchanged), so those remain capability-bound;
  the honest gain there is cost/step recovery (20-70 wasted step-generations
  reclaimed per task) and a clean exit that lets the exit-intent
  ProactiveVerifierDepGuard run. Net: robustness + large cost win, small
  upside on pass-rate concentrated on the crash cluster.
- expected_global_gain: Eliminates the `exit_reason=error` crash cluster
  (4 tasks) by converting it to clean `done`, protecting the replay gate and
  guaranteeing verifier scoring; recovers a large, wasted step/token tail on
  the full 16-task identical-turn-loop cluster; generalizes to any future task
  that falls into byte-identical assistant repetition (Bash OR no-tool-call).
- regression_risk: Near-zero on pass-rate. Every one of the 10 passing tasks
  has a max identical-assistant run of <= 2; the terminate threshold is 6
  (warn at 4), so no passing trajectory can trip it. The only behavioural
  change on a passing task would require it to emit the identical turn 6x,
  which never occurs in the observed passing set. Force-stop only fires after
  the run is already provably dead.
- cost_shift: Net decrease — truncates 20-70 wasted step-generations
  (each a full model call, some near the 4096-token cap) across 16 tasks,
  including the 900-1900s runaways. No added model calls (the synthetic stop
  turn replaces a model call, it does not add one).
