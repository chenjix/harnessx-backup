# Candidates — R1

Baseline R0: 29/50 pass. Failure taxonomy (from result.json `agent.finished`):
- `error` / "timed out": 9 tasks (infra/model chat timeout — mostly NOT harness-fixable)
- `error` / "HTTP 400 Bad Request": 2 tasks (578, 1321) — context overflow caused by loops
- `no_tool_calls`: 7 tasks (premature completion / wrong solution)
- `max_steps`: 2 tasks (958, 1031) — burned all steps in loops

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add the existing `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
to the pipeline (exact-repeat strategy: warn at 3, raise `LoopDetectedError` at 5;
name-only strategy disabled since TB2 has a single tool).

- Tasks affected: task_000578_cebe85a5, task_001321_658ce4a8,
  task_000958_4bb2b05d, task_001031_a8f0eb37, task_001653_c4cafa73
- Signal: `agent.finished` ∈ {error(HTTP 400), max_steps, no_tool_calls} on these
  5 tasks; each has a run of 26–62 **byte-identical consecutive Bash tool calls**.
  The 578/1321 loops overflow context → HTTP 400 crash (`exit_reason=error`).
- Verified (Read messages.json):
  - task_001321: same Bash call `"/home/user/extractor < /home/user/raw_dump.txt | head -20"`
    repeated 55 consecutive times (65 total calls); assistant text
    "Let me look at the hex dump more carefully..." repeated x55.
  - task_000578: same `cat > /home/user/workspace/main.go << 'EOF' ...` call repeated
    37 consecutive times (78 total); ends in HTTP 400.
  - task_000958: SQL-query Bash call repeated 62 consecutive times → max_steps (80).
  - task_001031: `python3 << 'EOF' from mpi4py ...` repeated 59 consecutive times → max_steps.
  - task_001653: `cat > /home/user/etl.c << 'EOF' ...` repeated 26 consecutive times.
- Why Control not Instruction: the agent already emits self-aware text
  ("I keep making the same mistake", "Let me try a different approach") yet keeps
  issuing the identical call — a prompt rule cannot break a mechanical repeat the
  model cannot self-interrupt. A `on_after_tool` guard that fingerprints tool
  name+input and both nudges (warn at 3) and hard-stops (raise at 5) is the
  mechanical intervention. The existing `CustomEditToolProcessor` only counts
  file-*write* commands (>, sed -i, tee) so non-write loops (1321 run, 958 query)
  slip through — this generalizes to any repeated call.
- Why not Action: no new capability is needed; the agent has the tool, it just
  loops on it.
- Retroactive check (A-corrective): partial-yes. For 578/1321 the raise-at-5
  converts a context-overflow `exit_reason=error` crash into a clean early
  `loop_detected` exit (protects the run loop; replay-safe) AND the warn-at-3
  nudge gives a genuine break-out chance before the hard stop. For 958/1031 it
  reclaims ~75 wasted steps that could be spent recovering. Flips are plausible
  on the tasks where the warn nudge lands before the model has fully committed;
  even where it doesn't flip, it stops the crash/step-burn and cannot regress a
  passing task (see regression_risk).
- Tasks affected (mechanism = identical-consecutive-call loop): 5 distinct tasks
  across 4 domains (data_science, data_processing, data_querying,
  scientific_computing) — systemic, not idiosyncratic.
- expected_global_gain: closes the 5-task loop cluster; 2 of these are hard
  `exit_reason=error` crashes that the post-flight replay gate treats as fatal —
  removing that failure mode is structurally valuable beyond pass count.
- regression_risk: near-zero. Measured every R0 passing task: max consecutive
  identical Bash call = 1 across ALL 29 passing tasks. The exact-repeat threshold
  of 5 (and even warn-at-3) is never approached by legitimate work. Strategy 2
  (name-only) is disabled (`name_warn_threshold=9999`) because TB2 exposes only
  Bash, so every step is a "Bash" call and name-only matching would spam warnings
  into every long passing trajectory.
- cost_shift: net negative (savings). Loops that ran 65–158 messages / 80 steps
  are cut off at ~5–8 repeats, saving thousands of tokens per looping task; no
  added cost on non-looping tasks (guard only appends a short string when a loop
  fires).
