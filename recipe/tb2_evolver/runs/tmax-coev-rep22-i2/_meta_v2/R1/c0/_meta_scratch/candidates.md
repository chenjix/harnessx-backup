# Candidates

Assigned focus: `task_000010_644ab1c2` fails (`budget_exceeded`, 80 steps).

## Diagnosis of the assigned task

The task requires writing `/home/user/operator.py`. Because the agent's CWD is
`/home/user`, running `python3 operator.py` there causes the local file to shadow
Python's stdlib `operator` module, breaking every import. The agent recognised the
issue but got stuck: from step ~45 to step 80 it emitted the **identical** Bash
command (`cat > /home/user/operator.py << 'EOF' ... EOF; chmod +x ...`) ~12 times
in a row, each returning `(exit 0, no output captured)`, making zero progress until
`budget_exceeded`. The existing `CustomEditToolProcessor` fired a soft text warning
at steps 46 and 63 but the model ignored it and kept repeating verbatim. The
`LengthTruncationRecoveryProcessor` did not fire (there was no `finish_reason=length`
— the model *was* issuing tool calls, just identical ones).

This is a **harness deficiency**, not a capability gap: the harness has no mechanism
that detects an *identical-tool-call repetition loop* and forcibly interrupts it.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatedCommandBreaker` `MultiHookProcessor` that detects N consecutive
byte-identical tool calls and, once tripped, injects an escalating forceful user
redirect that names the exact repeated command, tells the model the command is not
changing state, and forces a diagnostic pivot instead of a re-emission.

- Tasks affected (>=2, same mechanism — identical consecutive Bash calls burning the
  step budget):
  - `task_000010_644ab1c2` — longest identical run 12 (`cat > operator.py ...`)
  - `task_000118_3043e92d` — longest identical run 31 (`rm -f logs/*; ls -la logs/`)
  - `task_000958_4bb2b05d` — longest identical run 26 (`pkill -9 -f server; ... ./server &`)
  - `task_001207_44e97fe1` — longest identical run 25 (`log_sanitizer.elf "..."`)
  - `task_000028_7fe033ac` — longest identical run 20 (`wait; sleep 1; ps aux | grep server`)
  - `task_000313_1dce9844` — longest identical run 16 (`pkill -9 -f socat; ...`)
  - `task_001857_24daeef3` — longest identical run 11 (`pkill -9 -f diagnostic_server; ...`)
- Signal: `exit_reason=budget_exceeded` at exactly 80 steps on 8/50 tasks; on 6-7 of
  them a single command repeats 11-31× consecutively (verbatim `tool_calls.arguments`).
- Verified (Read `messages.json`):
  - task_000010 steps 45,47,49,51,53,56,58,60,62,64,66,68 — all
    `{"command":"cat > /home/user/operator.py << 'EOF'\n#!/bin/bash\ncd /tmp\nexec python3 /home/user/scripts/operator.py \"$@\"\nEOF\nchmod +x /home/user/operator.py"}`,
    each returning `(exit 0, no output captured)`.
  - task_000118 — 31 consecutive `{"command":"rm -f /home/user/logs/*; ls -la /home/user/logs/"}`.
  - task_000958 — 26 consecutive `{"command":"pkill -9 -f server 2>/dev/null; sleep 1\ncd /home/user && ./server & ..."}`.
- Why Control not Configuration: the existing `CustomEditToolProcessor` (Configuration
  lever, threshold knob) only matches file-*write* command patterns and only appends a
  soft advisory that the model demonstrably ignores; most of these loops are non-write
  commands (`ps aux|grep`, `pkill`, running an ELF) it never sees. Lowering its
  threshold would neither cover the non-write loops nor add the forceful escalation
  the small model needs. A new cross-cutting `on_after_tool`/`on_before_model` guard
  that keys on the raw tool-call signature is the correct mechanical fix.
- Why Control not Instruction: the model already *knows* (its own narration says it is
  stuck), yet still re-emits — a prompt rule about "don't repeat yourself" cannot bind
  a model already in a degenerate loop; only a runtime interception that changes the
  context after the loop is detected breaks it.
- Retroactive check (A-corrective): yes — on task_000010 the loop begins ~step 45 with
  35 steps of budget left; a forced pivot ("this exact command has run 3× with no state
  change; STOP repeating it; run a different diagnostic or try a fundamentally
  different approach — e.g. write the file from a directory that is not on sys.path,
  or rename around the shadowing") gives the model 30+ steps to recover. On the
  server/pkill loops the same interruption frees the remaining budget for a working
  approach. The loop is the actual blocker (budget is consumed by pure repetition),
  not a downstream symptom.
- expected_global_gain: targets the largest single failing cluster (8/50
  `budget_exceeded`, ~6-7 of which are pure repetition loops). Even flipping 2-3 is a
  material pass-rate gain; freeing wasted steps helps the rest finish.
- regression_risk: low. The breaker only acts after N (=3) *byte-identical* consecutive
  calls — legitimate work never issues the same command 3× in a row with identical
  output. It replaces the run-loop's own trailing user nudge (contract-safe, +0 net
  messages) rather than inserting, and never blocks the tool. Passing tasks (max
  identical run observed = 2 on passing/short tasks) are untouched.
- cost_shift: net negative (saves tokens). It curtails 20-30 wasted repeat steps per
  stuck task; the injected redirect is a few hundred chars once per trip.
