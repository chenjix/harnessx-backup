entry = '''

## Round 2 - break identical-command loops

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-17T18:30:00Z
hypothesis_id: h_repeated_command_breaker_v1
levers: [control]
predicted_affected: [task_000264_ab8c7253, task_001818_b251e5ea, task_001032_1adaccb9, task_000958_4bb2b05d]
cited_candidates: [C-002]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Close the residual budget_exceeded cluster that R1's narration-loop de-prime converted into an IDENTICAL-command repetition loop; flip the data_querying/data_processing subset (esp. 264) that is one different action away from producing its named deliverables, and reclaim wasted budget on the capability-bound tasks."
regression_risk: "Very low - the processor is warn-only and NEVER raises. Two currently-passing tasks (536, 1089) each issue 27 consecutive identical commands and still pass; a hard loop-raise would have regressed them, which is exactly why the stock LoopDetectionProcessor was rejected in favour of a warn-only variant."
cost_shift: "Neutral-to-negative: a few-hundred-char directive appended to at most one tool result per repeated run; earlier loop-break reduces steps/tokens on the affected cluster. No new model calls."
rollback_trigger: "Revert if R3 shows tasks 536 or 1089 regress T->F, OR net pass-rate drops below R1 32/50."
-->

### Why

R1 (accepted, 29->32/50, +3/-0) de-primed the pure-narration finish_reason=length
loop. But the residual budget_exceeded-at-80-steps cluster re-manifested with a
different shape: the model now issues a tool call every turn but re-issues the
EXACT SAME Bash command over and over, each returning the same result, and never
produces the task required output artifacts. Fingerprinting the R1 messages logs:
958 = 33/33 identical, 1818 = 27/29, 1032 = 25/29, 264 = 21 consecutive identical
heredoc writes to an intermediate SQL file. This is a genuinely new signal
(identical-command repetition, not length truncation) and no existing guard closes it.

### Changes

- processors/repeated_command_breaker.py - new RepeatedCommandBreakerProcessor
  (singleton group tmax_repeated_command_breaker, _order=21). On consecutive
  identical tool-call fingerprints it appends an escalating, deliverable-grounded
  directive to the tool result at warn_at=3 (verify/produce the required output
  artifacts; stop re-running the same command) and a hardened one at escalate_at=5.
  Warn-only - never raises. Contract-safe (mutates only ToolResultEvent.result).
- config.yaml - insert the processor after LengthLoopDeprimeProcessor, before
  CompactionProcessor.

### Evidence

- task_000264 last 16 messages: identical heredoc write to an intermediate SQL
  file re-issued back-to-back, each returning (exit 0, no output captured); the
  [EditDetection] over-editing warning fires 3x and is IGNORED - proving the
  generic nudge is too weak for this model. A grep of all 29 tool-call args finds
  ZERO references to the required deliverables (the two named output files): the
  agent had a correct recursive CTE but never ran it to produce output.
- task_000958 repeated command (33x): a broken-server restart loop
  (pkill; run server; ps grep), no progress.
- Pareto guard (why warn-only, not the stock raise): task_000536 (audit script
  re-written 27x) and task_001089 (malformed empty tool call 27x) BOTH PASS despite
  27 consecutive identical calls. The identical-command signature does not
  discriminate pass from fail, so LoopDetectionProcessor raise-at-5 would be a
  2-task regression. Passing long-horizon tasks otherwise have
  max_consec_identical < 3 and are untouched.

### Uncertainty

The directive is advisory; a weak model may ignore it just as it ignored the
edit-warning on 264 (the escalation + deliverable-grounding is the bet on why it
lands this time). For the C++ HTTP microservice tasks (958, 1701) the block is a
capability gap - this reclaims budget/cost but a flip is not expected there. If R3
shows no flips AND no regressions, the mechanism is safe but the remaining cluster
is capability-bound; if 536/1089 regress, revert immediately.
'''

path = "/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep2-i1/learnings.md"
with open(path, "a") as f:
    f.write(entry)
print("appended")
