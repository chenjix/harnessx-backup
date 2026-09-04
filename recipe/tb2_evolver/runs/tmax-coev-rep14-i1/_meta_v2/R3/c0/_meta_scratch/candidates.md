# Candidates — R3 / c0

Assigned focus: `task_000010_644ab1c2` fails.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `DegenerateLoopBreakerProcessor` (`on_after_tool`, append-only): when the
last `window=6` (command,result) fingerprints collapse to `<= distinct_max=2`
distinct values, append an escalating "you are looping, change strategy" note
to the tool result.

- Tasks affected (distinct, same mechanism — low-distinct-window loop while
  reward=0): task_000010_644ab1c2, task_000958_4bb2b05d, task_001706_24462a09,
  task_001031_a8f0eb37 (also touches task_000118_3043e92d more weakly).
- Signal: `reward=0` with a rolling window of the last 6 (cmd,result) pairs
  collapsing to <=2 distinct fingerprints. Measured windows-that-would-fire:
  task_000010=13, task_000958=9, task_001706=9, task_001031=42. Across all 29
  **passing** tasks in the round, 0 would fire — the shape is exclusive to the
  looping/failing cluster.
- Why this differs from the existing `LengthTruncationRecoveryProcessor`: that
  processor only fires on `finish_reason=="length" and not tool_calls` (model
  hit the token cap with NO tool call). task_000010's dominant loop is
  *completed* tool calls (msgs 45–54: `rm && ln && python3 -c 'import operator'`
  → `OK` alternating with `cd /tmp && python3 .../k8s_operator.py` → the same
  ImportError traceback, 9× each). Nothing in the R0 pipeline intercepts a loop
  of completed-but-identical tool calls.
- Why a *window/distinct* detector, not a "N consecutive identical results"
  detector: task_000010's loop is a 2-cycle (A/B/A/B), so max *strictly
  consecutive* identical result = 2 — a threshold-3 consecutive detector misses
  it entirely. The window collapses to 2 distinct fingerprints, which catches
  both the 2-cycle and the back-to-back reruns (000958/001706/001031).
- Verified (Read of messages.json):
  - task_000010_644ab1c2 msgs 45–54: assistant tool_call
    `rm /home/user/operator.py && ln -sf .../k8s_operator.py .../operator.py &&
    python3 -c "...import operator; print('OK')"` → tool result `OK`; next
    assistant tool_call `cd /tmp && python3 /home/user/k8s_operator.py 2>&1` →
    identical `Traceback ... circular import` — this exact 2-cycle repeats 9
    times; run ends at step 65 with `/home/user/operator.py` failing to import
    (final_pytest: circular-import traceback on `import operator`).
  - task_000958_4bb2b05d: `exit_reason=budget_exceeded`, steps=80; the same
    `sqlite3` invocation and its result repeat 13× back-to-back
    (max_consec_result=13).
  - task_001706_24462a09: `exit_reason=budget_exceeded`, steps=80;
    max_consec_result=12, dupcmd=15.
  - task_001031_a8f0eb37: `exit_reason=error`, steps=71; dupcmd=47, dupresult=41
    — a severe multi-step cycle.
- Why Control not Instruction: the R0 system prompt already implies "don't
  waste steps", and the model *knows* it is looping (its own narration says "the
  issue persists" repeatedly) — knowledge is not the gap. The gap is a
  mechanical interrupt at the moment the loop is detected. A prompt rule cannot
  observe the runtime repetition of tool results; only an `on_after_tool` hook
  can. So Control, not Instruction.
- Why Control not Action: no new capability is missing — the agent has Bash and
  full ability to act; it is stuck repeating one action. Nothing to add to the
  tool registry (TB2 exposes only Bash anyway).
- Retroactive check (A-corrective): yes — for task_000010, injecting "you are
  looping, this approach won't work, change strategy" at the ~3rd repeat returns
  control with ~60 steps of budget remaining and explicitly redirects away from
  the dead `import operator`/rename cycle toward a different mechanism (e.g.
  stripping the script directory from `sys.path` at the top of the file, or
  running via a wrapper) — the general escape the model never reached because it
  kept re-running the same two commands. For the budget_exceeded loopers
  (000958/001706), breaking the rerun mid-loop reclaims tens of steps to fix the
  actual bug rather than hitting the wall.
- expected_global_gain: Flips/relieves the degenerate-loop failing cluster
  (>=4 tasks spanning system_administration + data tasks). The mechanism is
  content-agnostic, so it generalizes to any future task where the model fixates
  on a repeating failing command.
- regression_risk: Very low. The nudge is append-only on the tool result
  (mirrors `CustomEditToolProcessor`'s contract — no message-count change, no
  system-prompt mutation, contract-clean). Measured: 0 of 29 passing tasks hit
  the window=6/distinct<=2 condition, so no passing task ever sees the note. The
  detector requires a *full* window and only fires inside an active loop.
- cost_shift: Net decrease. Breaking loops at the ~6th repeated result instead
  of letting them run to step 80 reclaims tens of steps per stuck task; the note
  is ~90 tokens and only fires inside a loop. Passing tasks are unaffected.
- rollback_trigger: If the next round shows the cited loopers still reward=0
  with the loop unbroken (nudge ignored) AND no passing task regressed, the
  mechanism is too weak — escalate (e.g. lower refire_gap or add a hard
  loop-terminate guard) rather than re-shipping this shape. If any
  previously-passing task regresses to F, revert.
