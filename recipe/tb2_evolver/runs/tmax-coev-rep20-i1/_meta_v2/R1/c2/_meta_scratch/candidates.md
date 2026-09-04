# Candidates — R1 c2 (focus: task_000024_a0664029)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatedToolCallBreaker` processor that detects byte-identical
consecutive tool calls and injects a corrective "stop repeating, change
strategy" user message before the next model turn.

- Tasks affected (failing, same mechanism — >=2 required):
  - task_000024_a0664029 (assigned focus): 27 identical `jq '.version'`
    calls in a row (steps 2–55), then forced compaction that lost the task
    spec, leaving almost no budget for the actual migration.
  - task_000010_644ab1c2: 33/33 identical `python3 -c "import socket..."`
    calls, ran to `budget_exceeded` (80 steps).
  - task_000956_7e92337f: 34/34 identical `python3 -c "import Levenshtein..."`
    calls, `budget_exceeded`.
  - task_001818_b251e5ea, task_000264, task_000329, task_000396, task_000505,
    task_000684, task_001031, task_001088, task_001090, task_001652,
    task_001673, task_000958, task_001498 — all 33/33 identical, all failed.
  - Cluster size: 28/50 tasks emitted a run of >=5 identical consecutive
    Bash calls; 27 of those 28 failed.
- Signal: `agent.exit_reason in {budget_exceeded, max_steps}` /
  `finished=budget_exceeded`; in the messages log, the assistant emits the
  byte-identical `(tool_name, input)` signature every turn with no change in
  the tool result. No `loop_detected` fires — the existing pipeline has no
  detector for the well-formed-but-repeated tool-call loop. `length_recovery`
  only catches the `finish_reason=length` (repetition *inside one generation*)
  variant, which is a disjoint failure shape.
- Verified (Read of message logs):
  - task_000024 steps 2–55: `dump24.py` output shows 27 consecutive
    `assistant` turns each with `tool_calls=[Bash {"command":"jq '.version'
    /home/user/project/config_v1.json"}]` and tool result `null\n`; content
    empty each turn. Step 56 = forced `[PostCompaction]`; the compaction
    summary then hardcoded the wrong `build_deps`/`LD_LIBRARY_PATH`.
  - task_000010, task_000956: `dump_loops2.py` confirms `distinct=1,
    total=33/34, max consecutive identical=33/34` — a single byte-identical
    command emitted for the whole session.
- Why Control not Configuration: no existing knob covers this — the pipeline
  has zero identical-consecutive-call detection to retune; `length_recovery`
  keys on `finish_reason=length`, a different trigger. It needs a new hook.
- Why Control not Instruction: the model has already committed to repeating
  the same command; a static system-prompt rule ("don't repeat commands")
  cannot observe *that a repeat has actually happened this run* — only a
  runtime hook that compares the current call signature to the previous one
  can fire the corrective nudge at the moment the loop forms. Round-0 already
  ran with a system prompt and still looped 33x.
- Why Control not a hard loop-kill: killing the run on N repeats risks ending
  tasks that are close to done or legitimately polling (e.g. task_000344
  passed while emitting 33 identical read-only `python3 -c` computations — a
  hard kill would have been neutral there but a nudge is strictly safer). The
  nudge adds a message and lets the run loop continue, so a legitimate poll
  degrades gracefully (told "result unchanged") instead of being terminated.
- Retroactive check (A-corrective): yes — if a nudge had fired at the 3rd
  identical `jq '.version'` on task_000024, the model would have had ~50 extra
  steps and no forced compaction to complete the migration; on task_000010 /
  task_000956 the model would have been redirected off the dead command
  toward a different inspection long before exhausting budget. The identical
  loop IS the blocker (it consumes the entire step budget), not a downstream
  symptom.
- expected_global_gain: the dominant failing cluster on this benchmark
  (27 failing tasks share this exact mechanism). Breaking the loop returns
  step budget to the actual task work; plausibly flips a meaningful fraction.
- regression_risk: low. The only passing task in the loop cluster
  (task_000344) already `budget_exceeded` with the initial pytest already
  passing — a nudge cannot regress an already-passing final state, it only
  stops wasted spinning. Non-loop passing tasks (task_000587, task_000748,
  task_000912, task_001781) have `max_identical_run=1–2`, well below the
  threshold of 3, so the processor never fires on them.
- cost_shift: net negative (cheaper). Tasks currently burning 33–80 turns on
  a repeated no-op will be redirected earlier, reducing tokens/turns. Worst
  case adds one short user message per fired nudge — negligible.

Note on the assigned-task's *final* assertion failure (`build_deps` produced
as `["core","ui"]` instead of `[{"name":"core"},...]`): that specific schema
mis-mapping is a model reasoning error, not a harness gap — and it was itself
downstream of the loop+compaction that corrupted the task spec. The harness
fix targets the upstream loop that starved the task of budget/context; the
residual schema reasoning is a model capability matter, not patched here.
