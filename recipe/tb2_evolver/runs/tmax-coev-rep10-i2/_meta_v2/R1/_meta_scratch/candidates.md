# Candidates — R1 (tmax-coev-rep10-i2)

Baseline R0: 21/50 (42%). Dominant failure shape: **23 of 29 failures
exited with `exit_reason=done`** — the agent believed it was finished.
Only 4 were `budget_exceeded`, 2 `error`. Most failing pytest tails read
"1 failed, N passed": the agent got most of the task right but shipped a
semantic/functional bug (wrong constant, daemon that doesn't keep the
invariant, query using wrong index) that a real end-to-end run would have
caught. The existing single-shot `CustomSelfVerifyProcessor` fires once;
the agent answers with a **shallow static re-check** (ls/grep/cat) and
immediately re-exits.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Rewrite `system_prompt.txt` to make verification *functional, not
cosmetic*: survey → restate every concrete checkable requirement (exact
paths, constants, thresholds, formats, runtime behavior) → implement to
those values → before stopping, RUN the solution end-to-end against the
real scenario and compare observed behavior value-by-value, recomputing
required constants independently.

- Tasks affected (failing, same mechanism — shallow verify then wrong
  value/behavior): task_000059_e4b842f5, task_000118_3043e92d,
  task_000264_ab8c7253, task_000115_40adb445, task_000396_e56917e2.
- Signal: `exit_reason=done` on 23/29 failures; final_pytest tails almost
  all "1 failed, N passed"; self-verify fired (2 refs) yet the post-nudge
  turn used only ls/grep/cat.
- Verified (Read):
  - task_000059 final turn: agent "verified" by `ls -lh final_mac.txt &&
    cat final_mac.txt` + `cat verify_mac.py` — never independently
    recomputed the MAC; verifier failed `test_verify_mac_py_python3_compatible`.
  - task_000118 final turn: agent "verified" by `ls -lh
    deployment_monitor.py` and `grep -n THRESHOLD_BYTES/SIGSTOP` — never
    ran `run_deployment.sh` to confirm the monitor keeps disk under quota;
    verifier failed `test_deployment_monitor`.
- Why Instruction not Control: this is a *knowledge/discipline* gap — the
  agent has the Bash capability to run its solution but does not know that
  static checks don't count as verification. C-002 supplies the mechanical
  push; C-001 supplies the general strategy so the agent knows *what* a
  real test looks like across task classes. Instruction is general and
  carries no task literals.
- Retroactive check (A-corrective): yes — if the agent had actually run
  its solution against the real scenario (recomputed the MAC; run the
  deployment sim), the wrong value / non-working monitor would have been
  visible before exit, giving it a chance to fix.
- expected_global_gain: targets the largest failing cluster (`done` +
  "1 failed"); plausibly flips several near-miss tasks across domains.
- regression_risk: low — general strategy text; no removal of prior
  guidance. Slight risk of extra exploration cost.
- cost_shift: +modest tokens per task (a few extra functional-test Bash
  calls). Bounded by step budget.

## Candidate C-002
[lens: failure | lever: control | intent: corrective]

Replace the single-shot `CustomSelfVerifyProcessor` with a stateful,
functional-aware exit gate (`FunctionalVerifyGateProcessor`, `_hook_='*'`,
same `_singleton_group` slot). On exit intent it nudges to verify; it then
tracks whether the agent runs a *substantive* command (executes/exercises
the solution) vs. purely read-only inspection (ls/cat/grep/head/find/…). If
the agent tries to exit again having only inspected, it re-nudges once more
with an escalated functional-test demand. Bounded by `max_nudges=2` so a
genuinely-done or genuinely-stuck task always terminates.

- Tasks affected (failing, exited done after shallow self-check):
  task_000059_e4b842f5, task_000118_3043e92d, task_000115_40adb445,
  task_000264_ab8c7253, task_000396_e56917e2, task_001937_ac874115.
- Signal: `_tb2_self_verify` appears exactly twice (1 call + 1 ack) in
  ~all failing done-tasks; the turn after the ack contains only read-only
  Bash (ls/grep/cat), then the agent re-exits.
- Verified (Read): task_000059 — post-ack turn is `ls … && cat …`, `cat
  Makefile`, `cat verify_mac.py`, then exit (all read-only). task_000118 —
  post-ack turn is `ls -lh …` then `grep -n THRESHOLD_BYTES/SIGSTOP`, then
  exit (all read-only). Under the old single-shot gate the agent is never
  pushed to actually execute the solution.
- Why Control not Instruction: the existing gate's *content* already tells
  the agent to test functionally (step 4 of the old checklist), yet the
  agent short-circuits because the gate fires only once and accepts any
  next turn. The missing piece is a *mechanical* re-fire keyed on "did the
  agent actually run anything" — a cross-task guard that a prompt rule
  cannot enforce on its own. C-001 handles the knowledge; C-002 handles
  the enforcement. Independent: each helps on its own.
- Retroactive check (A-corrective): yes — the re-nudge fires precisely on
  the observed shallow-exit turns; forcing one real run surfaces the
  functional bug before the verifier phase, matching the tasks' "1 failed"
  near-misses.
- expected_global_gain: same large `done`/"1 failed" cluster as C-001,
  via enforcement rather than advice; complements C-001.
- regression_risk: bounded. Worst case = up to 2 extra nudge cycles on
  tasks that were already correct → a few extra Bash calls, never an
  infinite loop (hard cap). Read-only heuristic could misclassify a
  legitimately-inspection-only final turn as shallow, costing one extra
  nudge — still bounded and harmless. No currently-passing task depends on
  exiting after a shallow check.
- cost_shift: +bounded tokens (≤2 extra verify cycles/task); expected net
  positive if it flips even 2–3 near-miss tasks.
