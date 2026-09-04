# Candidates

Assigned focus: `task_000028_7fe033ac` fails. Diagnosis below drives C-001.

## Diagnosis of task_000028_7fe033ac

- `result.json`: reward 0, agent.steps=75, elapsed_s=624.5, finished
  `no_tool_calls`. The `final_pytest` tail shows
  `ModuleNotFoundError: No module named 'requests'` — this is the
  **verifier's own test-collection environment** failing to import, a
  verifier-side artifact, NOT something the agent controls during its
  isolated phase. It is a red herring for harness diagnosis.
- The real, actionable failure is visible in the message log
  (steps 47-58): the agent enters a **degenerate semantic loop** — it
  repeatedly issues the identical Bash call `cat /app/server.cpp`
  (6 consecutive times; 8 total) interleaved with runaway
  narration ("Let me try to check if the server is actually
  running…" repeated to the token limit). It burns 75 steps / 624s
  making zero progress and never breaks out.
- The existing `LengthTruncationRecoveryProcessor` only catches
  *consecutive* length-truncated no-tool-call turns; the intervening
  identical tool call resets its counter, so it does not fire on this
  alternating tool-call / narration loop. No processor in the current
  pipeline detects "identical Bash command repeated N times".

## Cluster evidence (systemic, not idiosyncratic)

Measured longest run of **consecutive byte-identical Bash tool calls**
across all 50 trajectories in this round:

FAILING tasks with a deep identical-command loop (all reward 0, most
burn the full 80-step budget):
- task_000028_7fe033ac: 6 consecutive `cat /app/server.cpp`
- task_000338_27d6a1be: 21 consecutive identical `convert ...`
- task_001706_24462a09: 33/33 calls identical `redis-cli LLEN ...`
- task_001937_ac874115: 19 consecutive identical `ls -la ...`
- task_000958, task_001089, task_001536, task_001321, task_001090,
  task_001032, task_001673: all reward 0 with 15-27 identical repeats.

PASSING tasks — max consecutive identical run:
- Nearly all passers ≤3. Single outlier: task_001591_8901fea6
  **passes with an 11× byte-identical consecutive run** (it breaks
  out on its own and finishes). This sets the regression floor: a
  hard raise threshold must be strictly > 11 to avoid clipping this
  passer.

Correlation: consecutive-identical-run ≥7 → reward 0 in every case
except the single 11× passer; runs ≤5 are compatible with passing.

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Add the built-in `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
to the pipeline, tuned so it (a) warns early (redirect nudge injected
into the tool result at the 3rd identical repeat) and (b) hard-raises
`LoopDetectedError` only on deep loops (≥12 identical repeats),
above the 11× passer floor.

- Tasks affected (failing, same mechanism, ≥2 distinct):
  task_000028_7fe033ac, task_000338_27d6a1be, task_001706_24462a09,
  task_001937_ac874115 (plus task_000958, task_001089, task_001536,
  task_001321, task_001090, task_001032, task_001673 exhibiting the
  same shape).
- Signal: longest consecutive byte-identical Bash `tool_calls` run
  ≥7 correlates 1:1 with reward 0 across the round;
  `finished=no_tool_calls`/`max_steps` with `agent.steps`=75-80 on
  every deep-loop task. No pipeline processor fingerprints repeated
  tool calls today.
- Verified (Read, message log):
  - task_000028 steps 47/51/56 body — identical tool call
    `{"command":"cat /app/server.cpp"}` re-issued verbatim, each
    followed by the same "Let me try to check if the server is
    actually running…" narration; 6 consecutive, 8 total.
  - task_001706 — 33/33 tool calls are byte-identical
    `redis-cli LLEN csv_input`; the entire trajectory is one command
    repeated to the step cap.
  - task_000338 — 21 consecutive identical `convert ...` calls.
  - task_001937 — 19 consecutive identical `ls -la /home/user/`.
  - Regression floor: task_001591 (PASS) — 11× byte-identical
    `python3 -c '...base64...'` consecutive run, then breaks out and
    passes. Verified full-string identity (not just truncated head).
- Why Configuration not Control: the mechanism this needs
  (fingerprint the last-N tool calls, warn on consecutive repeats,
  raise on a deep run) already exists as a maintained built-in
  processor with exactly the two-strategy design required (exact
  name+inputs → warn@3/raise; name-only → warn-only). Authoring a
  fresh Control processor would duplicate a tested component and add
  regression surface for no benefit. The only decision is enabling it
  and tuning its thresholds to this round's evidence, which is a
  Configuration change (add + parameterise an existing processor).
- Why not just tighten `LengthTruncationRecoveryProcessor`: that
  processor's counter resets whenever a tool call is present, so it
  structurally cannot see a tool-call/narration alternating loop. The
  gap is a *missing* fingerprinting mechanism, not a mistuned knob on
  the length recoverer.
- Retroactive check (A-corrective): yes. On task_000028 the warn
  injected at the 3rd `cat /app/server.cpp` ("you are stuck in a
  loop… try something fundamentally different") lands in the tool
  result before repeats 4-6, giving the agent the redirect it never
  produced on its own. On the ≥12× loopers (task_000338/1706/1937
  etc.) the hard raise terminates the run at repeat 12 instead of
  step 80 — the container is left in its partial final state for the
  verifier (identical verdict, 0 reward) but ~60 steps and hundreds
  of wall-clock seconds are saved, and a genuinely stuck run no longer
  monopolises the round's budget.
- expected_global_gain: 1 assigned target (task_000028) gets a real
  in-loop redirect; ≥6 other deep-loop failers get early termination
  that frees wall-clock/step budget for the round and may let the
  warn@3 nudge redirect them before they go terminal. Failing cluster
  "identical-command loop" is the single largest failure shape in the
  round.
- regression_risk: LOW. Hard-raise threshold=12 sits above the only
  observed passing loop (11×), so no currently-passing task is
  clipped. The warn nudge only appends text to a tool result — it
  cannot break a task that was going to pass. `LoopDetectedError` is
  caught by the run loop as `exit_reason=loop_detected` (a clean
  exit, not `error`), so the synthetic replay gate is unaffected. The
  name-only Strategy-2 warn (threshold=8, warn-only, never raises)
  adds a soft nudge for varying-argument thrash with zero termination
  risk.
- cost_shift: net NEGATIVE (cheaper). Deep-loop tasks currently run
  to 80 steps; terminating at ~12 cuts their token/step spend by
  ~60-85%. The warn nudge adds a few dozen tokens per firing task.
  Expected aggregate token/wall-clock consumption drops.
