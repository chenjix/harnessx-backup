# Candidates — R3 / c4

Assigned focus: `task_000264_ab8c7253` fails `exit_reason=budget_exceeded`,
reward=0, all 3 required output files missing.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatedCommandLoopBreaker` MultiHookProcessor that fingerprints only
substantive Bash commands, warns on the Nth consecutive byte-identical command,
and raises `LoopDetectedError` for a clean budget-reclaiming exit on the Mth.

- Tasks affected: task_000264_ab8c7253, task_001031_a8f0eb37, task_001321_658ce4a8
- Signal: `exit_reason=budget_exceeded` at the full 80-step cap with **zero**
  output files written; the trajectory tail is a run of byte-identical Bash
  tool calls each returning the identical error. The current config's only
  loop guard (`LengthTruncationRecoveryProcessor`) fires exclusively on
  `finish_reason==length` with **no** tool call, so a tool-calling identical-
  command loop is completely unguarded.
- Verified (Read task_000264_ab8c7253.messages.json):
  - msg 23 tool_call args: `sqlite3 /home/user/company.db "EXPLAIN QUERY PLAN
    SELECT e.id, e.name, COUNT(*) ... FROM employees e JOIN hierarchy h ON
    h.path LIKE '%' || e.id || '%' WHERE h.depth > 0 ..."` .
  - msg 35 tool_call args: **byte-identical** to msg 23 (confirmed by direct
    string comparison).
  - msgs 24,26,...,69 tool results: identical `Error: in prepare, no such
    table: hierarchy (1)` (a CTE name referenced from a standalone statement —
    it can never resolve, so the retry is guaranteed to fail forever).
  - assistant msgs 23,25,...,68 narrate the verbatim sentence "I've been stuck
    in a loop of reasoning without making progress. Let me break out of it and
    try a completely different approach." ~23 times while re-issuing the SAME
    command. Result: budget_exceeded, `top_managers.csv` / `query_plan.txt`
    never written (final_pytest: "Output file missing").
- Why Control not Instruction: the model already *narrates* that it is stuck in
  a loop and "should try a different approach" on every one of the 23 turns —
  a prompt rule telling it the same thing is exactly what it is already failing
  to act on. The gap is mechanical: nothing in the loop detects the identical-
  command repetition and forces a state change or reclaims the wasted budget.
  A Control hook that fires on the mechanical repetition signal is the right
  layer; Instruction has already demonstrably failed here.
- Why Control not Configuration: there is no existing loop-detection knob in
  this config to retune — the generic `LoopDetectionProcessor` is not wired in,
  and wiring it naively would regress the passing task that recovers after ~20
  consecutive **empty-argument** calls (task_001089_220cc46b). A purpose-built
  processor that ignores empty/trivial calls is needed, not a knob tweak.
- Retroactive check (A-corrective): yes — with warn_threshold=3 the redirect
  lands on msg ~28 (3rd identical repeat), ~40 steps before budget_exceeded,
  giving the agent ample headroom to change the command and actually write the
  CSV / plan files. Even in the worst case where the model still cannot fix the
  logic, raise_threshold=6 ends the task cleanly as `loop_detected` and reclaims
  ~35 wasted steps (a cost win, not a regression).
- expected_global_gain: closes the identical-command budget_exceeded cluster
  (task_000264 recursive-CTE, task_001031 mpi4py 33x-identical, task_001321
  extractor 19x-identical). Generalizes to any task where the model re-issues a
  guaranteed-failing command verbatim — a content-agnostic mechanical pathology.
- regression_risk: Low. Fingerprints only substantive Bash commands
  (>= min_command_chars, non-empty); empty/trivial/non-Bash calls are neutral
  interludes that neither count nor reset — this specifically protects the
  passing 22x-empty-call recovery pattern (task_001089_220cc46b). warn precedes
  raise by 3 turns so a task that legitimately re-runs the same substantive
  command a few times gets a nudge before any hard exit. New singleton group
  `tmax_repeated_command_breaker`, additive; contract-clean (auto-check passed).
- cost_shift: Net negative. Looping tasks terminate ~35 steps early instead of
  burning the full 80-step / ~800s budget; ~0 added cost on non-looping tasks
  (one appended sentence per warn, only on tasks already in a verbatim loop).
