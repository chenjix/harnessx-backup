# Candidates — R2 / c3

## Candidate C-002

**Three-axis tag:** lens=exit-time verification / lever=control (processor) / intent=close a silent-wrong-output failure cluster

**Assigned focus:** `task_000109_09ddd96b` (data_science, Go WAV ETL pipeline).

### Signal (verified from trajectory)

- `result.json`: `exit_reason=done`, 23 steps, `finished=no_tool_calls`, reward=0.
  This is NOT a loop failure — the R1 `AssistantReasoningRepeatBreaker` already
  fixed the earlier `exit=error` 8x-reasoning-loop on this same task. The task
  now runs to a clean natural stop but produces **wrong numbers**.
- `final_pytest`: two assertions fail —
  - `test_window_4_imputation`: expects mean of window 4 == 0.0, agent produced
    `135.243`.
  - `test_anomaly_txt_correct`: expects `anomaly.txt == "4"`, agent wrote `"1"`.
- Trajectory msg 30/32/44: the agent's own program reported "Total imputed
  values: 2" and "Imputation proportion in anomaly window: 0.000125". The task
  premise (msg 0) is *"A recent batch of sensor data was corrupted ... silent
  conversion errors ... destroying down-stream correlation analysis"* and the
  deliverable is *"the window with the highest proportion of imputed data"*.
  Detecting only 2 corrupted samples out of 80000 flatly contradicts that
  premise — window 4 should be entirely imputed (mean 0.0) → the anomaly.
- Root cause: a silent data-decoding bug (almost certainly signed/unsigned int16
  handling) so the 8000 `-32768` samples in window 4 never matched the `== -32768`
  imputation test. The math on top was fine; the byte-level decode was wrong.
- The stock `CustomSelfVerifyProcessor` fired (msg 40–41) and the agent dutifully
  re-checked file existence, JSON validity, and window count — **all of which
  passed on the broken output**. The checklist has no step that would surface a
  result contradicting the task's own stated severity.

### Is this a harness deficiency or a capability gap?

Partly a capability gap (the specific Go int16 decode bug is model knowledge —
the harness must NOT embed WAV-parsing code). But there is a **generalizable
harness lever**: the exit-time self-verification checklist is blind to
*premise-consistency*. Across data-processing tasks, a silent parse/units/type
bug produces well-formed, plausible-looking, numerically-wrong output that the
existing "file exists / JSON valid / N items" checks wave through. A general
step — "restate the magnitude/shape the task's framing implies, and if your
computed result contradicts it, re-examine the data-loading layer" — is a
verification *habit*, not task knowledge, and would have made the agent notice
"the task says severe corruption but I found ~none" and re-check its decoder.

### Change

New processor `processors/plausibility_self_verify.py::PlausibilitySelfVerifyProcessor`,
replacing `benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor` in the
pipeline. Identical one-shot keep-alive/exit mechanics (same `_singleton_group`,
`_order=90`, same `on_after_model`/`on_before_model`/`on_before_tool` shape, so
the hook contract is unchanged and it still fires at most once). The only change
is two extra checklist items:
- (5) **Premise consistency**: restate the qualitative answer shape the task
  implies; flag when computed numbers contradict it; do not rationalize it away.
- (6) **Data-loading re-examination**: when (5) flags a mismatch, inspect the
  decode layer (signed/unsigned, endianness, field offsets, header skipping,
  units, column selection) with a raw-value diagnostic rather than re-reading
  trusted values.

No task IDs, no constants, no algorithms — passes contract auto-check.

### Retroactive check (would-this-have-helped)

- **Direct**: On task_000109, step 5 forces the agent to notice "premise = severe
  corruption; I measured 0.000125 proportion" → step 6 directs it to the int16
  decode where the signed/unsigned bug lives → likely corrects window-4 detection
  → both failing assertions flip. Not guaranteed (still needs the model to fix the
  decode), but it converts a silently-accepted wrong answer into an actively
  re-examined one, which is the only harness-level lever available here.
- **Generalization**: any data-ETL / parsing / analysis task where the true
  answer's magnitude is implied by the framing benefits — this is a broad TB2
  cluster (data_science domain), not one task.

### Why control-processor, not instruction (system prompt) or configuration

- Instruction (system prompt): the guidance is only relevant at *exit time*,
  when the agent is about to declare done with a concrete computed result to
  compare against the premise. Putting it in the always-on system prompt dilutes
  it and it would be forgotten by turn 23. The self-verify processor injects it
  exactly at the decision point — same reason the stock checklist is a processor.
- Configuration (knob tune): there is no existing knob for checklist content;
  the message is a module-level constant in the stock class, so customizing it
  requires a subclass/replacement processor.

### Pareto statement

- `expected_global_gain`: closes/attacks the "runs clean, wrong numbers" cluster
  in the data_science domain — silent parse/units/type bugs that pass the current
  file-existence-only self-check. task_000109 is the anchor; the lever generalizes
  to any task whose framing implies the answer's magnitude.
- `regression_risk`: LOW. Mechanics are byte-identical to the stock processor
  (contract-clean, one-shot, same order); only the injected text grew by two
  steps. Worst case a task that was already correct spends one extra reasoning
  turn confirming premise-consistency — no behavioral change to passing runs
  beyond a marginal token cost, and the check explicitly says "when all checks
  pass, end with SUCCESS", so it does not induce needless thrashing.
- `cost_shift`: slightly positive (a few hundred extra tokens in the single
  injected checklist message per task, plus possibly one extra diagnostic Bash
  turn on tasks that flag a mismatch). Bounded and one-shot.

- `rollback_trigger`: if a previously-passing data task newly fails with
  `exit_reason` in {loop_detected, budget_exceeded} traceable to post-self-verify
  thrashing (agent re-examines and breaks a correct decode), revert to the stock
  `CustomSelfVerifyProcessor`.
