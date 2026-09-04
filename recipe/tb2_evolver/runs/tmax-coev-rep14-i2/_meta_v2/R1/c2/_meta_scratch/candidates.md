# Candidates — Round 1 (proposal c2)

Assigned focus: `task_000028_7fe033ac` fails (`reward=0`, `exit_reason=budget_exceeded`, 80 steps).

## Diagnosis

Task: fix an nginx→C++/ffprobe microservice (routing, socket, frame-count
backend, logrotate). The agent actually *reached a working end-state* twice
(`Status: 200 / Body: '150\n'`), but never converged inside budget. The
trajectory is dominated by **repeated, non-progressing actions**:

- A **zombie-reaping loop**: the agent ran `kill -9` / `pkill -9 -f server`
  against `<defunct>` PIDs over and over. Zombies cannot be killed — they need
  their parent reaped — so every attempt returned `(exit 137, no output
  captured)` and the state never changed. The agent kept issuing the identical
  approach.
- **Length-truncation narration loops**: `"Let me try a different approach."`
  repeated verbatim until the output-token cap, twice (visible in the two
  `[response truncated by harness ...]` markers and two `Please continue`
  nudges).
- Repeated identical probe calls (e.g. the same
  `python3 -c "import urllib.request; ... urlopen('http://127.0.0.1:8080/')"`
  three times).

Net effect: the run burned 80 steps on repetition and exited
`budget_exceeded` rather than finishing. This is a **harness deficiency**, not
a capability gap — the model *found* the answer but had no mechanism to break
its own action-level loops early enough to spend the remaining budget on
convergence.

The `final_pytest` `ModuleNotFoundError: No module named 'requests'` is a
verifier-phase artifact (verifier test injected after agent exit; see
tb2-playbook), not something the agent could fix — so the real lever is the
budget-waste loop.

## Candidate C-001 — add action-level loop guard

- **Tag**: lens=control-loop-waste / lever=configuration / intent=corrective
- **Change**: insert the existing (well-tested, currently-unwired)
  `harnessx.processors.control.LoopDetectionProcessor` into the pipeline,
  ordered `_order=20` so it sits after compaction (`_order=8`) and parse-retry
  (`_order=10`).
  - `warn_threshold: 3` — inject a "you are looping, try something
    fundamentally different" nudge on the 3rd identical consecutive Bash call.
  - `threshold: 5` — raise `LoopDetectedError` (caught by the run loop →
    clean `loop_detected` exit) on the 5th, freeing the round's compute for
    other tasks instead of grinding to `budget_exceeded`.
  - `name_warn_threshold: 999` — **Strategy 2 (name-only) disabled**: the
    agent has only `Bash`, so ≥8 consecutive Bash calls is normal; leaving it
    on would spam false-positive warnings on every long task.
  - `window_size: 12`, `compaction_drop_threshold: 5` — defaults; the latter
    matches the pipeline's other compaction-aware processors.

- **Signal**: `task_000028_7fe033ac` `exit_reason=budget_exceeded`, 80 steps;
  visible messages show the zombie-kill approach repeated identically returning
  `(exit 137, no output captured)`, and the identical `urllib.request` probe
  issued 3×.

- **Verified body evidence**:
  - step (kill loop) tool_input `pkill -9 -f "server" 2>/dev/null; sleep 1; ps
    aux ...` → result `(exit 137, no output captured)`; the `for pid ... kill -9`
    variant immediately before returned the *identical* zombie list.
  - assistant content repeated verbatim: `"Let me try to kill all processes
    with a different approach."` many times, then
    `[response truncated by harness ... repetition loop]`.

- **Retroactive check (counterfactual)**: had `LoopDetectionProcessor` been
  active, the exact-match warn at 3 would have injected a "stop, try something
  fundamentally different" message into the zombie loop's result well before
  step 80, redirecting the agent (whose *correct* fix was `chmod`/`chown` the
  socket + start a fresh server, which it eventually did). Worst case, the
  raise at 5 converts an 80-step `budget_exceeded` into an early
  `loop_detected`, returning ~70 steps of compute to the round without changing
  this task's already-failing outcome.

- **Why configuration not action/instruction**: the fix is a
  reusable *mechanism* (detect + break repeated non-progressing actions), not
  task knowledge. Encoding "don't kill zombies" in the system prompt would be
  task-specific and fail hard-invariant #4. The processor already exists and
  generalizes to every task in the suite.

## Pareto statement

- **expected_global_gain**: reduces the `budget_exceeded` / repetition-loop
  cluster (any task where the model repeats an identical failing command).
  Returns wasted compute to the round even when the specific task still fails.
- **regression_risk**: a genuinely-legitimate identical retry (e.g. polling
  `sleep 2 && curl ...` waiting for a service to come up) could trip the warn
  at 3. Mitigated: warn is a soft nudge; raise threshold is 5 (rare to issue
  the *byte-identical* call 5× in a row on purpose); Strategy 2 disabled to
  avoid Bash-only false positives.
- **cost_shift**: net **negative** (lower) — cuts off runaway loops earlier;
  small per-call overhead from one extra processor is negligible.
- **rollback_trigger**: if next round's pass-rate drops OR `loop_detected`
  exits appear on tasks that were previously passing, revert (raise `threshold`
  or lower to warn-only).
