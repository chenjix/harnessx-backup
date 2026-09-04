
## Round 1 (c5) — recover from verbatim tool-result loops

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-28T13:00:00Z
hypothesis_id: h_stuck_result_breaker_v1
levers: [control]
predicted_affected: [task_000206_a943669b, task_000958_4bb2b05d, task_000118_3043e92d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the verbatim-loop-burns-80-step-budget cluster (three-plus tasks) by breaking the fixation in place and returning control while budget remains — a recovery strategy complementary to terminate-on-loop."
regression_risk: "Low — append-only note on a tool result after three-plus consecutive byte-identical outputs, a shape no R0-passing task shows; never drops/rewrites messages, never terminates. Contract check passes (append-only on_after_tool)."
cost_shift: "Net decrease — breaks dead loops at repeat three instead of letting them run to step 80, reclaiming tens of steps per stuck task; the note is about 90 tokens and fires only inside an active loop."
rollback_trigger: "If R3 shows any previously-passing task regressing, or the three cited tasks still budget_exceeded with the loop unbroken, revert (raise warn_threshold first if the nudge is merely too weak)."
-->

### Why

Assigned focus task_000206_a943669b exited budget_exceeded at step 80 with a
malformed findings.txt. The jq binary rejects hyphenated keys
(csp-report / blocked-uri) as "report/0 is not defined", and the model
re-issued byte-identical failing jq commands with verbatim assistant text
about 13-plus times in a row (messages.json rows 11-533) — the first third of
the step budget spent making zero progress. It escaped only after a
PostCompaction refresh, then had no budget left to fix its buggy analyze.sh.
The pipeline had no recovery hook for repeated identical tool results:
ParseRetryProcessor handles only unparseable model output, CustomEditToolProcessor
counts only write commands, and CustomSelfVerifyProcessor never fires on a
budget-exhausted run. Two other round-0 tasks share the mechanism
(task_000958 sqlite3 x18, task_000118 wait/ps x13).

### Changes

- processors/stuck_result_breaker.py — new StuckResultBreaker
  MultiHookProcessor. On on_after_tool, tracks consecutive byte-identical
  results per tool; at warn_threshold (3) appends an escalating corrective note
  ("this exact output has repeated N times — stop, switch mechanism") and at
  hard_threshold (6) a stronger note; reset_after_nudge re-fires if the loop
  persists. Append-only, non-terminating, content-agnostic (no task/path/
  syntax literals).
- config.yaml — register StuckResultBreaker at _order 35 (after
  CustomEditToolProcessor, before CustomSelfVerifyProcessor) via absolute
  file:// path. Rest byte-identical to R0.

### Evidence

- task_000206_a943669b messages.json rows 11-533: assistant text "The issue is
  that jq is interpreting report and uri ..." plus tool result "jq: error:
  report/0 is not defined ... (exit 3)" repeat identically over 10 times before
  any tool switch; result.json exit_reason=budget_exceeded, steps=80, reward=0,
  initial_pytest.passed=true; final findings.txt has empty "Attacker IP:" and a
  doubled Blocked URI line.
- task_000958_4bb2b05d, task_000118_3043e92d: prior-round sweep (learnings.md
  lines 108-114) documents sqlite3 x18 and wait/ps x13 consecutive-identical
  loops — same mechanism.

### Uncertainty

Distinct from the c3 proposal (LoopDetectionProcessor, configuration lever,
terminate-on-loop): this is a recovery nudge, not a kill switch. If the model
ignores the note and keeps looping, the reclaimed-budget benefit does not
materialize and the task may still fail — but the append-only design cannot
itself regress a passing task. If R3 shows the nudge too weak (loops persist),
raise the cadence or defer to a terminate-on-loop guard; if a passing task
regresses, revert.
