# Candidates — Round 1 (focus: task_000010_644ab1c2)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the one-shot `CustomSelfVerifyProcessor` with an escalating,
bounded-repeatable self-verify processor that fires a second, targeted
"put the artifact at the EXACT required path — do not exit with a
known-missing/misplaced output" reminder on a second exit attempt.

- Tasks affected (primary): task_000010_644ab1c2. Same-mechanism
  cluster (declared-done-but-required-output-absent, self-verify's
  single fire insufficient): task_000958_4bb2b05d, task_000264_ab8c7253
  (both ran the checklist once then exited on an insufficient re-verify).
- Signal: `agent.finished=no_tool_calls`, `exit_reason=done`, `reward=0`.
  In task_000010 the agent's OWN verification (post self-verify ACK at
  msg 74) printed `operator.py does not exist` (msg 76) yet it exited
  with the file absent; `final_pytest` fails on
  `test_operator_script_exists` — the file was never left at the
  required path.
- Verified (Read of task_000010_644ab1c2.messages.json):
  - msg 24/50: agent's solution actually WORKS (backup + socat/py port
    forward + pexpect CLI automation all succeed) — this is not a logic
    gap.
  - msg 20/56/66/82: `import operator.py` shadows the builtin `operator`
    module, so running `python3 /home/user/operator.py` crashes; agent
    renames the working file to `k8s_operator.py`.
  - msg 74 (self-verify ACK fires — once), msg 76: agent runs
    `ls /home/user/operator.py` → "operator.py does not exist".
  - msgs 77-89: agent repeatedly restates "the task requires operator.py
    but there's a naming conflict; I'll accept k8s_operator.py",
    hits the token limit 3× (user msgs 60/78/84 "cut off by token
    limit"), and exits WITHOUT ever leaving a file at
    `/home/user/operator.py`. The single self-verify fire was already
    spent, so no further push arrived.
- Why Control not Instruction: the checklist that fires here IS an
  instruction, and it already fired — the agent read it, verified, and
  found the miss. The gap is mechanical: the enforcement is one-shot, so
  a second exit attempt after acknowledging a missing artifact receives
  nothing. Only a processor can re-fire the exit-verify hook a bounded
  number of times keyed on repeated exit intent; a static prompt rule
  cannot react to "you already verified and are exiting again."
- Why Control not Action: no new agent capability is missing — Bash can
  create the file. The agent already knew the file was missing; it just
  needed a second mechanical push at the exit boundary.
- Retroactive check (A-corrective): yes — at msg 76 the agent had
  already discovered `operator.py` missing. A second escalating nudge on
  its next exit attempt ("create the artifact at the exact path; do not
  substitute a name") re-fires precisely where the agent was
  rationalising a substitute, giving it another turn to `mv`/rewrite the
  working script to `/home/user/operator.py`, which passes
  `test_operator_script_exists`.
- expected_global_gain: flips the declared-done-but-missing-artifact
  cluster (>=2 tasks) where the agent's own verification surfaces or
  could surface the miss on a second pass; generalises to any task where
  a required output is left at the wrong path/name — a recurring TB2
  failure class ("correct logic, wrong path", per playbook).
- regression_risk: low/bounded. `max_fires=2` means at most ONE extra
  exit round-trip beyond current behaviour; a genuinely finished agent
  answers the escalation, appends SUCCESS, and exits. Worst case is a
  few extra tokens/steps on already-passing tasks. Same singleton group
  ('tb2_self_verify') so no double self-verify mechanism. Contract check
  passes (appends exactly +1 user message, mirrors stock processor).
- cost_shift: small positive on tasks that trigger the second fire
  (one extra model turn + short nudge, ~a few hundred tokens); zero on
  tasks that never reach a second exit attempt.
