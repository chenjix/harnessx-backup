# Candidates — R4/c1

Assigned focus: `task_000011_d089ef35` (scientific_computing) — FAILS reward=0,
exit=done at 24 steps. Diagnosed below; it is a strong, clean member of the
recurring "done-but-numerically-wrong after a circular self-verify" cluster.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Augment the once-per-task self-verify checklist (CustomSelfVerifyProcessor,
`_order=90`) with one extra item that demands an **independent re-derivation
from the task specification** of any deterministically-computed result, injected
directly into the checklist message the model reads at exit.

- Tasks affected (same mechanism, all `exit=done reward=0`, deterministic wrong
  number after a superficial self-verify):
  - task_000011_d089ef35 (assigned focus) — mesh MSE socket server
  - task_000111_cbada64a — C++ OLS slope
  - task_001653_c4cafa73 — centroid / distance
  - task_001937_ac874115 — optimal grid size
  - task_001035_26564093 — DP / primer optimisation
- Signal: `exit_reason=done`, `initial_pytest.passed=true`, `final_pytest`
  fails a single deterministic numeric assertion; the agent's final turns are a
  requirements/format checklist or a re-derivation that reuses its own buggy
  implementation. No `budget_exceeded` / `error` / `loop_detected` — the agent
  believed it was done.
- Verified (Read, task_000011 body):
  - Step ~14: agent finds one indexing bug (`start_col` should be
    `(quadrant%2)*2`) and rewrites, but leaves the *symmetric* row bug
    `start_row = quadrant/2` (must be `(quadrant/2)*2`), so quadrants 2 & 3 read
    rows 1-2 instead of 2-3.
  - Step (server test): server returns Q2=38.25, Q3=55.25.
  - Step (self-verify body): agent "verifies" by hand-deriving the expected MSE
    **from its own wrong refined grid** and writes `38.25 ✓`, `55.25 ✓` — a
    circular check that rubber-stamps the bug. It never derived the expected MSE
    independently from the spec's literal quadrant definition (rows 2-3).
  - final_pytest: `Expected '68.25' for quadrant 2 ... got '38.25'`,
    `Expected '93.25' ... got '55.25'`. Q0/Q1 pass.
  - task_000111: final_pytest `abs((2.5056 - 2.5997))`; body: verification =
    "the values make sense ... in the format". task_001653/001937/001035:
    single deterministic numeric-assertion failures, `exit=done`.
- Why Control not Instruction: the R1 sibling `h_numeric_crosscheck_v1`
  (instruction lever, system-prompt section at task start) is `pending` and the
  task_000011 body shows why a task-start rule is weak — the agent *did* self-
  verify at exit, but the guidance had decayed and its check was circular. The
  decisive moment is the exit-time self-verify checkpoint; a Control processor
  that injects at exactly that message targets the failure temporally. A prompt
  rule cannot pick the moment; a mechanical post-hook is the right lever.
- Why not identical to the pending sibling `h_compute_crosscheck_selfverify_v1`
  (R2/c4, control): (1) my `current_config` is R1, which contains NEITHER
  crosscheck processor — this cluster has zero fix in the lineage I evolve.
  (2) Different injection point: R2/c4 appends to the synthetic ACK *tool-result*
  string ("Verification check initiated…"), a low-salience location; I mutate the
  *checklist user message itself*, where the numbered items the model actually
  reads live. (3) Different arming: R2/c4 gates on compute keywords (a
  service/socket task like task_000011 is not obviously a "compute" task); mine
  fires universally but the injected item is *self-scoping* ("if the result is
  fully determined by the task description…"), so it is a model-judged no-op on
  non-deterministic tasks — closing the arming-miss risk that keyword gating has.
- Retroactive check (A-corrective): yes — for the deterministic cases an
  independent derivation straight from the spec necessarily disagrees with the
  buggy value. task_000011: deriving MSE from the literal quadrant-2 definition
  (rows 2-3 → 9,10,13,14) gives 68.25, contradicting the agent's 38.25 and
  forcing it to find the row-index bug before exiting. task_000111 (OLS) and
  task_001653 (centroid) are deterministic recomputes that would disagree.
  Weaker but non-harmful on same-mental-model bugs (the item explicitly says a
  rerun of the same code is not independent and to hand-work a small case).
- expected_global_gain: flips members of the deterministic-compute cluster
  (>=2 strong: task_000011, task_000111; up to 5) whose only blocker is a
  circular self-verify — a single generalizable discipline, zero task literals.
- regression_risk: Low. Net +0 messages (mutates the trailing checklist message
  content), fires <=1x/task, contract-clean (validated: violations=0). The item
  is self-scoping, so on non-deterministic / already-correct tasks it is a
  model-judged no-op. Main residual risk: 1-2 extra verification tool calls on a
  near-budget compute task; the cited passing compute tasks had step headroom.
- cost_shift: +0 to a few short verification tool calls on deterministic-compute
  tasks that reach exit; exactly zero token cost on the ~45 non-compute tasks
  beyond one appended checklist item. Net positive if it recovers >=1 pass.
- rollback_trigger: next-round compute-cluster pass-rate flat/down, OR a
  previously-passing task regresses to budget_exceeded/max_steps attributable to
  the added verification steps, OR replay fails on the processor.
