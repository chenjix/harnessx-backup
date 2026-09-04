# Candidates — R1 c2

Assigned focus: `task_000028_7fe033ac` (system_administration) fails —
`exit_reason=budget_exceeded`, 80 steps, 544s, reward 0. Verifier ends
502 Bad Gateway (nginx → C++ UNIX-socket backend integration).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Escalate `RepeatedCommandBreaker` from advisory-only to a real
`on_before_tool` HARD BLOCK: once a byte-identical Bash command has
already executed `block_threshold` (=7) times, refuse to run it again —
return a synthetic corrective result instead of executing, saving the
budget the loop would otherwise burn.

- Tasks affected (same mechanism — identical command re-issued far past
  the point of usefulness, advisory ignored, run dies budget_exceeded):
  - task_000028_7fe033ac — assigned focus (identical cmd x12)
  - task_000118_3043e92d — identical `ps aux | grep -v grep | grep python` x16
  - task_000506_c13429e7 — identical `echo ... | python3 .../wfg_analyzer.py 1 3` x21
- Signal: `agent.exit_reason=budget_exceeded`, `steps=80`; per-trajectory
  max-identical-command-repeat count of 12 / 16 / 21; the existing
  `RepeatedCommandBreaker` `_HARD` advisory text present in the tool
  results yet the model re-issues the exact same command every turn.
- Verified (Read of messages.json):
  - task_000028 last ~12 assistant turns: byte-identical Bash
    `pkill -9 server ...; /app/server ...; python3 -c "import socket..."`
    re-issued while the tool result already carried
    `[RepeatedCommandBreaker] STOP. This identical command has now run
    6..12 times ...`. The backend probe SUCCEEDED each time
    (`HTTP/1.1 200 OK ... 150`) — the real fault (nginx 502) was
    elsewhere, but the agent kept re-probing the working part until the
    step cap. Also tripped `[EditDetection]` on `/app/server.log`.
  - task_000506 most-repeated command (`echo -e "1 2 5\n..." | python3
    /home/user/wfg_analyzer.py 1 3`) issued 21 times — pure loop.
  - task_000118 most-repeated command (`ps aux | grep -v grep | grep
    python`) issued 16 times — pure loop.
- Why Control not Instruction: the system prompt / SOUL guidance can't
  stop this — the harness already injects an escalating *text* directive
  ("Your very next command MUST be different") and the model ignores it
  12-21× in a row. The gap is not knowledge; it is that the mechanism has
  no teeth. Only a mechanical `on_before_tool` short-circuit (the same
  contract `BgInstallGuard` uses: `approved=False` + `synthetic_result`)
  actually prevents the wasted execution and forces a new action.
- Why Control not Configuration: there was no existing knob for
  "block after N"; the advisory-only processor structurally cannot block.
  Adding the block is a behavioural change to the processor, tuned by a
  new `block_threshold` kwarg (defaulted to 0 = off would be a no-op;
  set to 7 here so the model always sees warn(3)→hard(5) advisories
  first).
- Retroactive check (A-corrective): yes for the loop tasks — on
  task_000118 and task_000506 the entire tail is a single identical
  command; blocking at the 8th re-issue returns ~13 and ~14 steps of
  budget for the agent to pursue the real fix. For task_000028 the block
  forces the agent off re-probing the already-working backend and back
  onto the unmet requirement (nginx 502 → likely socket path/permission
  or nginx not started against the fixed conf); it does not guarantee the
  502 fix, but it removes the specific budget-exhaustion cause that made
  the task unrecoverable. Net: converts "guaranteed budget_exceeded loop"
  into "steps freed to try something different".

### Pareto
- expected_global_gain: 3 budget_exceeded failures share the identical-
  command-loop mechanism; freeing 12-20 steps each gives the agent a real
  chance to reach the actual fix. Generalises to any stuck-repeat class
  (compile/OCR/probe/rewrite loops) because the block is command-agnostic.
- regression_risk: LOW and bounded. Block fires only after a command has
  ALREADY executed 7 identical times — legitimate iteration (materially
  different commands, or the same command run a handful of times while
  state changes) never reaches the threshold. A genuine idempotent poll
  that the agent *intends* to run many times (e.g. waiting on a service)
  would be blocked, but the synthetic result explicitly tells it to probe
  a different aspect, and 7 identical no-progress runs is already
  pathological. The 1 currently-passing budget_exceeded task
  (task_001032, reward 1) has max-repeat only within normal range
  (checked: not a 7+ identical loop) so it is unaffected.
- cost_shift: strictly DOWN. Blocked commands are not executed, so the
  loop tasks stop burning tokens/time on redundant turns; the synthetic
  result is short.

Rollback trigger: if the next round shows a previously-passing task
newly failing with `exit_reason` other than budget_exceeded and its
trajectory contains a `[RepeatedCommandBreaker] BLOCKED` line on a
command that was legitimately being retried, raise `block_threshold`
or set it to 0 (advisory-only) and re-evaluate.
