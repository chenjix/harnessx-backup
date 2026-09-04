# Candidates — R5/c6

## Candidate C-001 — degenerate low-information tool-call loop breaker

**Three-axis tag:** lens=run-pathology / lever=control / intent=recover-cluster

**Signal.** Assigned focus `task_000396_e56917e2` (scientific_computing,
reward=0, exit_reason=**budget_exceeded**, steps=80). The deliverable
`/home/user/validation.log` *exists* but holds the wrong value (`0.60724`;
grader requires `0.0 < max_dev < 0.1`). The run pathology, not the value, is
the harness gap: from message 4 onward the agent fell into a **byte-identical
2-cycle** — `cat /home/user/validation.log` → `0.60724` alternating with a
long analysis command whose stdout never changes — repeated ~30 times,
consuming the entire 80-step budget.

**Verified body evidence.**
- `result.json`: `exit_reason=budget_exceeded`, steps=80, `initial_pytest.passed=true`;
  `final_pytest`: `AssertionError: The maximum deviation 0.60724 is not within the
  expected range (0.0, 0.1)`.
- `messages.json`: msgs 4–54 are an A/B/A/B cycle of two commands with
  unchanging results; the assistant even *narrates* the loop ("I've been
  repeating the same analysis many times") at msgs 19–53 yet keeps issuing the
  same pair. It briefly escaped at msgs 62–66 — read `/app/reference.dat` and
  `/app/lib-rk45/rk45.c`, and correctly identified the injected bug
  ("PERTURBATION: Negative exponent causes divergence!") — then **relapsed**
  into the same 2-cycle (msgs 66–69) and hit the wall with the fix never applied.
- **Loop-detector replay** (offline, on the recorded transcript): with
  `window=6, distinct_max=2` the condition first satisfies at tool-result
  **index 5** (≈step 6) and holds for 23 of 32 windows. So the nudge fires
  ~70 steps before the budget wall — ample headroom for the model to reach its
  own correct diagnosis (msgs 62–66) and apply the fix.

**Retroactive check (variant: would-the-mechanism-have-fired).** Replaying the
detector over `task_000396`'s recorded (command,result) fingerprints, the loop
condition fires at index 5 and 23× total. On the R2-passing set the sibling R3
sweep found 0/29 passing tasks satisfy `window=6/distinct<=2` — the shape is
exclusive to the looping/failing cluster, so append-only firing is expected to
be silent on passing tasks.

**Why control, not instruction/configuration.** The model already *knows* it is
looping (it says so verbatim) and even knows the fix — a static system-prompt
instruction (instruction lever) cannot break an in-progress runtime loop, and
tuning an existing knob (configuration lever) has no knob for this shape.
Only a runtime hook observing the repetition *shape* of completed tool calls
and injecting a redirect at the moment of stall can reclaim the budget. The
existing `LengthTruncationRecoveryProcessor` handles only the
`finish_reason=length` no-tool-call loop; the *completed-tool-call* 2-cycle
here is uncovered by the R0 pipeline.

**Change.** New `DegenerateLoopBreakerProcessor` (`MultiHookProcessor`,
`_order=33`, after `CustomEditToolProcessor(30)`, before
`CustomSelfVerifyProcessor(90)`). Maintains a rolling window of blake2b
fingerprints of (command, result) pairs; when distinct fingerprints in the
window ≤ `distinct_max` it appends an escalating soft→hard "you are looping,
change strategy — inspect state you haven't examined, find the root cause,
apply a concrete fix, verify the exact output path/value" note to the tool
result. Append-only, non-terminating, content-agnostic (no task ids / paths /
syntax literals). Re-fires every `refire_gap` results so a relapse (as seen at
msgs 66–69) gets re-nudged.

**Relationship to prior work / novelty.** This mechanism was drafted in the R3
thread `h_degenerate_loop_breaker_v1` (predicted 000010/000958/001706/001031),
which is `pending` (never gated; incumbent remains R0). It is **not reverted**,
so the novelty gate permits it. This proposal is a distinct witness
(`task_000396`, a *fragile* task not in the R3 predicted set) and a fresh
hypothesis id; the R3 draft targeted a different cluster. I strengthened the
hard-nudge wording to point the agent at un-examined inputs / root-cause fix,
motivated by 000396's relapse-after-brief-escape pattern.

**expected_global_gain.** Flips/relieves the degenerate completed-tool-call
loop cluster. `task_000396` is *fragile* — it **passed in R0** (per-task
history: R0=True, R1–R4=False) and the model reaches the correct diagnosis
unaided in this very transcript; the only thing standing between it and a pass
is the budget consumed by the loop. Reclaiming ~70 steps at the first stall lets
the already-present fix land. The mechanism is content-agnostic and covers the
broader loop cluster documented across rounds (sqlite3/jq/import reruns and
A/B/A/B cycles).

**regression_risk.** Very low. Append-only on the tool result (mirrors
`CustomEditToolProcessor`'s contract), so it cannot violate the message-count
hook contract, never removes messages, never mutates the system prompt, never
terminates. The R3 sweep confirmed 0/29 R2-passing tasks hit the
`window=6/distinct<=2` condition, so no passing task should ever see the note.

**cost_shift.** Net decrease. Breaks loops at ~the 6th repeated result instead
of running to the step-80 wall, reclaiming tens of steps per stuck task
(task_000396 alone spent ~74 of 80 steps looping). The note is ~90–130 tokens
and fires only inside an active loop.

**rollback_trigger.** If the next round shows `task_000396` still reward=0 with
the loop unbroken (nudge ignored) — escalate cadence / add a terminate-on-loop
guard rather than reship — OR if any previously-passing task regresses to F,
revert.
