# Candidates — R2 / c3

## Candidate C-002

**Three-axis tag:** lens=control-flow / lever=control / intent=close-failure-mode

**Signal.** Assigned task `task_000118_3043e92d` (system_administration)
fails: `final_pytest` — "Peak log directory size was 203915264 bytes, which
exceeds the threshold of 45000000 bytes." But the diagnostic root cause is NOT
the deployment-monitor logic — it is that the agent **never got to iterate on
the fix** because it was trapped in an undetected repeated-command loop that
consumed nearly its whole step budget.

**Verified body evidence** (`task_000118_3043e92d.messages.json`):
- Steps ~1–28 (`chatcmpl-tool-a8d86bbe...` through `...ab45589791294607`): the
  assistant emits a **byte-for-byte identical** message ("I see - the workers
  have exited...") and the **identical** Bash call
  `ps aux | grep -E "worker_sim|python3" | grep -v grep` ~20 consecutive times.
- Each tool result is *near*-identical but NOT byte-identical: the monitor's
  RSS column ticks (`10288` → `10300` → `10308` → ... → `10380`) and STAT
  flips `S`/`R`. So the existing `RepeatedCommandRecoveryProcessor` (keys on
  `tool_name + result_text + error_text`) sees ~20 DISTINCT fingerprints,
  `_run_len` stays at 1, and it **never fires**.
- After a PostCompaction event finally breaks the loop, the agent has ~28 steps
  left, self-verifies, confirms the (buggy) script exists, and exits `done` at
  step 56 — it never re-ran the deployment to notice peak size still blows past.

**Harness deficiency, not capability gap.** The result-keyed loop guard is
structurally blind to any loop whose command is identical but whose output
embeds a changing PID / RSS / timestamp / counter — a very common shape for
`ps`, `du`, `top`, `date`, tail-of-log polling. The agent chose the same
command 20×; the *cause* side (tool_input) was constant, the *effect* side
(result) drifted. Fixing this returns ~20 wasted turns to the agent so it can
actually iterate on the solution.

**Change.** New `processors/repeat_toolcall_recovery.py` →
`RepeatedToolCallRecoveryProcessor`. Fingerprints each tool call by
`(tool_name, tool_input)` at `on_before_tool`; counts consecutive identical
commands regardless of result drift; at `repeat_threshold=3` injects one
generic corrective user message (escalating at +2) telling the agent the
repeated command is not moving it forward and to take a materially different
action toward the task. Never blocks a call, never kills anything —
message-injection only, contract-clean (merges onto a trailing user message to
avoid double-user). Registered at order 7, adjacent to the existing
result-keyed guard, before compaction (8). The two guards are complementary:
result-keyed = stuck on a stable error; command-keyed = spinning on a
drift-immune poll/narration loop.

**Retroactive check (would-fire).** Replaying the fingerprint logic over
`task_000118`'s call stream: calls 1,2,3 identical → `_run_len` reaches 3 at
call 3 → nudge fires before call 4, i.e. after ~3 turns instead of ~20+. The
agent gets its budget back with 50+ steps remaining. The existing result-keyed
guard demonstrably would NOT fire here (verified: 20 distinct result strings).

**Why control not instruction.** The failure is a runtime control-flow trap
the model cannot see (each Bash call is stateless from its view; it does not
perceive it has repeated a command 20×). A system-prompt line ("don't repeat
commands") is weak — the model already narrates "let me try a different
approach" and then repeats anyway. Only a runtime processor that observes the
actual call stream and interrupts is reliable. Not `configuration`: no existing
knob detects command-keyed (vs result-keyed) repetition. Not `action`: no new
tool needed.

**expected_global_gain.** Targets the broad "stuck-loop burns the step budget"
cluster. The R1 journal already established loop-hammering as systemic (15/25
r0 failures showed ≥4 consecutive identical calls). The existing guard only
covers the byte-identical-result subset; this adds the drift-immune subset
(any polling/monitoring command — `ps`, `du`, `top`, `date`, `tail`),
structurally common across system_administration tasks. Any task where the
agent spins on a poll now recovers ~N turns of budget.

**regression_risk.** Low. Message-injection only, never blocks/kills. Only risk
is a false-positive nudge on a task that *legitimately* re-runs the same
command 3× in a row (e.g. deliberate polling loop by hand). Mitigations:
(a) threshold=3 requires three consecutive *identical* commands with no other
call interleaved — normal iterative work interleaves different commands and
resets the counter; (b) the nudge is advisory, so even a false positive costs
one soft message, not a blocked action; (c) contract-clean, order non-load-
bearing. Rollback trigger below.

**cost_shift.** Net negative-to-neutral. On the loop path it SAVES ~15–20
wasted model turns per affected task (large token reclaim). On the common path
it adds nothing (guard only injects when the run length hits 3). Worst case:
one extra ~90-token user message on a rare legitimate-3×-poll task.

**rollback_trigger.** If R3 pass_rate is flat/down AND any previously-passing
task regresses T→F with evidence the nudge interrupted a legitimate repeated
poll, revert this processor.
