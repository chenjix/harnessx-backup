# Candidates — R4 c6

Assigned focus: `task_000396_e56917e2` (scientific_computing, reward=0,
exit_reason=done, no_tool_calls, 30 steps).

## Diagnosis of the assigned focus

Task: fix a build + step-size bug in a vendored RK45 library, run the sim,
then compute the **maximum absolute deviation** between `output.dat` and an
analytical `reference.dat`, and write that single value to
`/home/user/validation.log`. Verifier asserts `0.0 < max_dev < 0.1`.

The agent fixed the two obvious bugs (`-lm` link flag; step-size exponent
`-0.25 → 0.25`), ran the sim, computed **max_dev = 0.607**, wrote it, and
declared done. `final_pytest`: `max deviation 0.6072 is not within (0.0, 0.1)`.

Root chain (verified in body):
- The vendored integrator is a **forward-Euler** step (`y_rk4[0]=y[0]+dt*y[1]`,
  comment "using Euler for demonstration"), which is unconditionally unstable
  for an oscillator — it gains energy. Reference (analytical `cos t / -sin t`)
  stays on the unit circle (amplitude ≈ 1.0); the agent's output diverges to
  amplitude ≈ 1.64 by t≈10 (verified: ref t=9.94 → (-0.870, 0.493), amp 1.0;
  output t≈9.94 → (-1.447, 0.765), amp 1.64). The exponent-sign fix was
  necessary but NOT sufficient — a stable integrator (true RK4/RKF) is needed
  to reach dev < 0.1.
- The agent had the smoking gun in hand: its own computed **max_dev = 0.607**.
  For a task whose whole point is matching a reference, 0.6 is obviously huge.
- Instead of questioning it, the agent "verified" by spot-checking only the
  first 4 leading time steps (msg 493/501/521) — where the Euler error is still
  tiny — and by re-listing that files exist (msgs 549–583). It never compared
  the divergent **tail**, and never treated 0.607 as a red flag.
- The stock `CustomSelfVerifyProcessor` fired (msg 531) but its generic
  checklist ("re-read task / files exist / inspect contents / validate your
  test") let the agent rubber-stamp a glaringly wrong computed metric.

Whether the agent can author a stable integrator is a **model capability gap**
— NOT patched here. But "declared done on a self-computed validation metric
that is obviously out of range, after spot-checking only the easy leading
samples" is a **general verification-discipline gap** the harness can close.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Append a general "computed-result plausibility + full-range comparison"
verification discipline to the sidecar `system_prompt.txt`: when a deliverable
is a computed metric or a reproduction/comparison against a reference,
sanity-check the *magnitude* of the result against what a correct answer should
look like, compare across the FULL range (including the hardest / tail / edge
samples, not just the easy leading ones), and treat a large residual/deviation
or an out-of-plausible-range value as evidence the underlying computation is
still wrong — do not declare done on a red metric.

- Tasks affected (same mechanism — computed/output value glaringly wrong,
  agent declared done without recognizing it):
  - `task_000396_e56917e2` — max_dev=0.607 vs required <0.1; validated only the
    first 4 (easy) time steps; ignored the divergent tail.
  - `task_001653_c4cafa73` — ETL report emitted Centroid/Distance
    (18.6199) far from expected (11.3444); declared done.
  - `task_001937_ac874115` — reported "Optimal Grid: 60" where the correct
    optimization result was 50; declared done on a wrong computed value.
- Signal: `exit_reason=done`, `finished=no_tool_calls`, `reward=0`;
  `final_pytest` shows a numeric/computed-value mismatch that is a large,
  self-evident deviation from the correct value, not a subtle format nit.
- Verified (Read of task_000396 body):
  - msg 441/443: `compare.py` prints `0.6072399999999999`; msg 449 writes it to
    `validation.log` verbatim.
  - msg 495/501/521: agent "verifies" only t∈{0, 0.01, 0.0534, 0.1437} — the
    leading steps where Euler error is ~1e-3 — and calls it "reasonable".
  - msg 531: `_tb2_self_verify` fires; msgs 549–583: agent re-`ls`/`cat`s files
    and re-declares success without ever re-examining the 0.607 magnitude or the
    divergent tail (output amp 1.64 vs ref amp 1.0 at t≈10).
  - task_001653 final_pytest: `Centroid: 36.36…/Distance: 18.6199` !=
    `Centroid: 42.00…/Distance: 11.3444`. task_001937 final_pytest:
    `Expected Optimal Grid to be 50, but got 60`.
- Why Instruction not Control: the agent already runs the comparison and has the
  bad number in context — this is a caller-side discipline miss (it did not
  reason about the magnitude), not a tool-layer data gap. A processor could
  extend the self-verify checklist, but (a) the pending R2
  `h_output_contract_verify_v1` already owns a drop-in replacement of
  `CustomSelfVerifyProcessor` at `_order=90`/singleton `tb2_self_verify` for the
  *format/off-by-one* variant, and a second processor competing for the same
  exit hook risks collision, and (b) the discipline here (magnitude
  plausibility, full-range/tail comparison) is knowledge about *how to reason
  about a result*, which is exactly the Instruction lever's job. A prompt rule
  keeps the plausibility judgement agent-side and cannot mechanically regress a
  passing task.
- Why not Action: TB2 exposes only `Bash`; the agent already has every
  capability it needs (it ran `compare.py`). Nothing to add to the action space.
- Retroactive check (A-corrective): yes — if the agent had held the rule "a
  large computed deviation / out-of-range metric means the computation is still
  wrong; compare the full range before finishing", task_000396's 0.607 and the
  divergent tail would have flagged the integrator as still broken instead of
  being rubber-stamped; task_001937's grid=60 and task_001653's distance=18.6
  are likewise self-evidently-off computed values the same discipline surfaces.
  (Residual risk: for 000396 the agent must then actually implement a stable
  integrator — a capability gap — so the flip is not guaranteed, but the
  discipline converts a silent wrong-answer finish into a genuine
  "not-yet-correct" signal and is a fair-run improvement for the whole cluster.)

- expected_global_gain: Relieve the "declared done on a glaringly wrong
  self-computed value" cluster (>=3 tasks across scientific_computing +
  data_science) by making magnitude-plausibility and full-range comparison an
  explicit pre-exit discipline; generalizes to any reproduce/validate/optimize
  task.
- regression_risk: Low — prompt-only append to the minimal R0 sidecar; removes
  no capability, changes no processor. Worst case a few extra comparison Bash
  calls on already-passing compute tasks (e.g. 000338, 001088 already extract
  and compute cleanly, so the rule only reinforces their habit). Append-only
  guidance cannot itself remove a mechanism.
- cost_shift: Small positive — a handful of extra tail/edge comparison Bash
  calls on compute tasks; likely net-neutral-to-favourable by converting silent
  wrong-answer finishes into corrected (or at least honestly-flagged) work.
- rollback_trigger: If next round is flat/down AND task_000396/001653/001937
  stay F on the same value mismatches (residual is pure model
  numerical-capability), OR any previously-passing compute task (000338, 001088,
  and the passing data_science cluster 000578/000760/001498/001697) regresses to
  F — revert to the R0 minimal sidecar prompt.
