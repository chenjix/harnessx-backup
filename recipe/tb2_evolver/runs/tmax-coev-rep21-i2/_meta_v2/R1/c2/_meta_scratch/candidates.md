# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatedCommandGuard` processor that detects N consecutive
identical Bash commands and injects an escalating loop-break nudge
into the tool result, forcing the model to change approach instead
of re-issuing the same failing command until budget is exhausted.

- Tasks affected (assigned focus + wider failing cluster, same mechanism):
  - task_000015_89886d8d (assigned) — reward 0, exit budget_exceeded
  - task_001031_a8f0eb37 — reward 0, budget_exceeded, 29x identical cmd
  - task_000011_d089ef35 — reward 0, budget_exceeded, 27x identical cmd
  - task_000477_de422e4d — reward 0, budget_exceeded, 26x identical cmd
  - task_000313_1dce9844 — reward 0, budget_exceeded, 18x identical cmd
  - task_000683_7c966a71, task_000958_4bb2b05d — 11x identical cmd, budget_exceeded
  - (systemic: every task with max consecutive-identical-command run >= 7
    in this round has reward 0)
- Signal: `exit_reason=budget_exceeded` cluster dominated by long runs of
  byte-identical consecutive `tool_calls[].function.arguments` producing
  byte-identical tool results. Baseline pass rate this round = 9/50 (18%);
  the identical-command-loop tasks are almost entirely inside the failing set.
- Verified (Read of message logs):
  - task_000015_89886d8d steps 2-7: identical
    `tesseract /app/routing_schema.png output_base ...` command issued 3x
    with identical "Invalid resolution 0 dpi" output; later steps 64-69:
    identical `sed -i "s/'department'.../" && python3 migrate.py` command
    issued 3x with identical `KeyError: 'dept'` traceback (the sed was a
    no-op so the error never changed). Agent burned its 80-step budget in
    these two loops and never wrote `/home/user/test_parser.py`.
  - task_001031_a8f0eb37: exact command
    `python3 -c "from mpi4py import MPI; help(MPI.Comm.Allgatherv)" | head -30`
    issued 29 times consecutively — a pure model loop, budget_exceeded.
- Why Control not Configuration: the existing `CustomEditToolProcessor`
  (threshold=7) only counts *file-write* commands (redirect / sed -i / tee),
  so pure read/exec loops (tesseract, `python -c help(...)`, no-op sed) slip
  past it, and 7 is far too high — the loops here are already fatal by 3
  repeats. `LengthTruncationRecoveryProcessor` only fires on
  `finish_reason=length`, not on repeated *completed* commands. There is no
  existing knob that intercepts a "same command -> same output" loop, so this
  is a new mechanical hook (Control), not a re-parameterisation.
- Why Control not Instruction: the model already *narrates* "I've been stuck
  in a loop, let me try a different approach" and then re-issues the identical
  command anyway (task_000015 steps 2/4/6, 64/66/68). A prompt rule telling it
  "don't repeat commands" would not help — it already believes it is not
  repeating. A mechanical guard that injects fresh, escalating text into the
  tool result at the moment of repetition breaks the self-reinforcing context
  the model is copying from.
- Retroactive check (A-corrective): yes — in task_000015 the loops consumed
  the entire budget before `test_parser.py` was ever created and before the
  `KeyError` was actually fixed; interrupting at 3 repeats with an escalating
  nudge frees ~60% of the step budget for the model to try a genuinely
  different fix and to write the missing required file. In task_001031 the
  29x loop would have been cut at 3, leaving budget to finish.
- expected_global_gain: targets the largest failing cluster
  (budget_exceeded via identical-command loops); plausibly flips several
  of the 41 failing tasks by reclaiming wasted budget. Generalizes because
  the mechanism is model-behavioural, not task-specific.
- regression_risk: low. Legitimate polling (`sleep N && curl ...`) rarely
  repeats a byte-identical command 3+ times back-to-back with identical
  output, and the guard only *appends advisory text* to the tool result —
  it never blocks execution or kills a command, so a genuine retry still
  runs. Threshold 3 chosen to sit above 1-2 benign re-runs. No passing task
  in this round had maxCmdRun >= 4 except task_000747/001653 (already
  reward 0), so no currently-passing task is touched.
- cost_shift: net negative (saves tokens/cost) — loops that currently burn
  10-29 identical model+tool round-trips get cut to ~3-6, freeing budget.
