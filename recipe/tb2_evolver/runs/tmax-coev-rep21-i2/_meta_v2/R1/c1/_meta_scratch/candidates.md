# Candidates

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Wire the existing `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
into the pipeline (warn at 3 identical consecutive tool calls, hard-raise
`LoopDetectedError` at 8) so degenerate identical-command repetition loops are
interrupted early instead of burning the entire step budget.

- Tasks affected (assigned focus first):
  - task_000011_d089ef35 (assigned): stuck ~40 consecutive identical `kill -9 66`
    calls on an un-killable zombie; only wrote the actual C program at step ~65,
    then hit budget_exceeded before it could validate output.
  - task_001031_a8f0eb37: 29 identical consecutive tool calls, budget_exceeded @80.
  - task_000477_de422e4d: 26 identical consecutive tool calls, budget_exceeded @80.
  - task_001382_6c9d34ea: 26 identical consecutive tool calls, budget_exceeded @80.
  - task_000313_1dce9844: 18 identical consecutive, budget_exceeded @80.
  - task_001207_44e97fe1: 13, task_000683_7c966a71: 11, task_000958_4bb2b05d: 11,
    task_001321_658ce4a8: 7, task_000118_3043e92d: 6, task_000510_b49b430c: 6 —
    all budget_exceeded @80 with the same identical-call-repetition mechanism.
- Signal: 16 tasks exit `budget_exceeded` at exactly 80 steps; a fingerprint sweep
  of assistant tool-call `name::arguments` shows ≥5 identical consecutive calls on
  10 distinct tasks (up to 29 in a row). The current pipeline has NO loop detector
  wired in — `LengthTruncationRecoveryProcessor` only fires on `finish_reason=length`,
  and `CompactionProcessor` fired (`[PostCompaction]` in task_000011) but the loop
  resumed identically afterward.
- Verified (Read):
  - task_000011_d089ef35 messages: assistant content `"The zombie process is still
    there. Let me try to use kill -9 with the process ID directly:"` + tool call
    `kill -9 66 2>&1; sleep 1; ps aux | grep mesh_server` repeated verbatim dozens
    of times; tool result `root 66 ... [mesh_server] <defunct>` identical each time.
    The loop only broke when `_tb2_self_verify` fired and the agent re-read the task.
  - Fingerprint sweep (Bash, quoted in trajectory notes): max identical consecutive
    = 29 (task_001031), 26 (task_000477, task_001382), 18 (task_000313), etc.
- Why Configuration not Control/Instruction: the corrective mechanism already exists
  as a tested builtin (`LoopDetectionProcessor`, two-phase warn→raise, compaction-aware
  fingerprint reset). Authoring a new Control processor would duplicate it; an
  Instruction rule ("don't repeat yourself") cannot mechanically interrupt a model
  that is already ignoring identical tool output. The gap is purely that the existing
  component is not wired into this config — a Configuration-lever fix.
- Retroactive check (A-corrective): yes — the warn injection at 3 repeats is exactly
  what broke the loop in task_000011 when self-verify accidentally forced a re-read;
  applied at 3 (instead of ~40) it would free ~35 steps for the real task. For the
  29/26/18-repeat tasks the hard raise at 8 caps a pure degenerate loop that otherwise
  consumes all 80 steps, and (per runloop.py) `loop_detected` still lets a partial
  final output stand rather than a silent budget wipe.
- expected_global_gain: interrupts the dominant `budget_exceeded @80` failure cluster
  (16 tasks, ≥10 with clear identical-call loops); frees steps for real task work.
- regression_risk: a legitimate identical poll-loop (e.g. `sleep 1; check port`
  repeated while waiting) could trip the raise. Mitigated by raise threshold=8
  (well above normal 2-3 poll retries) and warn-first at 3; the warn is only appended
  to the tool result, not a termination.
- cost_shift: net negative (saves tokens) — loops that currently burn 80 steps are
  capped at ≤8 identical repeats; no added cost on non-looping tasks.
