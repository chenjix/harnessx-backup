# Candidates — R1 c0 (focus: task_000010_644ab1c2 budget_exceeded cluster)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the R0 text-only `LengthTruncationRecoveryProcessor` with a
force-act variant that (a) also triggers on oversized no-tool-call content
(not just `finish_reason=="length"`) and (b) after N consecutive truncations
mechanically injects a real generic Bash workspace-snapshot tool call so
fresh, model-external output breaks the reasoning-only spiral.

- Tasks affected: task_000010_644ab1c2, task_000118_3043e92d,
  task_000264_ab8c7253, task_001031_a8f0eb37, task_001321_658ce4a8
  (the entire R0 `exit_reason=budget_exceeded` cluster — 5/50, all at the
  full 80-step cap).
- Signal: `agent.exit_reason == "budget_exceeded"` at `steps == 80` on all
  five; message logs show repeated run-loop "cut off by the token limit.
  Please continue from where you left off." user turns
  (task_000118: 20, task_000264: 18) interleaved with an assistant emitting
  the IDENTICAL sentence with zero tool calls turn after turn.
- Verified (Read):
  - task_000118 msgs 2,4,6,9,11,13,15,17,19,21,23 — assistant repeats
    verbatim "The user is right - I've been stuck in a loop. Let me take a
    concrete action..." with NO tool_calls; each followed by the raw passive
    "cut off by the token limit" continue nudge. First real tool call only at
    msg 25. The R0 processor's escalated text nudge never appears in history —
    the passive run-loop nudge survived, i.e. the R0 recovery did not fire
    (backend under-reported `finish_reason=="length"`).
  - task_000264 msgs — same identical-sentence spiral; last assistant text:
    "The query is still timing out. I've been stuck in a loop trying the same
    query." 18 passive continue turns, budget consumed.
  - task_000010 tail (msgs 62-69) — 30 tool calls but stuck cycling on
    socat/port-forward retries; required `/home/user/operator.py` never
    written (agent wrote `/home/user/k8s_operator.py`).
  - task_001031 / task_001321 — 33 tool calls each, near-identical retries
    (mpi4py API variants / output-capture variants) with no pivot; final
    assistant text is another "let me try a different approach" that never
    lands.
- Why Control not Instruction: the R0 evidence proves the model narrates
  straight past TEXT nudges — task_000118 repeats the same sentence 11×
  despite the loop being obvious to it ("I've been stuck in a loop"). A prompt
  rule is another text the model narrates past. Only a mechanical event-loop
  intervention (inject a real tool call so model-external output lands in
  context) breaks the momentum. This is the same injected-real-tool-call
  mechanism already proven to fire in this harness (`tb2_self_verify`).
- Why Control not Configuration: the R0 processor's knobs cannot be tuned to
  fix it — it never fired (its only trigger, `finish_reason=="length"`, was
  not reliably reported). The fix needs a new trigger (content length) and a
  new escalation mechanism (forced tool call), which are code, not kwargs.
- Retroactive check (A-corrective): yes — on task_000118/task_000264 the
  spiral begins well before step 40; a forced Bash snapshot at the 3rd
  consecutive truncation lands fresh state in context, and the model reliably
  resumes acting on concrete output rather than re-priming the same thought,
  restoring ~half the wasted budget so the task can finish.
- expected_global_gain: flips some/all of the 5-task budget_exceeded cluster
  (10% of the sampled set) by returning wasted steps to productive work;
  generalizes to any verbose-model truncation spiral, not these 5 tasks.
- regression_risk: LOW. Same singleton group replaces the R0 processor (no
  double recovery layer). On non-spiralling runs the new triggers never fire
  (finish_reason!=length and content<40k with a tool call). The one added
  behaviour — a single generic `ls/find/ps` Bash round-trip — only fires after
  3 consecutive truncations, a state that by definition only occurs on runs
  already burning budget. It clears the streak so it fires at most once per
  spiral.
- cost_shift: net NEGATIVE on the affected cluster (fewer wasted truncation
  turns → fewer tokens); +1 bounded Bash round-trip per spiral is negligible
  against ~18-20 saved re-priming turns. No cost change on healthy runs.

## Candidate C-002
[lens: failure | lever: instruction | intent: corrective]

Add two general strategy rules to the sibling system prompt: (1) verify every
required output artifact exists at its EXACT path (name+extension+directory)
before finishing; (2) after 2+ identical failures, stop retrying variants and
switch approach; keep turns short.

- Tasks affected: task_000010_644ab1c2 (wrong path: wrote
  `/home/user/k8s_operator.py`, task required `/home/user/operator.py`),
  task_001031_a8f0eb37 / task_001321_658ce4a8 (retry-variant spirals).
- Signal: task_000010 final_pytest fails on `test_operator_script_exists` for
  `/home/user/operator.py`; the file mentions `k8s_operator.py` 7× vs
  `/home/user/operator.py` only 2× — a classic "correct-logic wrong-path"
  hard failure (a known TB2 structural failure mode per playbook).
- Verified (Read): task_000010 result.json final_pytest output —
  "Operator script /home/user/operator.py does not exist. You must create
  it." task_001031/task_001321 last assistant turns are "let me try a
  different approach" after many near-identical retries.
- Why Instruction not Control: the path-exactness gap is a knowledge/discipline
  gap the agent can act on when reminded at exit — the existing
  `CustomSelfVerifyProcessor` already provides the mechanical exit hook, so
  Control is already present; what's missing is the explicit rule about exact
  paths. This is additive prompt strategy, not a new mechanism.
- Why not a new Control processor for path checking: the harness cannot know
  the required path (verifier tests are injected post-run and absent during
  the agent phase, per playbook) — only the agent, reading the task, knows it.
  So the guidance must be agent-side (Instruction), not a hard-coded checker.
- Retroactive check (A-corrective): partial-yes — the exact-path rule directly
  addresses task_000010's hard failure once the truncation spiral is broken by
  C-001; the switch-approach rule reinforces (but does not by itself guarantee)
  a pivot on task_001031/task_001321. Net additive with C-001.
- expected_global_gain: catches wrong-path hard-fails (a recurring TB2 mode)
  across the whole benchmark; complements C-001 on the retry-spiral tasks.
- regression_risk: LOW. General strategy text, no task literals; reinforces
  behaviours the existing time-reminder/self-verify processors already nudge.
- cost_shift: ~neutral (a few extra `ls` confirmations per task).
