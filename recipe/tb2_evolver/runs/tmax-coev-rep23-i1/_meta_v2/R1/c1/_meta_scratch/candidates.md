# Candidates — R2 (c1)

Assigned focus: `task_000015_89886d8d` fails (`exit_reason=budget_exceeded`,
steps=80, reward=0). Diagnosis in C-001 below.

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Wire the existing `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
into the Tmax pipeline (warn at 3 consecutive identical tool calls, hard-raise
`LoopDetectedError` at 6) so the agent stops burning its whole step budget
re-issuing a byte-identical Bash command that returns byte-identical output.

- Tasks affected (failing, same mechanism — identical command → identical
  output repeated until budget exhausted):
  - `task_000015_89886d8d` — the assigned task. Repeated the SAME
    `python3 << ... tesseract ...` call **26 times consecutively**, each
    returning the identical `read_params_file: Can't open ...` STDERR. Burned
    all 80 steps; never wrote `/home/user/migrate.py` or `/home/user/test_parser.py`.
  - `task_000010_644ab1c2` — repeated the SAME `ps aux | grep ... | xargs kill`
    call **28 times**, identical `[python3] <defunct>` output each time.
    `budget_exceeded`, reward=0.
  - `task_000118_3043e92d` — repeated the SAME `rm -f deployment_monitor.py &&
    cat > ...` heredoc **22 times**, identical `(exit 0, no output captured)`.
    reward=0.
  - `task_001818_b251e5ea` — repeated `cargo build --release` **22 times**;
    reward=0.
  - `task_001089_220cc46b` — repeated `make clean && make test` **12 times**
    consecutively; `budget_exceeded`, reward=0.
- Signal: `exit_reason=budget_exceeded` on the failing cluster, plus a
  computed "longest run of consecutive byte-identical tool-call arguments"
  of 12–28 per task; the paired tool outputs are also byte-identical
  (`Counter(tool_outputs).most_common(1)` == the run length). The current
  `config.yaml` processor list contains NO tool-call loop breaker — only
  `LengthTruncationRecoveryProcessor`, which fires only on
  `finish_reason=length` with no tool call. These loops have
  `finish_reason=stop` and a *valid* tool call, so nothing intercepts them.
- Verified (Read of `*.messages.json`):
  - `task_000015` steps 2–56 are the same assistant message
    ("The command line parsing is failing. Let me try using a Python
    script...") + identical `Bash` args + identical
    `STDERR: read_params_file: Can't open tessedit_char_whitelist=...`.
  - `task_000010` most-repeated tool output 28× of 33:
    `root ... [python3] <defunct> ...` (verbatim).
  - `task_000118` most-repeated tool output 22× of 31: `(exit 0, no output
    captured)` (verbatim).
- Why Configuration not Control/Instruction: the exact mechanism I need —
  consecutive-identical-fingerprint detection with a warn-then-raise policy
  and compaction-aware window reset — **already exists** as a battle-tested
  harnessx builtin (`LoopDetectionProcessor`). Authoring a new processor would
  duplicate it with more bug surface and less coverage (it already handles
  tool_call_id fingerprinting, name-only secondary strategy, and compaction
  resets). An Instruction rule ("don't repeat commands") cannot fire mid-loop
  — the model is already ignoring its own repeated failures; only a mechanical
  hook that injects a warning into the tool result and ultimately raises can
  break it. So the right lever is Configuration: enable + parameterise the
  existing component.
- Retroactive check (A-corrective): yes. On `task_000015`, the raise at 6
  consecutive identical calls fires around step ~8 instead of step 80,
  returning ~72 steps of budget the agent can spend pivoting to the correct
  tesseract invocation (`tesseract IN OUT_BASE`, output is a basename not
  `-o`). The warning injected at 3 also surfaces the loop to the agent so it
  may self-correct even before the raise. Same shape flips the other four
  budget-exhausted loopers: each currently dies mid-loop with no work saved.

### Global-optimization statement

- `expected_global_gain`: Directly targets the 5-task `budget_exceeded`
  identical-command-loop cluster (task_000015, task_000010, task_000118,
  task_001818, task_001089). Even a partial flip is net-positive because these
  are dead-loss tasks today. Generalizes to any Tmax/TB2 task where a
  single-tool agent gets stuck re-issuing one command — a recurring failure
  mode with single-tool (`Bash`) agents.
- `regression_risk`: LOW, but the honest risk is the three PASSING tasks that
  also contain long identical runs (task_000329 26×, task_001652 10×,
  task_001701 15×, task_001591 11×). Checked directly: in every case the
  agent had **already completed the required work** and was merely spinning
  (re-running `ls`/`cat` or emitting empty `{}` calls) instead of calling
  end_turn. `LoopDetectedError` exits cleanly with `exit_reason=loop_detected`
  (verified in `runloop.py:781` — NOT `error`), and the container's final
  filesystem state is preserved for the verifier, so a completed-work task
  that is force-exited **still passes** (and saves ~20 wasted steps). The warn
  at 3 also gives the agent chances to end_turn on its own first. Threshold set
  to 6 (above default 5) as an extra cushion for legitimate short retry
  bursts.
- `cost_shift`: NET DOWN. Failing loopers currently spend the full 80-step
  budget; capping at ~6 identical repeats cuts ~15–25 wasted steps per looping
  task. Passing spinners similarly shed ~10–20 idle steps. Non-looping tasks
  are untouched (they never reach the consecutive-repeat threshold).
- `rollback_trigger`: If a future round shows any previously-passing task
  flipping to `loop_detected`+reward=0 (i.e. force-exited before its work was
  complete), raise `threshold` (e.g. to 10) or revert this candidate.
