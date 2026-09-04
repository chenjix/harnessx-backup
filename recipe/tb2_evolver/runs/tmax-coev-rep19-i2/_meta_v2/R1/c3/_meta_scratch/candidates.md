# Candidates — Tmax coev rep19-i2 R1 c3

Assigned focus: `task_000111_cbada64a` (scientific_computing) fails.

## Diagnosis of the assigned task

`task_000111` asks for OLS slope/intercept + a seeded bootstrap CI on squared
residuals, written to `/home/user/result.txt`. The agent wrote structurally
correct C++, it compiled and ran in 7 steps / 16.9s, produced
`2.5056,1.2262,3.9742,6.2925`, and exited (`no_tool_calls`). The verifier
expected `m≈2.5997`; the agent got `2.5056` (Δ≈0.094). OLS is deterministic
given the data, so the wrong slope means the agent's *interpretation* of the
data/method (parsing / index / formula) differed from the reference — not a
harness crash, truncation, or tool error.

Root-cause classification: this specific miss is largely a **model
capability / interpretation gap** — re-running the same code reproduces the
same wrong number, so a naive "re-check your output" nudge would NOT flip it.
BUT the trajectory exposes a **generalizable harness deficiency in the
verification turn**: the built-in `CustomSelfVerifyProcessor` fires on
exit-intent, yet its checklist is oriented to *file existence + output format*.
In every cited trajectory the self-verify turn degenerated into re-`cat`-ing
the file — it never prompted the agent to re-audit *interpretation* choices
(draw order, index/axis convention, seed usage, parsing edge cases), which is
exactly where these numeric answers went wrong.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `NumericResultAuditProcessor` that augments the existing one-shot
`_tb2_self_verify` turn with a computational-interpretation audit directive,
firing only when the session produced a numeric-answer signature.

- Tasks affected (same mechanism — close-but-wrong numeric, fast exit,
  self-verify only checked format/existence):
  - `task_000111_cbada64a` — OLS/bootstrap; m=2.5056 vs 2.5997; 7 steps, 16.9s.
  - `task_001330_f5aff1f5` — Monte-Carlo trajectory fit; m=0.048 vs 0.050;
    7 steps, 28.9s.
  - `task_001937_ac874115` — grid optimisation; reported 60 vs expected 50;
    15 steps.
- Signal: `finished=no_tool_calls`, `exit_reason=done`, low `steps`,
  `reward=0` with verifier assertion `abs(got-expected)` just over threshold.
  Body shows the self-verify turn (`_tb2_self_verify`) followed only by
  `cat result.txt` + format re-confirmation.
- Verified (Read):
  - `task_000111` msgs[87-128]: after `_tb2_self_verify` fires, the agent's
    only follow-up action is `ls -lh ... result.txt` + narrating format
    correctness; no re-derivation, no spec re-read. Wrong slope committed.
  - `task_001330` msgs[86-129]: after `_tb2_self_verify` fires, the agent
    runs `cat trajectory_fit.txt`, re-lists the requirement bullets, and
    declares "Both values are rounded to 3 decimal places" — never questions
    the RNG draw order that produced m=0.048 vs the intended 0.050.
- Why Control not Instruction: the benchmark's self-verify prompt lives in a
  read-only module (`benchmarks/terminal_bench_2/harness.py`,
  `_SELF_VERIFY_MSG`) and I cannot edit it; the SystemPromptBuilder is a
  static sibling file, and a global system-prompt rule would fire on every
  turn of every task (nagging + cost on non-numeric passing clusters). A
  Control hook lets me (a) piggyback on the *existing* one-shot verification
  turn (no extra message injected — contract-safe augmentation of
  `event.result`), and (b) gate the directive on a value-agnostic
  numeric-result signature so it stays silent on non-numeric tasks.
- Why Control not Action: no new agent capability is needed — Bash already
  lets the agent re-derive/cross-check; the gap is *knowing to* at exit time.
- Retroactive check (A-corrective): partial-yes. For `task_001937` and the
  broader "misread an ambiguous spec point" subset, re-reading the method and
  trying the alternative interpretation at exit time can flip the answer
  (the directive explicitly says "try the alternative reading"). For
  `task_000111`/`task_001330` the misread is subtle and the flip is not
  guaranteed — this is the honest weak point. The directive is scoped as a
  strategy nudge, not a guaranteed fix; it raises the probability of catching
  an interpretation error without embedding any task-specific answer.
- expected_global_gain: targets the scientific_computing / data_science
  numeric cluster (only 1/5 sci-comp passed in r0). Even a modest hit-rate on
  interpretation-error catches is net-positive because the intervention is
  free on the tasks it does not fire on.
- regression_risk: LOW. Fires at most once, only on the already-existing
  self-verify turn, only when a numeric-result signature was seen, and only
  augments a tool result (never injects a message, never touches the system
  prompt). Non-numeric passing tasks (file_ops, most sysadmin/security) see
  nothing. Worst plausible harm: a numeric task that was already correct
  spends a few extra Bash calls re-checking — bounded, not a correctness risk.
- cost_shift: small positive on numeric tasks (a few extra verification Bash
  turns); zero on non-numeric tasks. No change to per-call token cap.
- Rollback trigger: if the next round shows the numeric cluster's pass-rate
  flat or lower AND numeric tasks' mean step-count up materially, revert —
  the directive is adding cost without catching interpretation errors.
