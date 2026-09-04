# Candidates — R5 c3 (focus: task_000118_3043e92d)

## Candidate C-001 — enable first-party LoopDetectionProcessor (terminate-on-exact-repetition)

- **Lens / Lever / Intent**: run-loop control shape / **configuration** / stop a
  budget-burning exact-repetition loop by enabling an existing, well-tested
  first-party processor rather than authoring a 6th bespoke nudge.
- **hypothesis_id**: `h_enable_loop_detection_v1`

### Signal

Assigned focus `task_000118_3043e92d` (system_administration): `reward=0`,
`exit_reason=budget_exceeded`, `steps=80`, `initial_pytest.passed=true`. Final
grader: `Peak log directory size was 209715200 bytes ... exceeds 45000000`.

### Verified body evidence

- `messages.json` tool-call fingerprint sequence (name+args, sha256[:16]):
  from msg 33 onward the model cycles a **3-command loop**
  `5540…`(`rm -f /home/user/logs/* && python3 …deployment_monitor.py &`) →
  `1c26…`(`sleep 1 && ps aux | grep …`) → `fe2f…`(`timeout 3 python3 …`), and the
  tail (msgs 56/60/64/68) is `5540…` **4× consecutive**, each returning
  `(exit 0, no output captured)`. The agent never runs `run_deployment.sh`
  concurrently to generate real load, so peak log size is never observed.
- The interleaved `LengthTruncationRecoveryProcessor` nudges (msgs 3/16/24/32/…)
  show as the **raw run-loop passive text** ("Your previous response was cut off
  … Please continue"), and the model quotes it back ("The user is right - I've
  been stuck in a loop") yet keeps looping — demonstrating that passive
  text nudges do NOT break this loop.
- **Passing-set false-positive sweep** (all reward=1 tasks in r4-traj): max
  strictly-consecutive identical tool-call run on a passing task = **4**
  (`task_001090_c61c71f2`, legitimate `ps aux | grep` process-poll). No passing
  task reaches 5. So `threshold=5` (exact raise) fires on **zero** passing tasks.
- **Failing loopers that WOULD be terminated at threshold=5** (reclaiming budget):
  `task_001031_a8f0eb37` (maxrun=35, was `error`), `task_001706_24462a09`
  (maxrun=33, `budget_exceeded`), `task_000028_7fe033ac` (maxrun=23),
  `task_000396_e56917e2` (maxrun=19).

### Root harness gap

R0 has **no loop-termination hook** for repeated *completed* tool calls.
`LengthTruncationRecoveryProcessor` only fires on `finish_reason=="length" and
not tool_calls`; the exact-repetition-with-tool-call loop is uncovered.
`CustomEditToolProcessor` counts only write commands; `CustomSelfVerifyProcessor`
never fires on a `budget_exceeded` run. A first-party, tested
`harnessx.processors.control.loop_detection.LoopDetectionProcessor` already
exists and is simply not registered.

### Change

- `config.yaml`: register `LoopDetectionProcessor` (`_order=20`, so it sits after
  `TaskTimeReminderProcessor` and before `CompactionProcessor`) with defaults
  `window_size=12, warn_threshold=3, threshold=5, name_warn_threshold=8,
  compaction_drop_threshold=5`. Strategy 1 warns at 3 consecutive exact repeats
  (note appended to the tool result — a signal task_000118's tool-call loop
  currently never gets) and RAISES `LoopDetectedError` at 5, cleanly ending the
  run with container state preserved for grading. Strategy 2 (name-only) is
  warn-only. Rest of the pipeline byte-identical to R0; `system_prompt.txt`
  sibling copied unchanged. No authored code.

### Retroactive check

- *Would it have fired on the focus task?* Strategy-1 **warn** fires on
  task_000118 (4 consecutive `5540…` at the tail crosses warn_threshold=3),
  injecting a corrective note into the tool result. The exact **raise** does not
  (max consecutive = 4 < 5), so task_000118 is not force-terminated — an
  intentionally conservative choice to protect `task_001090` (passing, 4
  consecutive polls). The flip for 118 is therefore *plausible-not-guaranteed*:
  it depends on the model acting on the injected warn. Honest read below.
- *Would it have mis-fired on a passing task?* No. Zero passing tasks reach 5
  consecutive; `task_001090` reaches 4 → gets only a harmless warn note, never
  terminated.

### Why configuration, not another control processor

The lever scoreboard shows **control = 5 attempts / 0 accepted / 1 reverted**
(four pending), and **configuration = 0 attempts**. Prior loop-related bets all
authored *new bespoke passive-nudge processors* (`stuck_result_breaker`,
`degenerate_loop_breaker`, `stuck_truncation_escalator`) — none enabled this
first-party terminate-on-loop config, and passive nudges are exactly what
task_000118 demonstrably ignores. Enabling a tested first-party processor with a
`threshold=5` raise is a different mechanism (hard stop, not more text), an
untried lever, and its global value (terminating 19–35× loopers early) does not
depend on the model changing behavior.

### Pareto framing

- `expected_global_gain`: relieve the exact-repetition budget-burn cluster
  (task_001031, task_001706, task_000028, task_000396, and the focus family) by
  ending dead loops at repeat 5 instead of the step-80 wall; reclaimed budget can
  convert some near-miss runs and shortens wall-clock. Generalizes to any exact
  tool-call loop.
- `regression_risk`: Low. The exact raise is gated to 5 consecutive identical
  calls — a shape no passing task shows (max=4). Worst realistic case: a passing
  task that legitimately needs ≥5 identical polls would be terminated; none exists
  in the r4-traj passing set, and the warn-only name strategy never raises.
- `cost_shift`: **Net decrease** — loops end at repeat 5 rather than running to
  the wall (reclaims tens of steps per stuck task); the warn note is ~60 tokens
  and fires only inside a loop.

### Uncertainty / rollback

task_000118's tail loop is a 3-cycle whose consecutive-exact count peaks at 4, so
the conservative `threshold=5` warns but does not terminate it — 118 flips only if
the model acts on the injected warn (its deeper miss is never running
`run_deployment.sh` under load, a reasoning gap). If a later round shows the big
loopers (001031/001706/000028/000396) still `budget_exceeded`/`error` with the
same 19–35× repetition (loop-detection somehow not firing), or ANY previously
passing task regresses to F on a terminate, revert. If the loopers are terminated
but 118 stays F on the log-size peak, keep the processor (global budget win
confirmed) and treat 118's residual as a monitor-logic capability gap.
