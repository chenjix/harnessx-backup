# Candidates — R2

## Candidate C-002
[lens: failure | lever: control | intent: corrective]

Add `RepeatedCommandBreakerProcessor`: detect consecutive byte-identical
(whitespace-normalized) Bash commands and, past a threshold, inject an
escalating redirect nudge — and at a higher threshold suppress the redundant
execution itself via `approved=False, synthetic_result=...` — forcing the
model out of a degenerate identical-command loop.

- Tasks affected (distinct mechanism, ≥2): task_001818_b251e5ea,
  task_000010_644ab1c2, task_000118_3043e92d, task_001031_a8f0eb37,
  task_000396_e56917e2 (all reward=0; several with 1000-4300s elapsed_s).
- Signal: R1 flipped the max_tokens/`finish_reason=length` crash cluster
  (agent_error dropped 7→2), but a new dominant failure shape emerged:
  tasks now end each turn *normally* with a well-formed Bash call yet grind
  to the ~33-step cap re-issuing the SAME command. Command-duplication ratio
  per failing task: task_001818 = 0.89 (26/28 identical), task_000010 = 0.35,
  task_000118 = 0.21, task_001031 = 0.21, task_000396 = 0.28.
- Verified (Read):
  - task_001818 msgs 6–68: the identical 3238-char `cat > .../main.rs <<EOF`
    heredoc is written ~20 times consecutively, each returning `(exit 0, no
    output captured)`. The existing EditDetection warning DOES fire
    (`[EditDetection] File .../main.rs has been modified more than 7 times`)
    at msgs 21/23/37/39/53/56 but the model ignores it and keeps writing the
    same bytes — never recompiling, never changing a line.
  - task_000010 msgs 2–15: identical `python3 -c "import socket ... Port 8080
    is open"` probe issued 4×, and `ps aux | grep -E "python|socat|mock"`
    issued 5×, cycling start→check→kill→retry with no adaptation until the
    step cap.
  - task_001031: identical `mpiexec -n 4 python3 ...` run 3× and `cat
    /home/user/results.json` 3× with no code change between runs.
- Why Control not Instruction: the model already RECEIVES a passive
  EditDetection text warning and ignores it — a prompt rule ("don't repeat
  commands") is the same class of passive signal and would be ignored too.
  The fix needs teeth: mechanically counting identical commands across turns
  (a cross-turn guard no per-call tool or prompt line can express) and
  physically suppressing the redundant execution so the loop cannot continue.
- Why Control not Configuration: EditDetection's `threshold` knob only tunes
  when the *passive* warning fires; it can't add execution suppression or
  cover non-file-write repeats (port probes, `ps aux`, `mpiexec`). A new
  mechanical hook is required, not a re-tuned existing one.
- Retroactive check (A-corrective): yes — for task_001818, suppressing the
  identical `main.rs` rewrite after 5 repeats and telling the agent the
  command achieved nothing frees ~15 wasted steps and forces it to react to
  the real compiler error (`E0308 mismatched types`) it saw at msg 13 but
  never addressed. For the port/ps/mpiexec loops the same reclaimed budget
  and forced-different-action give the agent turns to actually diagnose.
  Not a guaranteed pass (some tasks also have upstream logic gaps), but it
  converts guaranteed budget-exhaustion into a real chance.
- expected_global_gain: closes the largest homogeneous R1 failing cluster
  (identical-command loops), which spans system_administration,
  data_processing, scientific_computing, and data_querying — a cross-domain
  harness deficiency, not a single-task quirk.
- regression_risk: Low. On any task that never repeats an identical command
  ≥3 times the processor is a pure no-op (soft nudge only fires at streak 3+,
  suppression at 5+). A legitimate idempotent re-run (e.g. `ls` twice) tops
  out at the soft nudge unless run 5× back-to-back, which healthy runs do not
  do. Streak resets on any different command. Risk to the 31 passing tasks is
  minimal because passing runs adapt between commands.
- cost_shift: Strongly negative — eliminates thousands of seconds and dozens
  of wasted identical tool calls on the runaway failing tasks (task_000264
  4307s, task_001818 3144s, task_000118 1931s, task_001031 1433s).
