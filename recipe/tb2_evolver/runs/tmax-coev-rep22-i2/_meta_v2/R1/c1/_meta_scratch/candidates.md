# Candidates — Round 1 (tmax-coev-rep22-i2)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `CyclicToolLoopDetector` processor that terminates degenerate period-k
tool-call cycles (call + output byte-identical across repetitions) with a clean
`LoopDetectedError`, covering the loop shape the existing pipeline misses.

- Tasks affected: task_000011_d089ef35 (assigned), task_000010_644ab1c2
  (both degenerate loops that never self-terminate). task_000106_23215092 is a
  deliberate NON-target: its cycle has *changing* output and it recovered — the
  detector must not fire on it.
- Signal: high `steps` (79, 80) + long `elapsed_s` (962s, 787s) with
  `finished ∈ {no_tool_calls, budget_exceeded}` and reward=0. Tool-call
  fingerprint stream shows a repeating cycle.
- Verified (Read messages.json):
  - task_000011 steps 4–64: strict A-B-A-B cycle — two Bash calls
    (`socket.send(b'0\n')` → `Q0: 8.25`) and (`for q in 0 1 2 3` → 
    `Q0: 8.25\nQ1: 17.25\nQ2: 38.25\nQ3: 55.25`) repeated ~10× with
    byte-identical output each time (`uniq -c`: 10× each). The
    `LengthTruncationRecoveryProcessor` never engaged because these turns carry
    tool calls (its `_consecutive` counter resets on any tool call, line 82).
  - task_000010: single fingerprint repeated 17× (tail = 10+ consecutive
    identical calls); `exit_reason=budget_exceeded` at 80 steps / 787s.
  - task_000106 (non-target): period-5 cycle but the tool *outputs change*
    across cycles (`Traceback...` → then `Author 1 response: {...}` after scipy
    installs); the agent broke out and made progress. Combined
    (call+output) fingerprint differs each cycle, so the detector stays silent
    (verified in unit sim: `detect(t106) -> none`).
- Why Control not Configuration: the existing `LoopDetectionProcessor` is not in
  the pipeline, and even if enabled its Strategy-1 exact detector only counts a
  *consecutive tail* of ONE fingerprint (period-1) — the A-B-A-B alternation in
  task_000011 resets that count to 1 every step, so no tuning of its knobs
  catches a period-2 cycle. Strategy-2 (name-only) is warn-only and never
  raises. Closing the gap needs a genuinely new mechanism (period-k cycle
  detection on the call+output fingerprint), not a knob change.
- Why Control not Instruction: the model already received repeated
  "you are repeating yourself / cut off" nudges (run-loop continue message +
  `LengthTruncationRecoveryProcessor`) and ignored them for 30+ turns. A prompt
  rule cannot force termination; a mechanical hard-stop can.
- Retroactive check (A-corrective): partial-yes. If the detector had been in
  place, task_000011 and task_000010 would have terminated ~3 cycles in
  (index 5 / index 7 in the fingerprint stream) instead of burning 962s / 787s.
  This does NOT by itself flip the reward (task_000011's underlying MSE values
  are a model reasoning error — Q2/Q3 wrong; that is a capability gap, not a
  harness gap). The harness value is: (a) reclaiming ~900s + ~75 steps of
  wasted wall-clock/budget per stuck task so a single degenerate task can no
  longer block an evolve round, and (b) surfacing a corrective warning two
  cycles earlier that gives a still-capable model a chance to re-derive. The
  loop itself is the harness deficiency being closed; the wrong-answer reasoning
  is logged as a capability gap and skipped.
- expected_global_gain: eliminates a recurring degenerate-loop cluster (≥2
  tasks here, generic shape) that wastes wall-clock and can starve other tasks
  in the round; frees budget/steps that occasionally let a recovering agent
  finish. Generalizes to any Bash-only task where the model narrates-and-
  re-verifies without progress.
- regression_risk: false-positive termination of a task that legitimately
  re-runs an identical command sequence ≥3 times AND gets identical output each
  time. Mitigated by (i) requiring call+output identity (productive exploration
  changes output — see task_000106), (ii) max_cycles=3 (three FULL identical
  cycles, i.e. the same block seen 3×), (iii) compaction-aware window reset.
  Blast radius is small: a task that produces the same output 3× in a row from
  the same command is already stuck.
- cost_shift: net NEGATIVE (savings). Terminating stuck tasks early reclaims
  hundreds of seconds and dozens of model calls per degenerate loop; adds only a
  cheap sha256 per tool result otherwise. No token growth on healthy tasks.
