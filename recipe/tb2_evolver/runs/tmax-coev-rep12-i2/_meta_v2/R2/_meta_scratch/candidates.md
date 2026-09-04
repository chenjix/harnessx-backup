# R2 Candidates — tmax-coev-rep12-i2

## Candidate C-002 — content-repetition (narration loop) recovery processor

- **Lens / Lever / Intent**: control-loop failure detection / **control** (new
  `MultiHookProcessor`) / close a systemic thrash cluster + cut wasted cost.
- **Signal (frontmatter sweep)**: 4 of the 5 `budget_exceeded`/`max_steps`
  failures (all pinned at the 80-step cap) show degenerate narration loops.
- **Verified body evidence** (from `_meta_scratch/probe5.py` over the R1
  trajectory messages.json):
  - `task_000910_16cc0daf`: **15 consecutive identical** assistant turns
    ("Let me try a different approach - use a simpler command to compile and
    test."). exit=budget_exceeded, 80 steps.
  - `task_001028_5bc8bc70`: **9 consecutive identical** turns ("I've been stuck
    in a loop trying to understand the binary. Let me take a completely
    different approach.") — 24 turns share the same 60-char prefix.
    exit=budget_exceeded, 80 steps.
  - `task_000716_206dc0f6`: 4 consecutive identical turns; multiple prefix
    clusters incl. env-injected "stop the repetition" already present but not
    breaking the loop. exit=budget_exceeded, 80 steps.
  - `task_001877_bd2513aa`: repeated OCR-extraction narration; exit=budget_exceeded.
- **Why this is harness-shaped, not capability**: the *loop* is the harness
  deficiency (no mechanism catches a normal-finish narration loop), even though
  the underlying task is hard. Existing guards miss it:
  - `LengthTruncationRecoveryProcessor` only fires on `finish_reason=="length"
    and not tool_calls`; these turns finish normally / carry tool calls.
  - `CustomEditToolProcessor` only counts repeated *file-write* commands
    (redirects/sed -i/tee); these are diagnostic/compile commands.
  - `CustomSelfVerifyProcessor` fires once, only on exit-intent.
- **Change**: new `processors/content_repetition_recovery.py::
  ContentRepetitionRecoveryProcessor`, `_order=6` (right after length-recovery).
  Fingerprints normalized assistant content; after `repeat_threshold=3`
  consecutive look-alike turns injects a corrective nudge (change tactic /
  gather new evidence), escalating at `escalate_threshold=5` to a hard
  "STOP — write the required output file to its exact path or run one genuinely
  new command". +1 user message per chain max (replaces a trailing user msg if
  present) — contract-safe. `min_content_chars=40` avoids tripping on terse
  action turns.
- **Retroactive check (counterfactual)**: had this processor been live in R1,
  task_000910 would have been interrupted after turn 3 of its 15-turn identical
  streak (~step 6-9 instead of 80) and task_001028 after turn 3 of its 9-turn
  streak. Neither is guaranteed to flip (both are genuinely hard: multi-service
  daemon + binary RE), but the escalated nudge routes the model toward writing
  the required output file before the step cap — the specific gap that scored
  them 0 (files never created). Worst realistic case: no flips but large step/
  cost savings on the loop cluster.
- **Why control (new processor) not instruction (system prompt)**: the system
  prompt already tells the agent to plan and not repeat; the loops happen
  *anyway* mid-run. A static prompt cannot detect a live repetition streak — a
  processor watching `on_after_model` can. This is a detection/interception gap,
  the canonical case for a `MultiHookProcessor`.
- **expected_global_gain**: primary win is cost/step reduction across the
  ~4-task loop cluster (each currently burns the full 80-step cap on repeated
  output); secondary chance to flip a borderline task by forcing an earlier
  pivot to writing required outputs. Generalizes to any future narration-loop
  task, not just these four.
- **regression_risk**: a nudge could fire on a *legitimately* iterative task
  that happens to narrate similarly. Mitigated by (a) high threshold (3 near-
  identical turns, escalate at 5), (b) `min_content_chars` floor, (c) the nudge
  is advisory text, never blocks or drops a tool call, (d) counter resets after
  each nudge so it fires at most once per streak-start. No already-passing task
  in R1 showed a 3+ identical-narration streak (passing tasks finished in
  6-33 steps), so predicted collateral on the passing set is near zero.
- **cost_shift**: net **negative** (cheaper). Interrupting a 15-turn identical
  streak at turn 3 removes ~12 wasted model turns on affected tasks; the nudge
  itself is one short user message. On non-looping tasks the processor is a pure
  no-op (no message added).
- **rollback_trigger**: if R3 pass_rate drops below R1 (20/50) beyond repeat
  noise, OR the synthetic replay / any task shows `exit_reason=error`
  attributable to this processor, OR a previously-passing task regresses with a
  content-repetition nudge visible in its transcript → revert to R1 config.

### Carried forward (unchanged): C-001 verifier-dep guard
R1's `VerifierDepGuardProcessor` is retained byte-for-byte (repointed to the R2
copy). Evidence it worked: the 4 R1 target tasks no longer fail at pytest
collection with `ModuleNotFoundError` — they now reach real assertions
(task_000773 imports imageio fine; 000796/000910/002108 hit real server/logic
assertions). The +4/-4 R0→R1 swap is run-to-run noise (none of the gained/lost
tasks are ImportError-class). Keeping the guard is net-positive insurance for
any future correct-but-dep-blocked solution.
