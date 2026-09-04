
## Round 5 (c2) — enable built-in terminate-on-identical-loop guard

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-28T18:00:00Z
hypothesis_id: h_loop_detection_terminate_v1
levers: [configuration]
predicted_affected: [task_000028_7fe033ac, task_001706_24462a09, task_001031_a8f0eb37, task_000396_e56917e2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Close the identical-completed-tool-call loop cluster (4-plus tasks: 028 pkill x23, 1706 ps|grep x33, 1031 x35, 396 x19) by TERMINATING the loop at the 5th consecutive identical call (exit_reason=loop_detected) instead of burning the full 80-step budget; reclaims tens of steps and prevents further state corruption from continued thrashing."
regression_risk: "Very low - 0 of 30 R4-passing tasks reach 5 consecutive byte-identical tool calls (max passing = 4, task_001090); the failing cluster sits at 19-35, a wide margin above threshold=5. Strategy 2 (name-only) is warn-only and never raises, so varied-argument sequences cannot be terminated."
cost_shift: "Net decrease - terminates dead loops ~15-30 steps in rather than at the step-80 wall, reclaiming tens of steps/tokens per stuck task; the warn nudge is ~60 tokens and only appears inside an active loop."
rollback_trigger: "If next round shows any previously-passing task regress to F with exit_reason=loop_detected (false-positive termination), OR the cited cluster still shows maxrun 19-plus loops running to the wall (guard not wired), revert to R0. If loops terminate early but the four tasks stay F on their own correctness/verifier assertions, keep the guard (budget reclaimed, corruption avoided) and note the residual."
-->

### Why

Assigned focus task_000028_7fe033ac (system_administration, reward=0,
exit_reason=budget_exceeded, 80 steps). initial_pytest passes 4/4; final_pytest
fails at collection with ModuleNotFoundError: No module named 'requests' (the
grade-level cause, already owned by in-flight R3 h_http_verifier_dep_guard_v1 -
re-shipping that is a territory collision). The dominant harness gap the
trajectory exposes is different and systemic: a degenerate completed-tool-call
loop that burns the entire step budget with no forced termination. From msg 21
to msg 77 the agent issues the byte-identical command pkill -9 -f
"server|nginx" ...; /app/server ... nginx -c ... 23x consecutively - the pkill
-f "server" matches and kills the agent's own shell, so every result is (exit
137, no output captured) and the model observes nothing new. The existing
append-only nudges (EditDetection, LengthTruncation) fire and are quoted back
("The user is telling me to stop the repetitive reasoning") yet the loop
continues to the wall. Nothing in R0 TERMINATES a loop of completed tool calls
whose results never change. Sweep confirms a 4-plus-task cluster (028 maxrun 23,
1706 maxrun 33, 1031 maxrun 35, 396 maxrun 19), all reward=0 /
budget_exceeded|error.

### Changes

- config.yaml - enable the built-in
  harnessx.processors.control.loop_detection.LoopDetectionProcessor at default
  parameters (window_size=12, warn_threshold=3, threshold=5,
  name_warn_threshold=8, compaction_drop_threshold=5), inserted after
  ToolCallCorrectionLayer (consistent with its _order=20). Strategy 1 raises
  LoopDetectedError at 5 consecutive byte-identical calls -> run loop exits
  loop_detected; Strategy 2 is warn-only. Rest of the pipeline byte-identical to
  R0; system_prompt.txt sibling copied unchanged.

### Evidence

- task_000028_7fe033ac messages msgs 21-77: identical pkill -9 -f
  "server|nginx" ... -> (exit 137, no output captured) 23x consecutive; msg 21
  quotes the ignored nudge; run to step 80, exit_reason=budget_exceeded.
- task_001706_24462a09 msgs 2-68: identical ps aux | grep -E
  "(processor|sink|generator)" | grep -v grep 33x consecutive; budget_exceeded.
- task_000396_e56917e2 msgs 17-53: identical mock-error-coefficient probe 19x
  consecutive after the sim already showed a mismatch; budget_exceeded.
- task_001031_a8f0eb37: maxrun 35 consecutive identical, exit_reason=error.
- Passing false-positive sweep: 0 of 30 R4 reward=1 tasks reach maxrun 5-plus
  (highest = task_001090 at 4) - threshold=5 leaves a clean margin.

### Uncertainty

Why Configuration not Control: the exact-repeat terminate mechanism already
exists as a shipped built-in; the only gap is that it is absent from R0 -
enabling it is a registration/knob change, not new code. The in-flight R3
h_degenerate_loop_breaker_v1 (control) is append-only NUDGE, which this
trajectory proves is ignored (model quoted EditDetection/LengthTruncation back
while looping). A hard terminate is the differentiated mechanism the evidence
demands. Terminating early does not by itself flip task_000028's grade (verifier
import requests, a separate gap); the capability repaired is the missing
forced-exit on a degenerate loop, systemic across the cluster. If a passing task
ever issues one command 5x in a row it would be terminated early - the sweep
shows none do at maxrun 5-plus, so risk is very low. Watch next round for any
loop_detected regression per rollback_trigger.
