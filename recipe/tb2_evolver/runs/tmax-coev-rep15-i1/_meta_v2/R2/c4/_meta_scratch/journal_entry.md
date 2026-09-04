

## Round 3 — identical-command loop breaker (c4)

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-30T07:30:00Z
hypothesis_id: h_repeated_command_loop_breaker_v2
levers: [control]
predicted_affected: [task_000264_ab8c7253, task_001031_a8f0eb37, task_001321_658ce4a8]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
retry_rationale: "The R1/c5 sibling h_repeated_command_loop_breaker_v1 was a parallel proposal that never merged into the accepted R1/c2 -> R2 config lineage (outcome pending, not reverted), so this mechanism has never actually run against the benchmark. task_000264 is STILL failing budget_exceeded in the r1 trajectories with the exact 23x-identical-command loop the c5 processor was designed for, and the current config has no guard for tool-calling loops (only finish_reason=length). Re-scoped raise_threshold 5->6 for more recovery headroom before reclaiming budget."
expected_global_gain: "Closes the identical-Bash-command budget_exceeded cluster (task_000264 recursive-CTE 23x-identical, task_001031 mpi4py 33x-identical, task_001321 extractor 19x-identical). warn redirect gives recovery headroom to actually write required outputs; raise reclaims wasted steps on unrecoverable loops. Generalizes to any verbatim-command loop, content-agnostic."
regression_risk: "Low. Fingerprints only substantive Bash commands (non-empty, above min length); empty/trivial/non-Bash calls are neutral interludes that neither count nor reset, specifically protecting the passing many-empty-call recovery task_001089_220cc46b. warn precedes raise by 3 turns. New singleton group tmax_repeated_command_breaker; additive; contract-clean."
cost_shift: "Net negative — looping tasks exit early instead of burning full 80-step budget; ~0 added cost on non-looping tasks (one appended sentence per warn, only inside an active verbatim loop)."
rollback_trigger: "If R4 shows task_000264 still budget_exceeded (loop unbroken) AND none of the 3 predicted tasks flip, OR any previously-passing task regresses to loop_detected via the breaker firing (esp. task_001089_220cc46b), revert the RepeatedCommandLoopBreaker entry."
-->

### Why

Assigned focus task_000264_ab8c7253 fails exit_reason=budget_exceeded at the
full 80-step cap, reward=0, with ALL THREE required output files missing
(top_managers.csv, query_plan.txt, and the manager_id index). The trajectory
tail is a textbook identical-command loop: from msg 23 through msg 69 the model
issues the BYTE-IDENTICAL Bash command
`sqlite3 /home/user/company.db "EXPLAIN QUERY PLAN ... JOIN hierarchy h ..."`
23 consecutive times. `hierarchy` is a CTE name referenced from a standalone
statement, so it can never resolve — every call returns the identical
`Error: in prepare, no such table: hierarchy (1)`. On each of those 23 turns the
assistant narrates the verbatim sentence "I've been stuck in a loop ... let me
try a completely different approach" and then re-issues the SAME command. The
current config's only loop guard, LengthTruncationRecoveryProcessor, fires only
on finish_reason==length with NO tool call, so this tool-calling verbatim loop
is completely unguarded and the whole step/wall-clock budget drains with zero
progress. The same mechanical pathology recurs on task_001031 (mpi4py snippet,
33x consecutive identical) and task_001321 (extractor invocation, 19x
consecutive identical) — a loop pathology, not a missing capability.

### Changes

- `processors/repeated_command_breaker.py` — new `RepeatedCommandLoopBreaker`
  MultiHookProcessor (singleton group `tmax_repeated_command_breaker`,
  `_order=21`). Fingerprints only substantive Bash commands (non-empty, above
  min_command_chars); empty/trivial/non-Bash calls are neutral interludes
  that neither increment nor reset the consecutive-identical counter. At
  warn_threshold (3) appends a decisive redirect to the tool result telling the
  model to reconsider the root cause and change the command materially or
  finish; at raise_threshold (6) raises LoopDetectedError, converted by the run
  loop into a clean exit_reason=loop_detected with best-output recovery.
  Content-agnostic. (C-001)
- `config.yaml` — registered the new RepeatedCommandLoopBreaker
  (warn_threshold=3, raise_threshold=6, min_command_chars=12, tool_name=Bash)
  right after LengthTruncationRecoveryProcessor; everything else byte-identical
  to R1/c2 (service_deps_reminder path unchanged).

### Evidence

- `task_000264_ab8c7253` messages.json: msg 23 and msg 35 tool_call arguments
  are byte-identical (`EXPLAIN QUERY PLAN ... JOIN hierarchy ...`); tool results
  24..69 are all identical `Error: in prepare, no such table: hierarchy (1)
  (exit 1)`; assistant narration "I've been stuck in a loop ... try a completely
  different approach" repeats ~23 times. result.json:
  exit_reason=budget_exceeded, 80 steps; final_pytest "Output file missing at
  /home/user/top_managers.csv" + "Query plan file missing" + "No index found on
  manager_id".
- `task_001031_a8f0eb37` / `task_001321_658ce4a8` (per R1/c5 diagnosis): last
  turns cycle a byte-identical command; max consecutive-identical runs 33 and 19
  respectively; both budget_exceeded.
- Regression guard: task_001089_220cc46b (reward=1) makes many consecutive
  empty-argument calls; the breaker ignores empty/trivial calls so it never
  counts them and never fires.

### Uncertainty

The warn redirect flips a task only if the model, once told to change the
command, can actually produce the correct query/output within remaining budget.
If the underlying logic gap is unrecoverable, raise still ends the task cleanly
as loop_detected (still reward=0) but sooner — a cost win, not a pass win. If R4
shows task_000264 still budget_exceeded with the breaker present, the loop is
not the only blocker (genuine capability gap on recursive-CTE) and the next
round should look upstream. Watch task_001089_220cc46b for any false-positive
loop_detected regression.
