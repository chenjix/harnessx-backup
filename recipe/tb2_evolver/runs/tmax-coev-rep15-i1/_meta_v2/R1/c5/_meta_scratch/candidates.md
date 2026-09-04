# Candidates — R1/c5 (focus: task_000264_ab8c7253)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

New `RepeatedCommandLoopBreaker` processor: detect consecutive identical
non-trivial Bash commands, inject an early forceful redirect (warn_threshold=3),
and end the task cleanly via `LoopDetectedError` (raise_threshold=5) so a stuck
command loop no longer burns the whole 80-step / wall-clock budget.

- Tasks affected: task_000264_ab8c7253, task_001031_a8f0eb37, task_001321_658ce4a8
- Signal: `exit_reason=budget_exceeded`, `steps=80` on all three; the visible
  (post-compaction) messages show the SAME Bash command fingerprint repeated
  consecutively — max consecutive-identical run = 6 (000264), 33 (001031),
  19 (001321). The model re-narrates "let me try a fundamentally different
  approach" then re-issues the byte-identical command.
- Verified (Read of messages.json):
  - task_000264 turns: fingerprint `ab3bb5` (`sqlite3 ... WITH RECURSIVE
    subordinates ...`) issued 8× total, last 3 turns identical + earlier runs;
    each returns `Error: command timed out after 15s`; assistant content
    "The user is right - I've been stuck in a loop..." repeated verbatim ~10×.
    Final CSV `top_managers.csv` left 0 bytes → test_csv_output fails.
  - task_001031 last 4 assistant turns: identical `python3 << 'PYEOF' from
    mpi4py import MPI ...` snippet, identical narration "The newer mpi4py API
    has changed significantly. Let me try a different approach..."; 33
    consecutive identical.
  - task_001321 last turns: identical `echo "PROD-ABC-1234" | /home/user/
    extractor ...` command, narration "The output is still not being
    captured..."; 19 consecutive identical.
- Why Control not Configuration (register stock LoopDetectionProcessor):
  the stock `LoopDetectionProcessor` exact strategy counts degenerate
  **empty-argument** tool calls (`{}`) as identical repeats. A *passing* task
  (task_001089_220cc46b, reward=1) makes 22 consecutive empty-`{}` calls and
  still recovers — registering the stock detector at any threshold ≤22 would
  flip that pass to `loop_detected` (regression). The custom processor
  fingerprints only substantive Bash commands (non-empty, ≥12 chars) and
  treats empty/trivial calls as neutral interludes, so it fires on the real
  loop cluster while leaving the empty-call recovery pattern untouched.
- Why Control not Instruction: the model *already* narrates "I've been stuck
  in a loop, let me try something different" every single turn and still
  re-issues the identical command — a prompt rule telling it to break loops
  is exactly what it is already (uselessly) saying. Only a mechanical hook
  that changes the tool result / terminates the run can break the cycle.
- Retroactive check (A-corrective): partial-yes. The `warn_threshold=3`
  redirect fires ~5–15 turns before budget exhaustion on all three tasks,
  while step budget remains, giving the agent (which demonstrably can run
  varied diagnostics — task_000264 turn 45 ran `ls -lh`, turn 47 `cat`) a
  chance to change approach and produce correct output. The `raise_threshold=5`
  backstop guarantees the wasted wall-clock (1410s on 000264, burned entirely
  in a loop) is reclaimed as a clean `loop_detected` exit even when the agent
  cannot recover. Net: plausibly flips loop tasks with residual capability;
  strictly reclaims budget on the rest.
- expected_global_gain: closes the identical-command repetition-loop failure
  class harness-wide (3 confirmed tasks this round, generalizes to any task
  where the model degenerates into re-issuing one failing command); early
  redirect gives recovery headroom, raise reclaims budget.
- regression_risk: a legitimate task that must re-run the exact same
  substantive command ≥5× in a row (rare — retries usually vary flags/paths).
  Mitigated by: (a) ignoring empty/trivial calls (protects the observed 22×
  empty-call passing task), (b) warn-before-raise, (c) `loop_detected` is a
  clean exit with best-output recovery, not `error`.
- cost_shift: net cheaper — looping tasks terminate early (reclaim up to ~70
  wasted steps / >1000s wall-clock each) instead of burning the full budget;
  ~0 added cost on non-looping tasks (cheap per-call fingerprint).
