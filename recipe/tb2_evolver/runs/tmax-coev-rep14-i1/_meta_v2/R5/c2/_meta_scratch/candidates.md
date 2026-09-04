# Candidates — R5 c2

Assigned focus: `task_000028_7fe033ac` (system_administration) — `reward=0`,
`exit_reason=budget_exceeded`, 80 steps.

## Diagnosis

The initial_pytest passes (4/4). The final_pytest fails at *collection* with
`ModuleNotFoundError: No module named 'requests'` (the injected verifier module
`/tmp/test_final_state.py` does `import requests`). That is the grade-level
cause, and a legitimate harness discipline gap already covered by the in-flight
R3 hypothesis `h_http_verifier_dep_guard_v1` (pending) which explicitly lists
task_000028 — re-shipping that would be a novelty/territory collision.

But the *dominant harness gap this round's trajectory exposes* is different and
larger: a **degenerate completed-tool-call loop that burns the entire step
budget with no forced termination**. From msg 21 to msg 77 the agent issues the
byte-identical command
`pkill -9 -f "server|nginx" ...; /app/server >> /app/server.log 2>&1 & ...; nginx -c ...`
**23 times consecutively** (32 times total), each returning `(exit 137, no
output captured)` — the `pkill ... -f "server"` pattern matches and kills the
agent's own shell, so the tool always returns exit-137 with no output and the
model observes nothing new. The `[EditDetection]` and `LengthTruncation` nudges
fire and are quoted back by the model ("The user is telling me to stop the
repetitive reasoning") yet the loop continues to the 80-step wall.

Nothing in the R0 pipeline **terminates** a loop of *completed* tool calls whose
results never change: `LengthTruncationRecoveryProcessor` only handles
`finish_reason=length` no-tool-call turns; `CustomEditToolProcessor` and the
existing nudge-only processors are append-only and were demonstrably ignored
here. The run therefore burns ~55 wasted steps and leaves a corrupted final
state (services self-killed).

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Enable the built-in `harnessx.processors.control.LoopDetectionProcessor` in the
pipeline at its default parameters (Strategy-1 `threshold=5` raises
`LoopDetectedError` → run loop exits `loop_detected`), so a byte-identical
tool-call loop is terminated early instead of burning the full step budget.

- Tasks affected (failing cluster, ≥5 consecutive byte-identical tool calls):
  - `task_000028_7fe033ac` — maxrun **23**, budget_exceeded
  - `task_001706_24462a09` — maxrun **33**, budget_exceeded (`ps aux | grep …` x33)
  - `task_001031_a8f0eb37` — maxrun **35**, error
  - `task_000396_e56917e2` — maxrun **19**, budget_exceeded (mock-coefficient probe x19)
- Signal: `exit_reason ∈ {budget_exceeded, error}` with a tail run of ≥19
  byte-identical `tool_calls[].function.arguments`; result is unchanged each
  iteration (e.g. `(exit 137, no output captured)`), zero new observation.
- Verified (body-quoted):
  - task_000028 msgs 21–77: identical `pkill -9 -f "server|nginx" …` → `(exit
    137, no output captured)` repeated 23× consecutively; model quotes the
    ignored nudge at msg 21 ("The user is telling me to stop the repetitive
    reasoning") and keeps looping to step 80.
  - task_001706 msgs 2–68: identical `ps aux | grep -E "(processor|sink|
    generator)" | grep -v grep` repeated 33× consecutively.
  - task_000396 msgs 17–53: identical mock-error-coefficient probe command
    repeated 19× consecutively after the sim output already showed a mismatch.
- Why Configuration not Control: the *exact-repeat terminate* mechanism the
  evidence needs already exists as a shipped, battle-tested built-in
  (`LoopDetectionProcessor`, Strategy 1). The only gap is that it is **absent
  from the R0 pipeline** — enabling it is a knob/registration change, not new
  code. Authoring a bespoke processor (the in-flight R3
  `h_degenerate_loop_breaker_v1`, control lever) is strictly heavier and, more
  importantly, that proposal is **append-only nudge** — this trajectory proves
  append-only nudges are ignored here (EditDetection/LengthTruncation both fired
  and the model quoted them back while continuing). A hard terminate is the
  differentiated mechanism the evidence demands.
- Retroactive check (A-corrective): partial-yes. On all four tasks the loop is
  the actual budget-burning blocker; Strategy-1 fires at the 5th identical call,
  terminating ~15–30 steps into the loop with `exit_reason=loop_detected`
  instead of running to step 80/erroring. This reclaims the wasted budget and
  leaves the container in the pre-thrash state for the verifier. It does NOT by
  itself fix task_000028's grade (that fails on the verifier `import requests`,
  a separate gap owned by R3 `h_http_verifier_dep_guard_v1`); the harness
  capability being repaired is the missing forced-exit on a degenerate
  completed-tool-call loop, which is systemic across the cluster.
- expected_global_gain: closes the "identical-tool-call loop burns the whole
  step budget" gap for a ≥4-task failing cluster; converts wall-hitting
  budget_exceeded/error runs into early `loop_detected` exits. Any task whose
  loop is downstream of already-completed work can flip; all reclaim budget and
  avoid further state corruption from continued thrashing.
- regression_risk: very low. Across 30 R4-passing tasks, **0 hit maxrun≥5**; the
  single highest passing task is maxrun=4 (task_001090), one below the
  threshold=5 fire point. The failing cluster sits at 19–35 — a wide margin.
  Strategy 2 (name-only) is warn-only (never raises) so it cannot terminate a
  legitimate varied-argument sequence. No passing task issues the same tool call
  5× in a row, so no passing run can be prematurely terminated.
- cost_shift: net decrease. Terminates dead loops ~15–30 steps in instead of at
  the 80-step wall, reclaiming tens of steps (and their tokens) per stuck task;
  the warn nudge is ~60 tokens and only appears inside an active loop.

- rollback_trigger: if the next round shows any previously-passing task
  regressing to F with `exit_reason=loop_detected` (a false-positive
  termination), OR the cited cluster still shows maxrun≥19 loops running to the
  wall (processor not wired), revert to R0. If loops are terminated early but the
  four tasks stay F on their own correctness/verifier assertions, the residual is
  a separate capability/verifier gap — keep the loop guard (it reclaimed budget
  and prevented state corruption) and note the residual.
