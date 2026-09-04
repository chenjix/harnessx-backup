# Candidates — R3 (tmax-coev-rep12-i2)

Baseline: R0=20/50, R1=20/50, R2=21/50. Both prior rounds pulled the
`control` lever. R2's `ContentRepetitionRecoveryProcessor` **never fired**
in the R2 run (verified below) because it mis-modelled the loop shape.

## Candidate C-003
[lens: failure | lever: control | intent: corrective]

Add a `CommandRepetitionRecoveryProcessor` that keys on the **normalized Bash
command string** over a rolling window and injects an escalating corrective
nudge once the same command has been issued `repeat_threshold` (4) times —
catching the repeated-command loop that all three existing loop guards miss.

- Tasks affected (budget_exceeded loop cluster, all pinned at 80 steps):
  task_001028_5bc8bc70, task_000032_3fb303f6, task_000603_3ed5cdb5,
  task_000348_31fb8c8a, task_001465_aa3ed3f8, task_000910_16cc0daf,
  task_000716_206dc0f6, task_000796_828a72cf, task_002108_a8cfbf2a
  (>=2 distinct tasks, same mechanism).
- Signal: `exit_reason=budget_exceeded`, `steps=80`. Tool-call analysis over
  each `messages.json` (normalized command counter) shows massive duplicate
  re-issue of the SAME command:
  - task_001028: 33/33 tool calls identical (`echo 'X 192.168.1.1 - GET /test?q=<script>...' | ...`).
  - task_000032: 34/34 tool calls identical (`# Let me try to see if the hash might be a di...`).
  - task_000603: 15/17 identical (`source "$HOME/.cargo/env" && cd /home/user/ed...`).
  - task_000348: compile command reissued 10x; server-kill 5x; nohup start 5x.
  - task_001465: identical `curl -X POST http://localhost:5000/embed ...` 13x.
- Verified (body): R2 `content_repetition_recovery` nudge text
  (`change tactic concretely` / `stuck in a repetition loop`) appears **0 times**
  across all 50 `messages.json` — the R2 guard never armed. Reason: on
  task_000910 the assistant turns are long (1112/1964 chars) and *oscillate*
  in wording, so max-consecutive-same-120-char-prefix run = 1; the loop is
  visible only at the **tool-call layer** (`dup_reissued=15` there). The env
  layer even shows the agent narrating "I've been stuck in a loop" while it
  keeps re-issuing the same command — i.e. it *knows* it's looping but the
  harness never intercepts the mechanical re-issue.
- Why Control not Configuration: the existing loop detectors cannot be
  re-parameterised into this — `LengthTruncationRecoveryProcessor` gates on
  `finish_reason==length` (these finish normally with tool calls);
  `CustomEditToolProcessor` (threshold=7) counts only file-write commands
  (these loops re-issue curl/compile/probe commands); the R2 content
  fingerprint keys on assistant content, which varies turn-to-turn here. A new
  detector keyed on the tool-call command string is the missing mechanical hook,
  not a knob tweak on an existing one.
- Why Control not Instruction: the agent already *narrates* awareness of the
  loop ("I've been stuck in a loop") and still re-issues the command — a prompt
  rule telling it to avoid loops is exactly what it's already failing to self-
  apply. A mechanical interception that fires on the Nth identical command is
  the only reliable stop.
- Retroactive check (A-corrective): yes for step/cost — every cited task hits
  the 80-step cap purely on re-issue; intercepting at command #4 would cut
  ~700-1900s and ~70 wasted steps per task and route the escalated nudge toward
  writing the required output before the cap (the specific gap that scored these
  0 — e.g. task_000032 never wrote `/home/user/report.txt`). Flip probability is
  highest on tasks whose only remaining blocker is "never committed a partial
  output" (task_000032, task_000348, task_002108); genuinely capability-bound
  tasks (crypto crack) may still fail but stop wasting budget.
- expected_global_gain: closes the repeated-command loop class (~9 tasks all
  at the 80-step cap). Even if only 1-2 flip, the escalation nudge forces an
  output-write attempt on tasks that currently reach the cap empty-handed; the
  rest recover large step/cost budget. Generalizes to any future task where the
  agent re-issues an unproductive command.
- regression_risk: a false-positive nudge on a legitimately iterative task that
  re-runs one command (e.g. poll-until-ready). Mitigated: threshold=4 is clear
  of the 2-3x retries seen on passing tasks (build→fix→rebuild); passing tasks
  finished in 6-41 steps and none showed a >=4x identical-command run. The nudge
  is advisory (+1 user turn), never blocks the tool call, so worst case is one
  redundant message.
- cost_shift: net negative (cheaper). Interrupting a 33-turn identical-command
  streak at turn 4 removes ~29 wasted model+tool turns; pure no-op on non-looping
  tasks (deque stays under threshold).

## Idiosyncratic / capability-gap notes (no harness fix — skip)

- task_000998_4d9c7852: verifier crashes with `ImportError ... circular import`
  because the task *requires* writing `/home/user/operator.py`, which shadows
  stdlib `operator` when pytest collects from that CWD. Single task; the file
  name is task-mandated, so no generalizable harness fix. → NEEDS_FROM_HUMAN.
- The 19 `exit_reason=done` failures (task_000164, task_000785, task_000925,
  task_000908, task_000311, ...) are genuine correctness/domain gaps: the agent
  ran its own scripts, believed they passed ("all validations passed"), and
  committed wrong values (0.247<0.001, 1.0 vs 10.0, 4 vs 3). A verify-before-exit
  nudge won't help — the agent already thinks it verified. Model capability gaps.
- task_000773 (imageio): dep guard worked (imageio now imports) but the image
  lacks the ffmpeg backend to read mp4 — environment/capability, not harness.
