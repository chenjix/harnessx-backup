# Candidates — R2 c4

## Candidate C-004

**Three-axis tag:** lens=exit-quality / lever=instruction / intent=close-failing-cluster

**Signal (assigned focus):** `task_000111_cbada64a` (scientific_computing)
finished cleanly — `exit_reason=done`, 7 steps, `finished=no_tool_calls`,
`initial_pytest` passed — yet `reward=0`. The verifier failed on VALUE, not
structure: `Expected m to be approx 2.5997, got 2.5056` (OLS slope off by
~0.094). The agent's C++ was textbook-shaped (correct OLS formula skeleton,
correct bootstrap scaffold), compiled, ran, produced a well-formatted
`result.txt`, and the `_tb2_self_verify` hook fired. But the agent responded to
self-verify by re-reading the task CHECKLIST and running `ls` — it never
independently re-derived the number. A one-line `numpy.polyfit` on the same CSV
would have produced the correct slope and exposed the C++ bug.

**Verified body evidence:**
- `task_000111_cbada64a.result.json`: reward=0, exit_reason=done, steps=7,
  final_pytest tail `AssertionError: Expected m to be approx 2.5997, got 2.5056`.
- `task_000111_cbada64a.messages.json` msgs 66–128: after `_tb2_self_verify`
  fired, the agent's verification was purely structural — "correct formula ✓,
  correct scaffold ✓, format ✓, files exist (ls) ✓" — zero independent numeric
  recomputation. It declared SUCCESS on a wrong value.

**Cluster (retroactive check — variant: does the fix generalize to sibling
failures with the same signature?):** Swept all trajectories for
`reward=0 AND exit_reason=done AND finished=no_tool_calls`. Found a broad
"wrong-answer / false-confidence" cluster where the agent produced a
well-formed artifact then declared done on an incorrect VALUE:
- `task_000111` OLS slope 2.5056 vs 2.5997 (this focus)
- `task_000117` PDB column parse bug → `float('5 -94.99')` (wrong byte offsets)
- `task_001048` integral 165.7908 vs expected 161.8028
- `task_001937` optimal grid 60 vs expected 50
- `task_000396` max deviation 0.607 vs expected < 0.1
- `task_000328` 300 frames vs expected ~10 (interpretation error)
All are correctness-graded numeric/quantitative outputs where the code ran
without error but the value was wrong. Every one would benefit from an
independent second-route recomputation before exit.

**Why instruction, not control/action:**
- The failure is NOT a loop, crash, truncation, or degenerate repetition —
  existing control processors (LengthLoopBreaker, CyclicLoopBreaker,
  AssistantReasoningRepeatBreaker) are irrelevant; the run exits cleanly at 7
  steps. No control lever applies.
- It is NOT an environment/context-injection gap (action lever): the agent had
  everything it needed; it simply didn't validate its own numeric output.
- A processor could force a second computation, but "recompute by an
  independent route and reconcile" is a *strategy* the model must carry out
  through Bash — the only tool. Encoding a rigid recompute processor would be
  brittle (can't know which value to recompute) and task-shaped. The correct,
  generalizable fix is an instruction: teach the agent the cross-validation
  habit for any correctness-graded computed output. This is strategy, not
  task-specific knowledge — it names no constants, files, or algorithms from
  the training tasks and passes the "helps an unseen task" test.

**Change:** Augment the sibling `system_prompt.txt` (read by
`SiblingSystemPromptBuilder`, already wired in the config) with a general
"independent cross-check for computed results" section. Config pipeline is
copied byte-for-byte from R1 (no processor changes). The only shipped delta is
`system_prompt.txt`.

**Tasks affected (predicted_affected):** task_000111_cbada64a (focus),
task_000117_1b598e44, task_001048_14335141, task_001937_ac874115,
task_000396_e56917e2, task_000328_80fb4c9f — the wrong-value/false-confidence
cluster.

**expected_global_gain:** Flips a cross-domain failing cluster
(scientific_computing + data_science numeric tasks) where the artifact was
structurally perfect but the value wrong. An independent second-route check
(numpy/scipy vs hand-rolled C++/parse) directly catches the exact bug class in
≥6 observed tasks and generalizes to any correctness-graded computed output.

**regression_risk:** LOW. Prompt-only, additive guidance. Risk is a small token
increase from an extra verification computation on some tasks, and a
theoretical chance the agent over-verifies a trivially-correct value. It cannot
introduce loops or crashes (control processors still guard those). No
already-passing task should flip: passing numeric tasks already have the
correct value, so a cross-check just reconfirms it. Non-numeric tasks (the
guidance is explicitly scoped to "COMPUTE a quantitative result") largely
ignore it.

**cost_shift:** Mildly positive (more tokens/steps) on numeric tasks that now
run a second confirmation pass; negligible elsewhere. Justified: the alternative
is reward=0 after a full clean run. Bounded because the guidance targets only
correctness-graded computed outputs, not every task.

**rollback_trigger:** If a previously-passing numeric task newly fails, OR
overall step/token cost rises materially without cluster flips (predicted tasks
stay reward=0), the instruction is net-negative → revert to the R1 prompt.
