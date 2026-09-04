# Candidates

Assigned focus: `task_000010_644ab1c2` (system_administration) fails,
`exit_reason=budget_exceeded`, 80 steps, 879s (longest run in the round).

## Diagnosis of task_000010

The task: write `/home/user/operator.py` that backs up manifests, sets up a
`socat` port-forward (9090→8080), and drives an interactive CLI with pexpect.
Two harness-relevant failure shapes appear in the trajectory body:

1. **Advisory-ignored identical-command loop (dominant budget sink).** The
   agent re-issues byte-identical Bash commands turn after turn. The existing
   `RepeatedCommandBreaker` *fires* — the tool result carries
   `[RepeatedCommandBreaker] ... run 6/7/8 times ...` — but it only *appends
   text*; the model reads the warning and re-issues the SAME command anyway
   (`python3 /home/user/k8s_operator.py` re-run after each hard warning; the
   zombie-reaping `wait ...; ps aux` line re-run 6-8 times). Each
   `python3 /home/user/k8s_operator.py` also times out at 120s, so 4 identical
   re-runs alone burned ~480s of the 879s. A pure advisory has no teeth against
   a model that ignores advisories.

2. Naming/path mistake: agent renamed the required `/home/user/operator.py` to
   `k8s_operator.py` to dodge a stdlib `operator` shadowing crash, and never
   restored the required path (verifier wants `/home/user/operator.py`). This
   is single-task / task-specific and does not recur across ≥2 tasks — logged
   as a model capability issue, not shipped.

The other three `budget_exceeded` tasks (task_001207, task_000506,
task_000747) are binary-reverse-engineering tasks (match a Python reimpl to a
compiled ELF's output) — a distinct capability class, not this loop shape.
So the *loop-advisory-ignored* mechanism is what I ship against; it is the
one generalizable harness deficiency the assigned task exposes, and it is a
whole-benchmark mechanism (the breaker guards every task).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Give `RepeatedCommandBreaker` teeth: after N identical re-runs past a new
`block_threshold`, stop *appending a warning* and instead **block execution**
(`approved=False` + `synthetic_result`) of the byte-identical Bash command, so
the loop cannot keep consuming step/time budget. Warn/hard advisory tiers are
unchanged; the block is a third, higher tier.

- Tasks affected: task_000010_644ab1c2 (primary). Mechanism (identical-command
  loop that ignores the advisory) is benchmark-wide: the breaker runs on `*`
  for every task, so any task that enters this loop shape is capped instead of
  burning to `budget_exceeded`.
- Signal: `exit_reason=budget_exceeded`, 80 steps, 879s. Tool results in the
  body contain repeated `[RepeatedCommandBreaker] ... run 6/7/8 times ...`
  hard warnings, followed by the agent issuing the identical command again.
- Verified (Read of task_000010_644ab1c2.messages.json):
  - steps 2/4/6: identical `wait 171 ...; ps aux | grep ...` command re-issued
    while the tool result already says "run 6/7/8 times ... STOP".
  - steps 15/23/37/60/64: `python3 /home/user/k8s_operator.py` re-issued after
    hard-warning results; step 24 result: "run this EXACT command 4 times",
    step 38: "5 times", step 61: "6 times", step 65: "7 times" — the advisory
    was present and ignored every time.
- Why Control not Instruction: the breaker text IS an instruction, and the
  trajectory proves the model reads it and disobeys. Adding *more* prompt text
  cannot enforce what a warning already failed to enforce. The only mechanism
  that stops the budget burn is a hard `on_before_tool` block that refuses to
  execute the identical command — a mechanical hook, not knowledge.
- Why not just Configuration (lower `hard_threshold`): lowering the warn/hard
  thresholds only makes the ignored advisory appear sooner; it does not change
  that the advisory is ignored. The change in kind (advisory → block) is what
  matters, so a new processor tier is required. The block threshold is set
  conservatively (8) so legitimate short poll/healthcheck repeats (typically
  2-4) are never blocked.
- Retroactive check (A-corrective): yes — had the block fired at the 8th
  identical `python3 /home/user/k8s_operator.py` / zombie-reap run, the agent
  would have been forced off the dead-end command with ~40+ steps and ~500s of
  budget still available to fix the port-forward and restore the output path,
  instead of exiting `budget_exceeded` with the loop still spinning.
- expected_global_gain: caps the worst budget-burn shape (advisory-ignored
  identical loop) on any task that enters it; frees steps/time for recovery.
  Directly targets the assigned failing task and generalizes to the loop class.
- regression_risk: a legitimate task that must run one identical command ≥8
  times in a row would be blocked. This is rare — identical ≥8× with no
  variation is almost always a genuine loop (passing tasks in this round top
  out well below that). The block message explicitly tells the agent to vary
  the command or move on, so a task with a real need can adjust one byte and
  proceed. Threshold 8 keeps the passing clusters (max identical repeats
  observed far lower) untouched.
- cost_shift: net negative (saves tokens/time) — it terminates loops that
  currently run to the 80-step / wall-clock cap. No new cost on non-looping
  tasks (the hook is a cheap hash lookup).
