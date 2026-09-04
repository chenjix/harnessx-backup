# Candidates — R3

## Candidate C-003
[lens: failure | lever: control | intent: corrective]

Add a `StepBudgetReminderProcessor` that, keyed on `task.max_steps`, injects one
"stop exploring, write+verify required deliverables now" user message at 0.75 and
0.90 of the step budget — closing the residual `budget_exceeded`-at-max_steps
cluster that currently receives no convergence signal.

- Tasks affected (corrective, failing, same mechanism):
  task_000010_644ab1c2, task_000118_3043e92d, task_000958_4bb2b05d,
  task_001031_a8f0eb37, task_001032_1adaccb9, task_001321_658ce4a8
  (all `exit_reason=budget_exceeded`, steps=80).
- Signal: `agent.exit_reason=budget_exceeded` at steps=80 on all six.
  `TaskTimeReminder` text appears 0x in their message logs (it no-ops because
  the live config gives it no `timeout_seconds`). The R1/R2 loop breakers already
  collapsed the identical-command loops here: max-consecutive-identical calls in
  R2 is 3-5 (was 21-33 in R1), so the residual is varied unproductive
  exploration, not a mechanical loop — nothing warns the agent it is running out
  of steps.
- Verified (Read of R2 messages):
  - task_000010 (70 msgs, budget_exceeded): last assistant turn = "Let me check
    if the port forwarder is running now." — still investigating at cut-off,
    `TaskTimeReminder`/step-budget signal count = 0.
  - task_001321 (70 msgs, budget_exceeded): last assistant turn = "Now I can see
    the actual product codes ... the grep is correctly extracting the pattern" —
    still diagnosing, no deliverable written; time-reminder fired 0x.
  - task_001032 (70 msgs, budget_exceeded): last turn is the length-loop-deprime
    stub ("prior turn discarded ... repeated no-op narration removed") — the agent
    burned the budget without producing output; time-reminder fired 0x.
- Why Control not Configuration: the natural Configuration tweak would be to give
  the existing `TaskTimeReminderProcessor` a `timeout_seconds`, but that keys off
  WALL-CLOCK, and these tasks are cut off by the STEP budget (80 steps), not by
  time — a time threshold would fire unpredictably relative to the step limit.
  The correct signal (steps-consumed / max_steps) is not exposed by any existing
  processor's knobs, so a new Control hook that counts steps and reads
  `task.max_steps` is required. Not Instruction: a static system-prompt line
  ("watch your step budget") cannot know the RUNTIME step count, so it cannot fire
  a deadline-relative nudge; the whole value is the dynamic, budget-proportional
  trigger.
- Retroactive check (A-corrective): partial-yes. For the tasks whose final turns
  show the agent still *diagnosing* with un-written deliverables (010, 1321, 1032),
  a mid-budget "stop exploring, write+verify the required files now" nudge points
  the remaining ~20 steps at production instead of investigation — the plausible
  flip path. For the genuinely capability-bound members (958 C++ HTTP service,
  1031 scientific compute) it reclaims budget but a flip is not expected; the
  nudge does not embed any domain knowledge, so it cannot hurt them.
- expected_global_gain: the six `budget_exceeded` tasks are the one failing
  cluster with a purely mechanical (non-capability) deficiency remaining — they
  receive zero budget-pressure signal today. Even 1-2 flips is real, and the
  mechanism generalises to any future task that runs long, because the threshold
  is a fraction of the runtime `max_steps`, not a hardcoded constant.
- regression_risk: near-zero. In R2, 29/30 passing tasks finished in 8-37 steps —
  below the 0.75 band (=60 steps at max_steps=80). Only task_001818 (passing, 69
  steps) reaches the band, and the injected nudge ("write+verify required
  deliverables now") reinforces exactly what a near-budget task should do; it does
  not redirect a productive path. Warn-only, never raises.
- cost_shift: neutral-to-negative. Earlier convergence on the budget cluster ends
  runs sooner (fewer wasted steps/tokens); the injected message is a few hundred
  chars fired at most twice on the tail of long runs only. No new model calls
  beyond the existing loop.
- rollback_trigger: revert if R4 shows any of the currently-passing long-horizon
  tasks (001818, 001536, 001652, 001498) regress T->F, OR net pass-rate drops
  below the R1 incumbent (32/50).

### Note on the R2 breaker (kept, not changed)

Investigation confirmed the R2 `RepeatedCommandBreakerProcessor` is effectively
inert on this benchmark: across the whole R2 run it fired only warn-level (never
reached `escalate_at=5`) and only on already-FAILING tasks (010/015/118/1321/1652),
never on a passing task, and flipped nothing. The R1->R2 losses (587/1031/1090)
do not show the breaker firing in their logs and are run-to-run variance of the
weak 9B model; 1652 is a capability-bound OCR task that failed regardless. Because
the breaker is harmless and already accepted, it is kept unchanged this round so
that R3's single lever (the step-budget reminder) is cleanly attributable. If R3
also fails to move the cluster, a future round should drop the breaker as dead
weight rather than tune it.
