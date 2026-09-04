# Candidates — R1/c0

Assigned focus: `task_000010_644ab1c2` (fails, `exit_reason=budget_exceeded`).

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Add the existing `LoopDetectionProcessor` to the pipeline so an identical
tool-call-with-identical-output run is warned early (and hard-stopped as a
backstop), instead of silently burning the entire step budget.

- Tasks affected: task_000010_644ab1c2 (primary). The mechanism —
  identical-Bash-call repetition loops that neither `LengthTruncationRecovery`
  (fires only on `finish_reason=length` with NO tool call) nor `ParseRetry`
  (fires only on parse errors) can catch — is a benchmark-wide failure class,
  not a single-task quirk. Any task where the agent gets stuck re-issuing an
  ineffective command falls in the same bucket.
- Signal: `result.json` → `agent.exit_reason = "budget_exceeded"`, `steps = 80`.
  Messages 2–59 are the *exact same* assistant turn ("The zombie processes are
  still there. Let me try a different approach…") emitting the *identical* Bash
  command `ps aux | grep -E "python|socat" | grep -v grep | awk '{print $2}' |
  xargs -r kill -9 2>/dev/null; sleep 2; …` and receiving the *identical* tool
  output (defunct PIDs 115/130/139) every single turn.
- Verified (Read messages.json):
  - step 2 assistant: `CALL:{"command": "ps aux | grep -E \"python|socat\" ...
    xargs -r kill -9 ...; sleep 2; ps aux | grep ..."}` → tool result: three
    `[python3] <defunct>` zombie lines.
  - steps 4,6,9,11,…,59: byte-identical assistant content AND byte-identical
    tool output — ~27 consecutive identical (call, result) pairs.
  - step 56 assistant even narrates "I've been repeating the same command many
    times"; step 58 "I've been stuck in a loop" — the model *recognises* the
    loop but the passive run-loop continuation never forces it out.
  - step 60 (finally): abandons the loop, writes `operator.py`, and by step 65
    the script runs, creates the backup, sets up the port-forward, and begins
    applying manifests — i.e. once unstuck the agent makes real progress, but
    only ~20 steps of budget remain and the run ends before both manifests are
    applied (`final_pytest`: only config.yaml logged, deploy-v2.yaml missing).
- Why Configuration not Control: the correct mechanism already exists as a
  fully-implemented, contract-clean component
  (`harnessx.processors.control.loop_detection.LoopDetectionProcessor`,
  `_order=20`, compaction-aware). The deficiency is purely that it is *absent
  from this recipe's pipeline*. Authoring a new near-duplicate processor would
  add maintenance surface and risk regressions for zero benefit. The right
  lever is to register the existing one (tuning its warn/raise knobs).
- Why not Instruction: the model already *knew* it was looping (it says so at
  steps 56/58) — a prompt rule telling it "don't loop" would not have changed
  behaviour, because the blocker is the passive continuation, not missing
  knowledge. A mechanical guard that injects a corrective warning into the tool
  result (and hard-stops as a backstop) is what breaks the cycle.
- Retroactive check (A-corrective): yes. With `warn_threshold=3`, a strong
  "you are stuck in a loop, try something fundamentally different" warning is
  appended to the tool result by step ~5 instead of the model self-recovering
  at step ~58. That returns ~50 steps of budget, which the tail of the actual
  trajectory shows is enough to write and run the operator script and apply
  both manifests. Even in the worst case the `threshold=8` raise converts a
  full-budget burn into a clean `loop_detected` exit, freeing round compute.

### Pareto framing

- expected_global_gain: closes the "identical-call repetition loop →
  budget_exceeded" failure class benchmark-wide. This is the single most
  wasteful failure shape (an entire 80-step budget on one stuck command) and
  the fix both (a) gives real recovery budget back via the early warning and
  (b) caps worst-case compute via the backstop raise.
- regression_risk: Low. The processor is warn-first (append text to tool
  result) and only raises on **5 more** consecutive *byte-identical* tool calls
  after the warn (threshold=8). Legitimate work almost never issues 8 identical
  calls with identical output in a row; interleaving any different call resets
  the consecutive counter. It is compaction-aware, so post-compaction message
  drops clear stale fingerprints and cannot cause false positives. No
  already-passing cluster relies on being allowed to repeat one command 8×.
- cost_shift: Net negative (saves cost). Early warning shortens loops; the
  backstop caps runaway tasks at 8 steps instead of the full 80-step budget.
  Added per-step overhead is a sha256 of the tool-call summary — negligible.

### Rollback trigger

If R2 shows task_000010 still fails AND any previously-passing task regresses
to `loop_detected` (false-positive termination of legitimate repeated work),
revert this candidate.
