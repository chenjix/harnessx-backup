# Candidates — R2 / c3

Assigned focus: `task_000111_cbada64a` (scientific_computing) fails.

## Diagnosis

`task_000111` asks for OLS slope/intercept + bootstrap CI, written to
`result.txt` as `m,c,ci_lower,ci_upper`. The agent wrote structurally-correct
OLS+bootstrap C++, compiled/ran in 7 steps, committed `m=2.5056`; verifier
expected `m≈2.5997` (`abs(2.5056-2.5997)=0.094 > 0.001`). OLS is deterministic
in the data, so this is an *interpretation* miss (parsing / index / estimator /
formula variant), not a crash. The same shape recurs across the sci-comp
cluster:

- `task_001330_f5aff1f5`: Monte-Carlo fit committed `m=0.048` vs expected
  `0.050` (verifier: "Expected m=0.050, but found m=0.048").
- `task_001937_ac874115`: grid optimisation reported 60 vs expected 50.
- `task_001035_26564093`: primer optimisation committed `GCGC` vs `GCAT`.

Verified failure MECHANISM (bodies read): in every case the agent's
`_tb2_self_verify` turn only re-`cat`'d the output and re-confirmed format — it
never recomputed the answer independently. In the one passing analogue
`task_000011_d089ef35` the agent *noticed* a discrepancy between its hand-calc
(16.625) and its program (17.25) during self-verify and then explicitly talked
itself out of investigating ("let me trust the server output... my manual
calculation was wrong") — it lacked a forcing function to reconcile the
disagreement before committing.

Baseline note: my `current_config` (R1/config.yaml) does NOT contain any
numeric self-verify augmentation — the R1 `numeric_result_audit` mechanism
lives only in a sibling R1 candidate config, not in the baseline handed to
this proposal. So this cluster is currently *unprotected* in my baseline.

Honest scope: getting the exact deterministic reference value (2.5997) may
require model interpretation the harness cannot guarantee. The gate raises the
probability of a catch by forcing the missing *action* (independent
recompute + reconcile), which is bounded-cost and free on non-numeric tasks.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `NumericRecomputeGate` `MultiHookProcessor` that, on the one-shot
`_tb2_self_verify` turn of tasks that emitted a numeric-answer signature,
appends ONE directive forcing an *independent second computation* and
reconciliation of any disagreement before the agent commits.

- Tasks affected: task_000111_cbada64a, task_001330_f5aff1f5,
  task_001937_ac874115, task_001035_26564093 (all sci-comp/data-science
  close-but-wrong numeric commits; same mechanism — commit without an
  independent cross-check).
- Signal: `reward=0`, `finished=no_tool_calls`, low step count (7-19); verifier
  `final_pytest` shows a small numeric delta (`abs(2.5056-2.5997) > 0.001`;
  `'0.048' != '0.050'`; `60` vs `50`; `GCGC` vs `GCAT`).
- Verified (Read):
  - task_000111 msgs 87-128: after `_tb2_self_verify`, agent runs only
    `ls -lh ...` + `cat result.txt` and narrates format correctness — no
    re-derivation. Committed `2.5056,1.2262,3.9742,6.2925`.
  - task_001330 result.json verifier hint "Ensure you used numpy.random.seed(42)
    ..."; agent self-verify only re-reads the fit file, never questions RNG
    draw order producing 0.048.
  - task_001937 verifier "Expected Optimal Grid to be 50, but got 60".
  - task_000011 (passing analogue) msgs 344-452: agent *detects* hand-calc vs
    program disagreement during self-verify, then commits anyway ("let me
    trust the server output"). Demonstrates the missing forcing function.
- Why Control not Instruction: the gap is not knowledge — the agent already
  knows how to compute; the R1 audit already tried a "re-read the spec"
  Instruction-style nudge. The missing thing is a *mechanical, uniformly-firing
  hook* bound to the exit-intent turn that (a) only triggers on a value-agnostic
  numeric signature and (b) demands a concrete cross-check *action*. A static
  prompt rule fires on every task (nagging the passing non-numeric clusters)
  and cannot be gated on the runtime numeric signature. A Control hook on the
  synthetic self-verify tool result gives per-run gating for free.
- Why Control not Action: the agent already has `Bash`, its only tool, and can
  run an independent recompute with it; no new action-space is missing. The gap
  is that it doesn't *do* the cross-check, so the fix is a directive on the
  verify turn, not a new tool.
- Retroactive check (A-corrective): partial-yes. If forced to recompute
  independently at the self-verify turn, task_000111 / task_001330 raise the
  odds of surfacing the interpretation delta (strongest on task_001937-style
  "alternative reading" and task_000011-style "already saw the disagreement"
  cases). It is a probability lift, not a guarantee — re-derivation may
  reproduce the same misread if the agent repeats the same interpretation. The
  downside is bounded: silent on non-numeric tasks, fires once, augments a tool
  result only.
- expected_global_gain: raises catch-rate on the sci-comp/data-science
  close-but-wrong-numeric cluster (4/5 failing in r0) by adding the *action*
  step (independent recompute) the whole cluster skipped. Generalises to any
  small-numeric-deliverable task.
- regression_risk: numeric tasks that were already correct spend a few extra
  verification Bash turns; non-numeric tasks untouched (processor stays silent
  — `_looks_numeric` gates on value-agnostic result shape). No correctness
  risk — it never injects a message, only augments `event.result`; the agent
  can still confirm-and-commit if the two computations agree.
- cost_shift: small `+` on numeric tasks (one extra independent recompute +
  compare, a few Bash calls); ~0 on non-numeric tasks; per-call token cap
  unchanged (harness_runner max_tokens).
- rollback_trigger: if next round shows the sci-comp/data-science numeric
  cluster pass-rate flat/down AND numeric tasks' mean step-count materially up
  (cost without catches), revert.

## Distinctness from R1 (`h_numeric_audit_v1`)

R1's accepted hypothesis appended a *"re-read the spec / audit interpretation"*
advisory to the self-verify turn (an Instruction-flavoured nudge). This
candidate is a distinct mechanism at the same lever family: it forces a
concrete **independent recomputation and reconciliation action** — the step
every cited trajectory (including the passing task_000011 that *saw* the
disagreement) actually skipped. Different hypothesis_id, different payload
(action-forcing vs advice), and it is applied to a baseline that currently
lacks any numeric self-verify augmentation.
