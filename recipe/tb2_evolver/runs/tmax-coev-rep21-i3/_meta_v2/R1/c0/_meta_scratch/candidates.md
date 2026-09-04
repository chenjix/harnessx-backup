# Candidates — R1 c0

Assigned focus: `task_000010_644ab1c2` fails (`budget_exceeded`, reward 0).

## Candidate C-001 — Enable LoopDetectionProcessor (identical-tool-call loop breaker)

**Three-axis tag:** lens=trajectory-control / lever=configuration / intent=corrective

**Signal.** `task_000010_644ab1c2` exits `budget_exceeded` after 80 steps
with reward 0. Its `final_pytest` fails with a circular-import
(`/home/user/operator.py` shadows stdlib `operator`), but that is a
downstream symptom. The *harness-relevant* root cause is a hard
repetition loop: the agent issued the **byte-identical** Bash call
`pkill -f socat ...` (returning `exit 143, no output captured`) **23
times in a row**, with the identical assistant preamble each time,
never breaking out until the step budget was exhausted.

**Verified body evidence.**
- `task_000010_644ab1c2.messages.json`: 33 total tool calls, only 8
  unique; the top duplicate (`pkill -f socat`) appears 23× consecutively,
  each returning `(exit 143, no output captured)`.
- `task_000010_644ab1c2.result.json`: `exit_reason: budget_exceeded`,
  `steps: 80`.

**Cluster (global gain).** A sweep of all 50 trajectories found **17
tasks** with a run of ≥5 byte-identical consecutive tool calls. Most of
these exit `budget_exceeded` having burned their budget spinning on a
stuck command. Examples: `task_001207_44e97fe1` (29× consecutive),
`task_001717_a9c46d8d` (26×), `task_001832_dd672877` (26×),
`task_001447_8bde38ef` (21×), `task_000015_89886d8d` (16×).

**The fix.** `harnessx.processors.control.LoopDetectionProcessor`
already exists in the codebase but is **not in the current pipeline**.
Its Strategy-1 (exact name+inputs fingerprint) injects an escalating
"you are stuck in a loop, try something different" nudge directly into
the tool result once a call repeats `warn_threshold` times consecutively,
and raises `LoopDetectedError` at `threshold`. The run loop catches that
cleanly (`exit_reason=loop_detected`, best-output recovery — NOT `error`,
confirmed in `harnessx/core/runloop.py:781`), so replay is safe.

**Knob choice — why warn-heavy, raise-conservative.** Two currently
**passing** tasks also contain long identical runs:
- `task_000316_99102ad4` (reward 1, `done`): `echo "Task completed
  successfully."` repeated 22× — harmless idle-spin *after* the task was
  already solved.
- `task_000870_7cbd963f` (reward 1, `done`): the same correct recursive
  SQL query repeated 26× mid-flow, then reached a natural `done`.

A hard-raise at the default `threshold=5` would have killed both of
these → regression. So:
- `warn_threshold=3` — the zero-risk nudge (extra text in the tool
  result) fires early on all 17 stuck tasks, giving the model repeated
  chances to self-correct. Warnings never force an exit, so the two
  passing repeaters are untouched.
- `threshold=40` — the hard raise fires only on truly pathological
  runaway loops, well beyond anything a passing task did (max passing
  run was 26). In practice this benchmark never hits 40, so the raise is
  a safety net, not the primary mechanism. The nudge does the work.
- `name_warn_threshold=8` — default; warn-only, catches same-tool /
  varying-args circling.

**Retroactive check (corrective variant).** Would this have helped
`task_000010`? At the 3rd identical `pkill -f socat` the tool result
would carry `[LoopDetection] ⚠️ The exact same tool call(s) have been
issued 3 times in a row ... try something fundamentally different.`,
escalating each subsequent repeat. The 4B model spinning on a
`exit 143` no-op is exactly the case an injected "stop and reconsider"
nudge is designed to break — and even if it doesn't self-correct, the
budget is no longer silently consumed by 20+ identical no-ops.

**Tasks affected (corrective intent → failing tasks to flip).**
`task_000010_644ab1c2` plus the stuck-loop cluster:
`task_001207_44e97fe1`, `task_001717_a9c46d8d`, `task_001832_dd672877`,
`task_001447_8bde38ef`, `task_001857_24daeef3`, `task_001547_8cde5da2`,
`task_001902_29d93022`, `task_000106_23215092`, `task_000506_c13429e7`.
(Preserve: `task_000316_99102ad4`, `task_000870_7cbd963f`.)

**Why configuration, not action/instruction.** The mechanism already
exists as a tested processor; the deficiency is purely that it is absent
from the pipeline. No new code (lower regression risk than authoring a
processor), no system-prompt edits (which the philosophy discourages and
which a 4B model in a tight loop tends to ignore anyway — an in-band tool
-result nudge is more likely to be attended to than a static prompt line).

**Pareto statement.**
- expected_global_gain: gives an escalating self-correction nudge to the
  ~17-task identical-repetition cluster that currently burns budget to
  `budget_exceeded`; even partial break-out recovers budget for real work.
- regression_risk: warnings add a few tokens to repeated tool results; the
  hard raise at 40 is above any observed passing run so no forced-exit
  regression on the two passing repeaters.
- cost_shift: net **down** — stuck tasks stop consuming 20+ redundant
  no-op steps; tiny per-warning token cost on repeaters.
