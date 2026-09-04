# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace `CustomSelfVerifyProcessor` with a subclass whose injected exit
checklist adds an explicit independent-recomputation / interpretation-audit
step for computed-value outputs (recompute the key quantity by a second
method/tool and reconcile, audit input-row completeness, re-examine
index/boundary/unit interpretation, sanity-check magnitude).

- Tasks affected (assigned focus + cluster):
  - assigned: `task_000111_cbada64a` — OLS slope 2.5056 vs expected 2.5997
    (tol 0.001); `exit_reason=done` after only **7 steps**.
  - cluster (same mechanism — plausible-but-wrong computed value, exit@done,
    no correctness self-doubt): `task_000117_1b598e44` (fixed-width column
    parse: `line[38:46]` grabbed the wrong byte window → `ValueError`),
    `task_001937_ac874115` ("Optimal Grid" 60 vs 50 — off-by-one/boundary),
    `task_000328_80fb4c9f` (extracted 300 frames vs expected 10 @1fps —
    count/interpretation), `task_001048_14335141` (165.7908 vs 161.8028,
    tol 0.001 — algorithm/interpretation).
- Signal: `reward=0`, `exit_reason=done`, low-to-moderate step counts
  (7/27/16/13/17), `domain ∈ {scientific_computing, data_science}`. Final
  pytest tails all show a single numeric/parse assertion failing while the
  file exists and is well-formed — the agent committed one interpretation
  and stopped.
- Verified (Read):
  - `task_000111` messages.json step 4–6: agent writes textbook-correct OLS
    C++, runs once, gets `2.5056,...`, and its self-verify turn only
    re-confirms *spec compliance* ("OLS formula – correct", "percentile
    method – correct") — it never recomputes the slope by a second method or
    questions the number. Committed at step 7.
  - `task_000117` final_pytest tail: `could not convert string to float:
    '5 -94.99'` — a fixed-width slice was one column off; a completeness /
    interpretation audit would have flagged the mis-sliced field.
  - `task_001937` final_pytest tail: `Expected Optimal Grid to be 50, but got
    60` — boundary/off-by-one the agent never re-examined.
  - `task_000328` final_pytest tail: `Expected around 10 frames (1 per second
    for a 10s video), got 300` — a units/count interpretation the agent never
    sanity-checked.
- Why Control not Instruction: the mechanism is a *missing verification step
  at the exit boundary*, not missing domain knowledge or a static prompt
  rule. The existing self-verify is already a Control processor that fires at
  exit intent; the narrow, correct fix is to enrich that same one-shot
  interception (fire-once, keepalive, +1 user message) — a system-prompt rule
  would apply on every turn (token cost, ignorable) and could not guarantee it
  lands at the decisive exit moment. No task-specific knowledge is injected;
  the added text is a general verification strategy.
- Why not a new tool (Action): the agent already has `Bash` and can run
  `python3`/`awk`/`numpy` for an independent recompute — no capability is
  missing, only the discipline to do it. TB2 exposes only `Bash` anyway.
- Retroactive check (A-corrective): partial-yes.
  - For `task_000117`, `task_001937`, `task_000328`, `task_001048`: yes — an
    independent recompute + completeness/interpretation audit at exit would
    surface the mismatch (wrong byte window, off-by-one, 300≠10 frames, algo
    divergence) and give the agent a concrete signal to fix before committing.
  - For the assigned `task_000111`: **no** — its C++ OLS is mathematically
    correct, so a second numpy computation returns the *same* 2.5056; the
    reference's 2.5997 is a data/spec-specific target the harness cannot
    reconstruct. Honest verdict: the assigned task is a data/model gap the
    harness cannot close, but the failure it exposes (no numeric
    cross-check at exit) is a real, recurring, harness-addressable cluster.
- expected_global_gain: flips a subset of the computed-value failure cluster
  (5 tasks observed this round across scientific_computing/data_science)
  where the wrong value is detectable by independent recompute or
  completeness/boundary audit; generalizes to any future task whose graded
  output is a scalar/count/statistic.
- regression_risk: low. Mechanism is byte-identical to the existing
  fire-once self-verify (contract check passes, 0 violations); only the
  message text changes. Longer message adds one-time tokens on the exit turn;
  no new turns. Non-computational tasks see the same first three checklist
  items as before plus benign extra guidance they can skip. Rollback trigger:
  if pass-rate on already-passing scientific_computing/data_science tasks
  (`task_000109`) drops or total cost rises materially, revert to the base
  `CustomSelfVerifyProcessor`.
- cost_shift: small positive per task that reaches exit (one longer injected
  message; possibly +1 short recompute Bash call on computational tasks).
  Bounded — fires at most once per task.
