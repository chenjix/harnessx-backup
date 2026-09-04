# Candidates — R2 (c4)

## Candidate C-002
[lens: failure | lever: control | intent: corrective]

Add a `RepeatCommandBreakerProcessor` that detects when the same Bash
command produces the same output ≥N times consecutively and injects a
one-shot corrective nudge to break the degenerate no-progress loop.

- Tasks affected (assigned focus + cluster):
  - `task_000140_01c78b42` (assigned) — reward 0
  - `task_000010_644ab1c2` — reward 0, `exit_reason=budget_exceeded`
  - `task_000015_89886d8d` — reward 0, `exit_reason=budget_exceeded`
  - `task_001818_b251e5ea` — reward 0
  - (also observed: task_000118 21×, plus non-fatal cases 000329/000958)
- Signal: consecutive-identical-command run length per task (from a
  sweep of every `messages.json`): 000010=28, 000015=26, 000329=26,
  000118=21, 001818=19, 000140=15. Verified that command **and** result
  are byte-identical across the whole run for 000010/000015/001818
  (`outputs_identical=True`). Two of these tasks hit
  `exit_reason=budget_exceeded` — the loop consumed the entire step
  budget. 5 of the 6 top-repeat tasks fail; the one that passes (000329)
  only repeats a harmless post-completion "Task completed" echo, which
  the threshold (4) still tolerates because it does not block a passing
  run — the nudge merely suggests changing command, it never removes a
  tool call.
- Verified (Read of `task_000140_01c78b42/messages.json`):
  - steps 2–31: 15 consecutive `kill -9 353 587 815 1030 ...; ps aux |
    grep vm_service` calls, each returning the identical listing of four
    `[vm_service] <defunct>` (zombie) processes. Zombies cannot be
    killed, so every iteration is a permanent no-op.
  - steps 38–53: switches to `pkill -9 -f vm_service; ... ps aux` and
    gets `(exit 137, no output captured)` identically ~8 times.
  - step 54: context compaction fires (loop finally interrupted by a
    different mechanism, too late — the summary lost the task plan).
  - Final failure `test_no_lingering_service_processes` lists 7 lingering
    PIDs — the loop never reaped them and the agent never fixed
    `test_pipeline.sh` (rc 127) because it spent the whole session in the
    kill loop.
  - Verified `task_000010_644ab1c2/messages.json`: 28× identical
    `ps aux | grep -E "python|socat" | ... | xargs kill` with identical
    output, then `budget_exceeded`.
- Why Control not Configuration: no existing processor covers
  result-loop detection — `LengthTruncationRecoveryProcessor` only fires
  on `finish_reason=length` (no tool call), a disjoint shape. A sibling
  proposal in this batch (`h_loop_detection_pipeline_v1`) attacks the
  same cluster via Configuration — registering the stock
  `LoopDetectionProcessor` with warn@3 / **raise@8** (a hard
  `loop_detected` termination). That backstop is risky here: its own
  evidence shows task_000010 recovered and made real progress at *step
  60*, after ~27 loop iterations — a hard raise@8 would have KILLED that
  run before it recovered. My design deliberately **never terminates**:
  it only injects a corrective, zombie-aware nudge and lets the agent
  keep its full budget, so it cannot convert a late-recovering task into
  a hard loss. It also keys on (command, result) identity rather than a
  tool_call_summary hash, and escalates wording on a persistent loop.
  This is the safer shape for the same cluster; the two proposals are
  intentionally different bets (Control-nudge-only vs Configuration-with-
  hard-raise) so the batch can compare them.
  Why Control not Instruction (addendum): the agent has no *self-monitor*
  for "same command → same output N times" — task_000010's own
  narration ("I've been repeating the same command", "I've been stuck in
  a loop") shows the model *knows* it is looping yet cannot break out.
  A prompt rule cannot count repeats mid-loop; a Control hook can.
- Why Control not Action: the agent's action space is fine (Bash suffices);
  the gap is a missing mechanical guard around the loop, not a missing
  capability.
- Retroactive check (A-corrective): yes — if the nudge had fired at
  step 6 of task_000140 (4 identical kills), the agent would have been
  told the command is not progressing and to change approach (and that
  `<defunct>` zombies cannot be killed), reclaiming ~50 steps to actually
  fix `test_pipeline.sh` and the process lifecycle. For 000010/000015 the
  loop *was* the terminal blocker (`budget_exceeded`) — breaking it
  directly returns budget to the task.
- expected_global_gain: closes a benchmark-wide degenerate-loop cluster
  (≥5 failing tasks show 15–28× identical command/result runs, 2 of
  them terminal `budget_exceeded`). Purely mechanical + content-agnostic,
  so it generalizes to any future task where the model gets stuck
  re-issuing an unchanging command.
- regression_risk: Low. The processor never blocks or rewrites a tool
  call — it only appends one user message after the loop is already
  proven stuck (threshold 4 consecutive *identical* command+result
  pairs). A legitimately-passing task that happens to echo the same line
  a few times (e.g. 000329's post-completion narration) is unaffected
  because (a) it has already produced its answer and (b) the nudge only
  *suggests* a different command, it removes nothing. Threshold 4 is well
  below the 15–28 seen in failures but above normal 2–3× polling.
- cost_shift: Neutral-to-negative (saves tokens). Injects ~120 tokens
  once (twice at most) per stuck task, but *prevents* the 15–28 wasted
  identical tool round-trips that currently dominate those trajectories —
  net token/step reduction on exactly the tasks it fires on.
