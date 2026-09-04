# Candidates — R2 c0 (focus: task_000010_644ab1c2)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Supersede `LengthTruncationRecoveryProcessor` with a variant that escalates on
**cumulative** (not just consecutive) `finish_reason=length` truncations, and on
a chronic loop drops the runaway narration to a stub instead of re-priming it.

- Tasks affected (corrective, ≥2 distinct, same mechanism):
  - task_000010_644ab1c2 (F, budget_exceeded, 80 steps, 11 truncations)
  - task_001032_1adaccb9 (F, done, 73 steps, 27 truncations)
  - task_000939_1592be48 (F, done, 47 steps, 10 truncations)
  - task_001857_24daeef3 (F, budget_exceeded, 80 steps, 5 truncations)
- Signal: run-loop passive nudge `"Please continue from where you left off."`
  recurs many times per task (11 / 27 / 10 / 5). The stock
  `LengthTruncationRecoveryProcessor` escalation is keyed on the **consecutive**
  truncation run; in these tasks the pattern is *alternating*
  truncate → nudge → one-command → truncate (`TCTCTC…`), so the consecutive
  counter keeps resetting to ≤2 and the hard escalation never fires. Meanwhile
  each collapsed ~1964-char "I'm stuck" turn stays in context and re-primes the
  next runaway generation.
- Verified (Read of messages.json):
  - task_000010 truncation user-msgs at indices [3,5,24,28,32,36,40,44,48,52,65]
    — spread across the whole run to the very end (n=70); assistant turns at
    2/4/23/27/…/64 are each exactly 1964 chars (`collapse marker count = 11`),
    all re-narrating "The user is right - I've been stuck in a loop trying to
    free port 9090. Let me try a different port…". The agent never creates the
    required `/home/user/operator.py` (final_pytest:
    `test_operator_script_exists AssertionError … does not exist`); it dies
    `budget_exceeded` at step 80.
  - task_001032 truncation indices [7,11,19,23,…,96,98] — 27 truncations
    interleaved with tool calls across the entire 100-message run; consecutive
    run stays low so the stock hard nudge never triggers.
  - task_000939 truncation indices [38,40,44,46,50,54,60,64,68,72] — 10
    truncations, chronic tail, ends `done` with wrong/missing output.
- Why Control not Configuration: the fix is not a knob on the existing processor
  — the stock class simply has no notion of cumulative truncations (it resets on
  every tool call), so no parameter value reproduces the escalation. A new
  `MultiHookProcessor` (same `_singleton_group`/`_order`, so it replaces the stock
  one) is the minimal mechanism. Why Control not Instruction: the corrective
  nudge already reaches the model each truncated turn (the processor's
  `on_before_model` rewrites the trailing user message) and the model still
  loops — a prompt rule cannot break a mechanical re-priming loop; the mechanism
  (drop the re-priming narration, escalate the directive on cumulative count) is
  what changes behaviour.
- Retroactive check (A-corrective): partial-yes. The mechanism removes the
  dominant budget sink (every second turn was a wasted full-4096-token
  re-narration) and stops the collapsed "I'm stuck" text from re-priming the
  loop, giving the model many more clean action-turns within budget and a
  terminal "one minimal command, fix the required output PATH" directive that
  targets exactly task_000010's terminal blocker (operator.py never written to
  the exact path). It is honest that flipping is not guaranteed — task_000010's
  proximate stall was also a semantic misread of `/proc/net/tcp` — but the
  harness deficiency (chronic truncation loop with no cumulative escalation and
  narration re-priming) is real and shared across ≥4 tasks, and the change is
  net-positive on budget for all of them.
- expected_global_gain: reclaims the wasted ~50% of budget spent on repeated
  full-length re-narration across the chronic-truncation cluster (≥4 tasks this
  round), converting truncation turns into either productive action turns or an
  earlier terminal directive; plausibly flips the ones whose only remaining
  blocker was running out of budget before writing the required output file
  (task_000010, task_001857 both `budget_exceeded`).
- regression_risk: Low. The processor never force-exits and never removes
  messages (contract-safe: `on_after_model` rewrites only its own event content,
  `on_before_model` rewrites only the trailing user message). The one passing
  task that truncates a lot — task_001832_dd672877 (PASS, 7 truncations at
  indices [9,11,29,31,35,39,66]) — front-loads its truncations and then produces
  40+ productive messages; the only effect of the change on it is that its late
  collapsed turns become a short stub instead of head+tail (harmless) and it may
  see the chronic directive, which merely says "emit one command" — exactly what
  a recovering agent already does. task_000011 (PASS, 3 truncations) stays under
  `chronic_threshold=4` entirely.
- cost_shift: Net negative (lower). Chronic-loop tasks stop emitting repeated
  4096-token re-narration turns; the stub collapse also shrinks forwarded
  context. No forced extra model turns.
- rollback_trigger: Revert if next-round pass_rate drops, OR if task_001832 /
  task_000011 (or any previously-passing task that truncates) regresses to F
  attributable to the escalated directive, OR if replay fails on the processor.
