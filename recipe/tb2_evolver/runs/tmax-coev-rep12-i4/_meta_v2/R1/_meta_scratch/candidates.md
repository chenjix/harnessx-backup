# Candidates — R1 (meta_v2)

Baseline R0: 10/50 pass (0.20). Dominant failure cluster: `exit_reason=budget_exceeded`
at the step cap (steps=80), 11 tasks, only 1 of which passed.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a Bash-scoped repetition-loop breaker: warn on the 3rd byte-for-byte
identical Bash command, and on the 6th intercept the call (don't execute) and
inject a corrective "abandon this line of attack" redirect.

- Tasks affected (failing cluster, same mechanism — identical-Bash loop until
  step cap): task_000032_3fb303f6, task_000910_16cc0daf, task_001044_45c70cf1,
  task_001465_aa3ed3f8, task_000506_c13429e7, task_001028_5bc8bc70,
  task_000938_6d7bdc5c, task_000796_828a72cf, task_000761_072f9e93
  (9 of 11 `budget_exceeded` tasks).
- Signal: `agent.exit_reason=budget_exceeded` at `steps=80` on 11 tasks; on 9
  of them the same assistant turn / Bash command repeats 10-33 times. Measured
  max consecutive identical-Bash runs: 16 (t_000032), 33 (t_000910), 27
  (t_001044), 26 (t_001465), etc. Loops begin at the 3rd-7th Bash call, so
  ~25-75 steps are wasted after the loop sets in.
- Verified (Read of message logs):
  - task_000910_16cc0daf: 33x identical
    `rm -f /tmp/frame1.bin && ffmpeg -i /app/vnc_recording.mp4 -frames:v 1 -f rawv...`;
    the tool output is the interactive prompt `File 'pixel_format=rgb24' already
    exists. Overwrite? [y/N] Not overwriting - exiting` every time; assistant
    narrates "I need to stop repeating the same command" then repeats it.
  - task_000032_3fb303f6: 16x identical Bash (`# Let me try to see if the hash
    might be for a common word...`) returning `(exit 0, no output captured)`;
    assistant narrates "Let me try a different approach" then repeats verbatim.
  - task_001044_45c70cf1: 27x identical `echo "1 10 1 10 ..." | perf_oracle`
    returning the same `Trend: m=0 b=1`; assistant re-counts the same output.
  - task_001465_aa3ed3f8: 26x identical `curl -s http://localhost:5000/embed ...`.
- Why Control not Instruction: the model already *knows* it is looping — it
  literally writes "I'm stuck in a loop / I need to stop repeating this command"
  and then repeats it anyway. A prompt rule cannot fix a behaviour the model
  states it should stop but can't; a mechanical hook that refuses to re-run the
  dead command and forces a redirect is the only thing that breaks the cycle.
- Why Control not Configuration: there is no existing knob for this. The stock
  `LoopDetectionProcessor` is not in the pipeline and, if added as-is, would
  fingerprint ALL tools including the injected `_tb2_self_verify` tool — which a
  PASSING task (task_002138) calls 22x consecutively — and would raise
  `LoopDetectedError` killing that pass. A custom Bash-only detector avoids that.
- Retroactive check (A-corrective): partial-yes. The loop is the actual blocker
  (not a symptom): the agent burns its entire remaining budget re-running one
  dead command, so those steps produce nothing. Breaking the loop at the 6th
  repeat returns ~25-70 steps to the agent to try a different approach, and the
  early warn (3rd) gives it a nudge while budget is still ample. This does not
  *guarantee* each task flips (some also need domain capability), but it removes
  the specific harness-level failure mode (wasting the whole budget on a proven-
  useless command) that is common to the whole cluster.
- expected_global_gain: recover wasted budget on the largest failing cluster
  (9 tasks, all same mechanism). Even a partial flip rate here is the highest-
  leverage move available; also caps wall-clock on the 1600-1900s runaway tasks.
- regression_risk: near-zero on the observed passing set — every R0 passing task
  had a max consecutive identical-Bash run of exactly 1 (verified across all 10
  passing tasks), so warn(3)/break(6) can never fire on them. Internal tools
  (self-verify) are excluded from fingerprinting by design, protecting the one
  passing task that repeats self-verify 22x. Residual risk: a legitimate task
  that must poll the identical command >=6 times (e.g. waiting on a service) —
  mitigated by (a) break_threshold=6 being generous, (b) the redirect explicitly
  telling the agent to verify-and-finish if the deliverable already exists rather
  than hard-failing the task.
- cost_shift: net decrease — intercepting dead loops truncates 20-75 wasted
  step-generations per affected task (the 1600s/1900s runaways especially).
