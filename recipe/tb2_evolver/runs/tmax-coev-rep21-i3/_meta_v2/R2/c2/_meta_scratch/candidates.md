# Candidates — R2 c2 (focus: task_000028_7fe033ac budget_exceeded)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Swap the stock `LoopDetectionProcessor` for an `EscalatingLoopBreakProcessor`
subclass that (a) lowers the exact-match hard-raise threshold 40→6 so verbatim
loops terminate, and (b) escalates the name-only (semantic) nudge into a
decisive commit-or-pivot directive at `escalate_at=6` and hard-raises at
`name_threshold=14`, converting a guaranteed reward-0 `budget_exceeded` burn
into an earlier clean `loop_detected` exit while pushing still-progressing
agents to pivot to productive work with budget to spare.

- Tasks affected (corrective, ≥2 distinct, same mechanism —
  `exit_reason=budget_exceeded` driven by a recognised-but-unbroken loop):
  task_000028_7fe033ac (focus), task_000109_09ddd96b, task_001706_24462a09,
  task_001716_c1f2ac56, task_000936_2a78f3ca (10 total in the round).
- Signal: 10/43 failures (≈45% of the *evaluable* failures — 21 of the 43 are
  docker container-name conflicts with `exit_reason=error`, not agent failures)
  have `exit_reason=budget_exceeded`, ALL reward 0. Every one fires the stock
  `[LoopDetection]` warning heavily (8–25 times) yet runs the full 80 steps —
  the warn-only Strategy-2 never raises and the Strategy-1 hard raise
  (`threshold=40`) is unreachable inside an 80-step budget.
- Verified (Read, body-quoted):
  - task_000028_7fe033ac msgs 37/39/41/47/49/51: the agent cycles raw-socket /
    HTTP probes (`curl`/`wget`/`nc` absent → `/dev/tcp` → python socket) ~26
    distinct commands, each preceded by "I'm stuck in a loop trying to test the
    connection", until step 80 `budget_exceeded`.
  - task_000109_09ddd96b (last ~14 msgs): agent repeats `ls -la /home/user/`
    VERBATIM ~25× ("I'm stuck in a loop calling the same command") — a pure
    exact-match loop that the old `threshold=40` never stops; it self-breaks to
    write `pipeline.go` only at step ~78, with no budget left to finish.
  - task_001706_24462a09 (last ~12 msgs): agent repeats
    `timeout 1 bash -c 'pkill -9 -f processor'` (exit 137, hangs each time)
    many times, "I'm stuck in a loop trying to kill the processor", to
    `budget_exceeded`.
- Why Control (subclass) not pure Configuration: lowering `threshold` alone
  (a Configuration edit) stops the *exact-match* verbatim loops cleanly, but
  the largest sub-pattern (task_000028) varies its arguments, so Strategy-1
  never accumulates — only the *name-only* signal fires. The stock processor's
  name-only branch is warn-only with a *static* message the model has already
  learned to ignore ("I'm stuck… let me try a different approach" → repeats).
  Making it escalate into a concrete directive AND adding a bounded name-only
  raise both require overriding `on_after_tool`; no constructor knob exposes
  either. Everything else in the pipeline is unchanged.
- Why not Instruction: the agent already *knows* it is looping (it says so
  every turn); a system-prompt rule adds nothing a runtime, loop-triggered,
  escalating tool-result message doesn't deliver at the exact moment of the
  loop. The gap is a missing forcing function at the tool layer, not missing
  knowledge in the prompt.
- Retroactive check (A-corrective): partial-yes, honest.
  - task_000109 / task_001706 (exact-match verbatim loops): a hard raise at
    exact `threshold=6` (or name `threshold=14`) terminates them ~70 steps
    earlier. This alone does not flip reward (state was incomplete), BUT the
    escalated directive arriving at run-length 6 (vs a static warn the model
    ignored) gives the agent budget to pivot to the constructive action it
    only reached at step ~78 — the plausible flip path.
  - task_000028: build was genuinely broken (nginx 502 unresolved), so an
    earlier stop does not flip it; the escalation's value here is redirecting
    the 40+ wasted probe steps toward the root cause. Net: the change cannot
    make any of these *worse* (budget_exceeded and loop_detected are both
    reward 0) and gives the recoverable subset a real pivot path.
- expected_global_gain: the `budget_exceeded` cluster is the single largest
  evaluable-failure bucket (10 tasks). Even a modest pivot rate on the
  recoverable subset (verbatim/semantic loops where work was nearly done)
  flips tasks; the change generalises to any task where the 4B model recognises
  a loop but lacks a forcing function to break it.
- regression_risk: LOW. Every currently-passing task has max exact-consecutive
  tool-call run == 3 (measured across all 7 passers; highest is task_000876 at
  3, which only trips the *warn* at 3, never the raise at 6). No passing task
  approaches name-only run 14. The only pipeline change is this one processor;
  Strategy-1 exact semantics are otherwise identical.
- cost_shift: strongly NEGATIVE (savings). Pathological loops that previously
  ran to 80 steps now exit at ≤14 semantic / ≤6 exact repeats, reclaiming
  ~60–70 steps of wall-clock and tokens per affected task. On passing tasks
  cost is unchanged (they never reach the new thresholds).
- rollback_trigger: if R3 shows the `budget_exceeded` count NOT shrinking, OR
  any previously-passing task flipping to `loop_detected`, revert to the stock
  `LoopDetectionProcessor` with `threshold=40`.
