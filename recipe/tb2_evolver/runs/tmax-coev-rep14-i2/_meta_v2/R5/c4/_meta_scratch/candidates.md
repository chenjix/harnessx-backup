# Candidates — R5 c4

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Append an independent-recomputation + spec-ambiguity-probe directive to the
stock self-verify checklist, fired only on numeric-compute tasks and only on the
turn the checklist message is present (new `ComputeVerifyAddendum` processor).

- Tasks affected (>=2 distinct, same mechanism — agent commits a self-consistent
  but deterministically wrong number after a format-only self-verify, exits
  `done` in few steps):
  - task_000111_cbada64a (assigned focus) — OLS slope committed m=2.5056 vs
    oracle 2.5997; exit done at 7 steps.
  - task_001330_f5aff1f5 — committed m=0.048 vs 0.050; verifier hint names the
    exact spec ambiguity ("Ensure you used numpy.random.seed(42)"); exit done 8.
  - task_001937_ac874115 — Optimal Grid 60 vs 50 (search boundary / off-by-one);
    exit done 14.
  - task_001653_c4cafa73 — Centroid 36.37,45.81,38.03 / Distance 18.62 vs oracle
    42.01,45.90,43.07 / 11.34 (indexing / assignment error); exit done 8.
- Signal: `eval_passed=False`, `exit_reason=done`, low `steps` (7-14) across the
  scientific_computing / data_science compute cluster; final_pytest is a single
  numeric-equality assertion `abs(x - <oracle>) <= tol`.
- Verified (Read of task_000111.messages.json):
  - step 5 (assistant): "Let me verify the output format is correct and the
    values make sense. ... This is in the format `m,c,ci_lower,ci_upper` with 4
    decimal places as required." — verification is format-only.
  - step 6 (`_tb2_self_verify` fires): assistant response is a requirements
    checklist of "✓" ticks plus `ls -lh`; NO independent recomputation of m,
    NO probe of the reading / precision path. Exits immediately after.
  - task_001330 final_pytest tail: `assert '0.048' == '0.050' ... Ensure you
    used numpy.random.seed(42) and the correct parameters` — a seed/draw-order
    ambiguity the agent never surfaced.
  - task_001937 final_pytest tail: `Expected Optimal Grid to be 50, but got 60`
    — a boundary/off-by-one the agent never re-derived.
- Why Control not Instruction: the sibling live bet (h_numeric_crosscheck_v1,
  R1, instruction lever, still pending, `instruction` scoreboard 0/1 accepted)
  already tries the broad system-prompt-swap version of this idea. A whole-prompt
  rewrite pays the guidance cost on EVERY task (all ~50, most non-compute) and
  competes with SiblingSystemPromptBuilder. A Control processor is the narrower,
  lower-regression mechanism: it augments the EXISTING self-verify checkpoint
  in-place, fires the directive only when (a) the task is a numeric-compute task
  by description AND (b) the stock checklist message is actually present, and is
  a total no-op on the ~40+ non-compute tasks and on any run where self-verify
  never fires. It is a mechanism gap (the stock checklist has a keep-alive item
  and a generic "values correct" item but NO independent-recompute step), not a
  knowledge gap, so Control over Instruction.
- Why Control not Configuration: no existing knob turns the self-verify checklist
  into a compute-aware one; the checklist text is a hardcoded constant in the
  read-only harness module. A new conditional processor is required.
- Retroactive check (A-corrective): yes for the spec-ambiguity members
  (task_001330 seed/draw-order, task_001937 boundary, task_001653 indexing) — a
  forced enumeration of "seed & draw order / index convention / boundary
  inclusivity / off-by-one" at the decisive self-verify turn puts the exact
  failing choice in front of the agent while it still has budget to fix it.
  For task_000111 the answer is qualified-yes: OLS is deterministic, so a blind
  recompute would agree; but the directive specifically asks for an INDEPENDENT
  tool/language recheck (Python/numpy against the C++ result) plus a "did you
  read EVERY input record" count check — either surfaces a C++-side reading /
  precision bug that reproduces as 2.5056, which a same-language recompute would
  not. The value is concentrated in the spec-ambiguity majority of the cluster.

- expected_global_gain: Flips part of the recurring compute cluster (>=4 tasks
  this round, multiple domains) whose failures are numeric-value errors caused by
  un-probed spec ambiguities / single-run trust, not by missing capability. The
  directive is generic strategy (no task literals/constants/code) so it transfers
  to any unseen numeric-compute task.
- regression_risk: Very low. Fires <=1x/task, ONLY on armed compute tasks AND
  ONLY when the stock self-verify checklist is the trailing user message; it
  mutates that one user-message string in place (no insert/drop/reorder —
  contract-clean, AUTO-CHECK passed). On the ~40+ non-compute tasks it is a
  complete no-op. Worst case on an armed task: 1-3 extra verification Bash calls
  near exit; the cited cluster all finished in <=14 steps with ample headroom.
- cost_shift: Negligible-to-slightly-positive. Adds a few verification tool calls
  on armed compute tasks that reach exit (replacing an immediate wrong-answer
  exit with a chance to catch the bug); exactly zero on non-compute tasks. No
  forced extra model turns.
- rollback_trigger: Revert if next-round pass_rate is flat/down on the compute
  cluster, OR any previously-passing short compute task regresses to
  max_steps/budget_exceeded attributable to the added verification steps, OR
  replay fails on ComputeVerifyAddendum.
