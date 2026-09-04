# Candidates — R2

## Candidate C-002
[lens: failure | lever: control | intent: corrective]

Add a warn-only, escalating `RepeatedCommandBreakerProcessor` that, on
consecutive IDENTICAL Bash tool calls, appends a deliverable-grounded directive
to the tool result (verify/produce the task's required output artifacts, stop
re-running the same command) — escalating in severity at higher repeat counts,
but never raising.

- Tasks affected (failing, same mechanism):
  - task_000958_4bb2b05d — 33 tool calls, ALL 33 identical (broken server
    restart loop `pkill; ./server; ps`).
  - task_001818_b251e5ea — 27 of 29 identical; budget_exceeded.
  - task_001032_1adaccb9 — 25 of 29 identical; budget_exceeded.
  - task_000264_ab8c7253 — 21 consecutive identical `cat > /tmp/test.sql`
    heredoc writes; the primary flip candidate (see Retroactive check).
- Signal: `exit_reason=budget_exceeded` at steps=80 on all four; message-log
  fingerprinting shows one command fingerprint dominating the consecutive tail
  (max_consec_identical = 33/27/25/21). Each repeated call returns the same
  result (frequently `(exit 0, no output captured)`).
- Verified (Read of messages.json):
  - task_000264 last 16 messages: the identical `cat > /tmp/test.sql << 'EOF'
    WITH RECURSIVE subordinates AS ...` heredoc is re-issued back-to-back, each
    returning `(exit 0, no output captured)`. The `[EditDetection] File
    /tmp/test.sql has been modified more than 7 times ... try a fundamentally
    different approach` warning appears **3 times** and is IGNORED. A grep of all
    29 tool-call arguments finds **zero** references to the required deliverables
    `top_managers.csv` / `query_plan.txt` — the agent had a correct recursive CTE
    but never ran it to produce output.
  - task_000958 repeated command (33x): `pkill -9 -f server ...; ./server & ...;
    ps aux | grep server` — same restart loop, no progress.
- Why Control not Configuration/raise: the stock `LoopDetectionProcessor` exists
  but RAISES `LoopDetectedError` at 5 identical calls. Two currently-PASSING
  tasks issue 27 consecutive identical commands and still pass (task_000536 =
  audit-script re-write 27x → pass; task_001089 = malformed empty tool call 27x
  → pass). The identical-command signature does NOT discriminate pass from fail,
  so a hard raise would produce a 2-task REGRESSION to chase a 1-task flip. A
  new warn-only processor is required; simply enabling the stock detector is
  Pareto-negative. Why not Instruction: the existing generic edit-warning IS an
  in-context nudge and it was ignored 3x on task_000264 — a system-prompt rule
  would be the same class of signal. The gap is a mechanical guard that fires
  precisely on the identical-repeat condition with an escalating, deliverable-
  grounded message, which only a processor can express uniformly across tasks.
- Retroactive check (A-corrective): yes (partial, task-dependent) — for
  task_000264 the agent already had the correct query and only needed to stop
  re-writing the intermediate SQL file and instead run it + write the two named
  output files; an escalating "STOP repeating; verify/produce the required
  deliverable" directive at repeat 3-5 is exactly the missing push (the generic
  edit-warning was too weak). For task_000958/1701 (C++ HTTP microservice) the
  block is a genuine capability gap — the breaker reclaims budget/cost but a flip
  is not expected there.
- expected_global_gain: Targets the residual `budget_exceeded` cluster left after
  R1 (which converted narration loops into command loops). Plausible flip on the
  data_querying/data_processing subset (264, and possibly 1818/1032 which produce
  partial output) where the agent is one different action away from the
  deliverable; hard cost reclamation on the capability-bound C++ tasks.
- regression_risk: LOW by construction — warn-only, never raises, so no task is
  terminated early. The only surface touched is appended text on a tool result
  when the SAME command repeats >=3x consecutively. The two passing 27x-repeat
  tasks (536, 1089) keep running to completion; the appended nudge is advisory
  and, in the worst case, ignored exactly like the existing edit-warning is.
  Interleaving any different command resets the count, so varied exploration in
  passing long-horizon tasks (which have max_consec_identical < 3) is never
  touched.
- cost_shift: Neutral-to-negative. The nudge text is a few hundred chars appended
  to at most one tool result per repeated run; if it breaks a loop earlier the
  task spends fewer of its 80 steps, reducing tokens/cost on the affected
  cluster. No new model calls.
- rollback_trigger: Revert the processor if R3 shows any of the passing repeat-
  loop tasks (536, 1089) regress T->F, OR if net pass-rate drops vs R1's 32/50.
