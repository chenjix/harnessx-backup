# Candidates — R5

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `LoopBudgetGuardProcessor`: count total (not just consecutive) issues of
each whitespace-normalized Bash command per task; nudge once at 3 issues and
physically suppress execution at 5, forcing a different action and reclaiming
the step budget.

- Tasks affected: task_000118_3043e92d, task_000863_7acceb19,
  task_001032_1adaccb9, task_001652_86e1d185 (dominant); also partially
  task_001031_a8f0eb37.
- Signal: `exit_reason=budget_exceeded` at the 80-step cap on 8 R4 failures;
  for these tasks the per-command repeat count of the single most-issued
  command is 22-33 out of ~33 total tool calls (near-total degenerate loop).
  The incumbent (R1) config has NO loop guard active — the R2 breaker was
  reverted at the R3 gate back to R1.
- Verified (Read of `.messages.json`):
  - task_000118 step loop: `python3 /home/user/deployment_monitor.py & sleep 2 ; ps aux | grep -v grep` issued **33x identically**, each returning the same `ps aux` listing showing the monitor is NOT running — the model relaunches a crashing process without ever debugging it.
  - task_000863: identical `cat > /tmp/process_legacy.py << EOF ...` heredoc write **29x**.
  - task_001032: identical `cat > /home/user/safe_extractor.cpp << EOF ...` heredoc write **31x**.
  - task_001652: identical `strings /app/legacy_note.png ... | grep -E "[A-Za-z0-9]{10,}"` **22x**, each `(exit 1, no output captured)`; the assistant literally narrates "I'm stuck in a loop repeating the same command" 20x yet re-issues the same command. It only recovered (found tesseract, extracted the token) AFTER ~50 wasted steps and then ran out of budget.
- Why Control not Instruction: the loop is a mechanical failure to react to an
  unchanging tool result; the model already *knows* it is looping (it says so)
  but cannot stop itself — a prompt rule cannot enforce the stop, only a
  processor that physically suppresses the redundant execution can. Why not
  Configuration: no existing knob detects identical-command repetition
  (CustomEditToolProcessor only warns on over-writes and only passively). Why a
  new processor vs re-using the reverted R2 breaker verbatim: R2 tracked only
  *consecutive* repeats and suppressed at streak 5, which misses the
  interleaved-narration loop (task_001652) and cycling loops; this version keys
  on *total* per-command issue count, catching both shapes and biting sooner in
  effective terms.
- Retroactive check (A-corrective): yes for the budget-reclaim shape — on
  task_000118/000863/001032 the loop IS the blocker (budget burned entirely on
  one useless command); suppressing at 5 reclaims ~28-30 steps and forces the
  model onto the actual problem (why the process crashes / whether the file is
  already written). task_001652 is the strongest case: it *did* find the
  solution path (tesseract) but only after the loop wasted its budget — cutting
  the loop off early leaves ample budget to finish. For task_001031 (MPI
  deadlock) the retroactive answer is partial: breaking the loop reclaims budget
  but the underlying capability gap may still block — counted as upside-only, not
  a guaranteed flip.
- expected_global_gain: flips a subset of the 8 `budget_exceeded` failures
  (largest homogeneous R4 failing cluster, cross-domain: system_administration,
  file_operations, security, scientific_computing) by converting whole-budget
  degenerate loops into bounded runs with budget left to recover. Generalizes
  because it keys purely on command-repetition, never on task content.
- regression_risk: Low. Strict no-op on any run that does not issue the exact
  same normalized command >=3 times — the 23 R4 passing tasks all finish in
  8-54 steps with adaptive, distinct commands and never repeat one command 3+
  times, so they never arm the nudge or suppression. The one theoretical risk is
  a legitimate poll loop (e.g. `sleep 1; curl health` retried while a service
  boots); mitigated because such polls are usually not byte-identical across
  many iterations and the suppression only fires at 5 total identical issues,
  after which a different action (longer sleep, different check) is exactly what
  is wanted anyway.
- cost_shift: Strongly negative (cheaper). Eliminates 20-30 wasted identical
  tool executions and the associated 250-3200s of runaway wall-clock on the
  looping tasks; passing tasks are unaffected.
- rollback_trigger: If R6 regresses any currently-passing task where LoopGuard
  fired, or the `budget_exceeded` count does not drop, revert the processor.
