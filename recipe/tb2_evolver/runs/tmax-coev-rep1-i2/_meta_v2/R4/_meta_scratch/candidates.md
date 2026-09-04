# Candidates — R4

Context: R3 = 32/50 (0.64). The truncation-spiral control lever (R1/R3) has
**landed and is now exhausted** — this round confirms it worked: `contnudge`
(passive "continue" nudges) dropped from 10-20/task in R2 to 0-4/task in R3,
max assistant content dropped from ~2000 (right at the collapse threshold) to
596-3320, and 0 collapse-marker turns were needed. The remaining
`budget_exceeded`/`loop_detected` tasks are no longer truncation spirals;
they are genuine debugging thrash or capability gaps. Per the R3 journal's own
rollback trigger ("if the cluster still shows many passive nudges per task with
no flips"), the trigger did NOT fire — nudges collapsed — so the processor stays
as-is. No further truncation change is warranted.

Failure taxonomy of the 18 R3 failures (verified via `final_pytest.output_tail`):
- Multimodal input (OCR of PNG / audio transcription): 000015, 000536, 001818 —
  9B text model can't reliably parse images/audio. **Model capability gap — skip.**
- Wrong computed value / algorithm / accuracy: 001498 (error 231 vs 163),
  001937 (grid 60 vs 50), 000587 (C++ CSV NaN handling), 001090, 000958,
  001032 (zip-slip logic), 000505 (adversarial bypass), 001781 (Rust
  memory-leak/deadlock fix). **Model logic gaps — skip.**
- Ran out of steps / gave up before required artifact existed: 000933 (tarball),
  001089 (regression test file), 001031 (results.json), 000010 (proxy).
  Heterogeneous root causes (each a distinct logic/thrash issue), not one
  mechanism — do not cluster.

None of these 18 is a *systemic harness deficiency* with >=2 tasks sharing one
harness-addressable mechanism. What IS systemic, cross-cutting, and
harness-addressable is a **false-positive control mechanism** — see C-001.

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Disable the LoopDetectionProcessor Strategy-2 *name-only* warning
(`name_warn_threshold: 8 -> 999`) — it is structurally meaningless in a
Bash-only benchmark and fires as pure context/cost noise on nearly every task.

- Tasks affected (cost + desensitization, cross-cutting):
  - The name-only warning fired **567 times across 47 of the 50 tasks** —
    passing AND failing. It is not a discriminator: passing task_001673 tolerates
    29 warnings and passes; failing task_000010 has 15 and fails.
  - Representative passing tasks carrying the noise: task_000028 (26),
    task_000748 (20), task_001652 (25), task_001321 (22), task_001536 (23),
    task_001673 (29).
  - Representative failing tasks carrying the noise: task_001090 (34),
    task_001032 (30), task_000958 (25), task_001031 (26).
- Signal: LoopDetectionProcessor has two strategies. Strategy 1 (exact
  fingerprint = tool name + serialised inputs; `warn_threshold=3`,
  `threshold=10`) catches real identical-command loops — it fired only **61**
  times total and legitimately raised `loop_detected` on task_000536
  (identical failing SQL repeated 8-9x) and task_001031. Strategy 2
  (`name_warn_threshold=8`) warns on tool-*name* repetition alone. Because
  the tb2 agent has **exactly one tool (Bash)** — a hard benchmark constraint
  (see tb2-playbook: "The task agent has exactly one tool: Bash ... cannot be
  changed") — Strategy 2 fires on every task that makes >=8 consecutive Bash
  calls, i.e. essentially every non-trivial task. It carries no information.
- Verified (Read):
  - `harnessx/processors/control/loop_detection.py:196-204`: Strategy 2 computes
    `s2_run` from `event.tool_name` only and emits `_NAME_WARN_TEMPLATE`
    ("You have called `{tool}` {count} times consecutively with different
    arguments ... are you going in circles?") whenever `s2_run >=
    name_warn_threshold` and no Strategy-1 warning already fired.
  - task_000015 final tool message body: "Task completed successfully!
    [LoopDetection] ⚠️  You have called `Bash` 13 times consecutively with
    different arguments. This suggests you are stuck in a repetitive pattern"
    — appended to a *successful* completion result. The warning is factually
    wrong (the calls had different arguments and were productive) and persists
    in context.
  - Strategy 1's genuine detection stays untouched: task_000536 body shows the
    exact-match template ("The exact same tool call(s) have been issued 8 times
    in a row") firing on a real stuck loop — that path is `warn_threshold` /
    `threshold`, which this candidate does NOT change.
- Why Configuration not Control/Instruction: the mechanism (Strategy-1
  exact-match detection) is correct and worth keeping; only one knob
  (`name_warn_threshold`) governs the structurally-meaningless Strategy-2
  branch. Authoring a new processor to strip warnings would duplicate an
  existing, correct component; a prompt rule can't suppress a mechanically
  injected tool-result suffix. The narrowest correct lever is the existing
  knob. A bigger lever is not warranted.
- Retroactive check (A-corrective): partial-yes. This is primarily a
  **cost/context-hygiene + desensitization** fix, not a claimed single-task
  flip. The honest retroactive statement: removing 567 false "you are going in
  circles" injections (a) cuts persisted-context tokens on the 47 affected
  tasks and (b) stops training the model to ignore loop warnings, so the *real*
  Strategy-1 warnings (61 fires) land with more salience. It does not by itself
  fix any capability/logic gap, and I do not claim it flips a specific failing
  task — which is why it ships as a low-risk Pareto-positive hygiene change, not
  as a targeted corrective. If a marginal task (e.g. one currently teetering on
  premature `done` after being told it's "going in circles") flips, that is
  upside, not the thesis.

- expected_global_gain: Removes 567 false-loop injections across 47/50 tasks.
  Cleaner context on every longer task; the meaningful exact-match loop detector
  (which correctly killed 000536-style real loops) is preserved and now less
  drowned out. Plausible marginal upside on tasks where the spurious "are you
  going in circles?" nudge pushes the agent toward premature `done` on
  legitimately long multi-step work (the biggest observed lever per playbook is
  sustained planning/execution, which this false signal discourages).
- regression_risk: Very low. The only behavioural change is the *absence* of a
  warning that provably fired on passing tasks without helping them (they
  passed *despite* it). Strategy 1 (exact-repeat) is unchanged, so real
  identical-command loops are still caught and still raise `loop_detected` at
  `threshold=10`. No processor code authored; no pipeline reorder; single knob.
- cost_shift: Net reduction. Each suppressed warning is ~350 chars appended to a
  tool result that then persists for the rest of the run; 567 of them across the
  benchmark, concentrated on the longest (most expensive) tasks, is a
  measurable input-token saving with zero added compute.
- rollback_trigger: If R5 pass_rate drops below 32/50 OR any task that passed in
  R3 regresses into a `loop_detected`/`budget_exceeded` state that the
  name-only warning would plausibly have pre-empted, revert
  `name_warn_threshold` to 8.
