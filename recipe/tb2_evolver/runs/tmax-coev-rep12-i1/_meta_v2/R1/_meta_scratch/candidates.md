# Candidates — Round 1 (tmax-coev-rep12-i1)

Baseline R0: 29/50 (58%). One `agent_error` (task_002108), five
`budget_exceeded` (80/80 steps), rest `done` but failed the verifier.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `NoProgressRepeatGuard` processor that detects when the agent runs the
SAME Bash command and gets the SAME output N times in a row, and appends a
corrective "stop repeating, change approach / inspect real state" nudge to
the tool result (escalating past a higher count).

- Tasks affected (distinct failing task_ids, same mechanism):
  - task_002108_a8cfbf2a — `exit_reason=error`, 44 consecutive identical
    `cat > src/main.rs << EOF` commands.
  - task_000470_f819ab03 — `budget_exceeded` 80/80; 28 consecutive identical
    `cat << EOF | nc 127.0.0.1 8080` commands, each returning
    `(exit 0, no output captured)`.
  - task_000840_b2ac4603 — `budget_exceeded` 80/80; 10 consecutive identical
    (command+output) pairs.
  - task_000908_170e5e4e — `budget_exceeded` 80/80; 8 consecutive identical
    (command+output) pairs.
  - (further corroboration, not primary): task_000348 maxrun 4, task_001400
    9 duplicate commands, task_000796 5 duplicate commands.
- Signal: `exit_reason ∈ {budget_exceeded, error}` on the failing cluster;
  per-trajectory analysis of `tool_calls` shows a maximal run of consecutive
  identical Bash commands (maxrun) of 44 / 28 / 10 / 8, and identical paired
  (command, tool-result) signatures across that run. The current pipeline has
  no guard for this shape — `LengthTruncationRecoveryProcessor` only fires on
  `finish_reason=length` (these loops have a normal finish reason + a real
  tool call).
- Verified (Read of `.messages.json`):
  - task_000470 steps: `CMD "cat << 'EOF' | nc -w 2 127.0.0.1 8080..."` →
    `OUT '(exit 0, no output captured)'` repeated ~11 times back-to-back in the
    first 24 events; total 31/33 commands identical. The agent never noticed
    nothing changed.
  - task_002108 last 8 assistant tool calls are byte-identical
    `cat > /app/frame_server/src/main.rs << 'ENDOFFILE' ...`; run ended
    `exit_reason=error` at step 68.
  - task_000840 / task_000908: paired (command+output) max consecutive
    identical runs measured at 10 and 8 respectively (script over messages).
- Why Control not Instruction: the agent already "knows" not to loop in the
  abstract — the failure is that within a single stuck run it has no external
  signal that the last K attempts produced identical results, so a static
  prompt rule does not fire at the decisive moment. A mechanical hook that
  observes the actual (command, output) history and injects a targeted
  interrupt exactly when the loop is detected is the right layer. It also
  fires uniformly across every task without the agent having to remember it.
- Why Control not Configuration: there is no existing knob for
  identical-command loops; `LengthTruncationRecoveryProcessor` targets a
  different trigger (`finish_reason=length`) and would not fire here. This is a
  genuinely missing mechanical guard, not a mis-tuned one.
- Retroactive check (A-corrective): yes. On task_000470 the agent burned all
  80 steps re-sending the same payload to a listener that produced no output;
  an interrupt at repeat 3 would have redirected it to inspect why the server
  produced no result (freeing ~25 steps of budget for actual progress). On
  task_002108 the interrupt would have broken the 44x rewrite loop before the
  run crashed. The nudge does not hand the agent the answer — it forces
  observation of real state, which is exactly what the stuck agent skipped.
- expected_global_gain: recovers step budget on the `budget_exceeded` /
  `error` cluster (>=4 tasks with hard identical-loop signatures, plus 2-3
  softer ones). Even partial recovery on 2-3 of these flips them, and prevents
  the one hard `agent_error` crash.
- regression_risk: low. The guard only appends text to a tool result and only
  when the SAME (command,output) repeats >=3 times consecutively — a shape
  that does not occur on any passing trajectory (passing runs make progress,
  so consecutive identical cmd+output triples do not arise). It never blocks
  or rewrites a command, never touches `event.messages`, and resets per task.
  Worst case on a legitimate repeat (e.g. deliberately polling the same
  command 3x) is a few hundred extra tokens of advisory text.
- cost_shift: net negative-to-neutral. Breaking 80-step loops early *reduces*
  tokens/steps on the affected cluster; the only additive cost is the warning
  string on the rare legitimate 3x repeat.
