# Candidates — R2

## Mechanism note (decisive)

The tmax eval loop (`recipe/tmax_eval/run_eval.py` + `agent_loop.py`) does
**not** execute the `config.yaml` processor pipeline. It only consumes the
**system prompt**, resolved by `_resolve_system_prompt` in this order:
`--system-prompt-file` → `system_prompt.txt` sibling of the harness config
→ first `templates/*.j2` under the config dir → agent_loop default.

Implication: R1's loop-detection *processor* change was inert. R1's
`system_prompt.txt` was byte-identical to the built-in default, so R0≈R1
behaviorally; the observed +3/-4 flip (30→29) is run-to-run noise from the
stochastic Qwen3.5-9B (temperature 0 but non-deterministic server), not a
real regression. The **only** live lever on this benchmark is the system
prompt. This round pulls that lever.

## Candidate C-002
[lens: failure | lever: instruction | intent: corrective]

Replace the default system prompt (which *encourages* early stopping) with
one that instills a generalizable three-phase discipline: (1) survey the
environment and enumerate the task's explicit requirements before acting,
(2) satisfy each requirement, (3) verify every stated requirement against
actual filesystem/process state before stopping — matching required output
paths, filenames, and exact string content literally.

- Tasks affected (corrective, ≥2 distinct, same mechanism — premature stop
  / insufficient verification against explicit requirements):
  - `task_000748_c9807703`: wrote `0502` to rust_errors.txt; requirement was
    the full code `E0502`. `finished=no_tool_calls`, 18 steps.
  - `task_001090_c61c71f2`: monitor process ended up named `bash`, spec
    required process name `monitor`. `finished=no_tool_calls`, 32 steps.
  - `task_000933_1f27096a`: build artifact / binary at wrong path
    (`bin/graph_math_double` not found where verifier looked).
    `finished=no_tool_calls`, 8 steps.
  - `task_000015_89886d8d`: required file `/home/user/test_parser.py` never
    created. `finished=no_tool_calls`, 78 steps.
  - Supporting (same shape, not all counted): `task_000028_7fe033ac`
    (task left incomplete — `requests` path), `task_000118_3043e92d`
    (log-dir size threshold not actually met), `task_000140_01c78b42`
    (lingering vm_service processes not cleaned), `task_001937_ac874115`
    (reported Optimal Grid 60, expected 50), `task_001781_529727cf`
    (deadlock-fix pattern absent).
- Signal: `agent.finished=no_tool_calls` on 9/21 R1 failures; verifier
  `final_pytest` fails on an *explicit, checkable* requirement (exact string,
  exact path, exact process name) the agent never re-verified. Passing
  cluster (29 tasks) all also stop at `no_tool_calls` but median 13 steps,
  max 64 → ample headroom for a verify pass (max_steps=80).
- Verified (Read, body-quoted):
  - `task_000748` final assistant msg: "Uses sed to extract just the numeric
    code (e.g., '0502' from 'E0502')" — the agent narrates dropping the `E`,
    never re-reads the requirement, then stops. Verifier: `assert 'E0502' in
    '0502\n'` fails.
  - `task_001090` final assistant msg: "The task is complete. Let me
    summarize..." — declares done; verifier finds process `comm=='bash'`
    not `'monitor'`.
  - `task_000933` final assistant msg: "All requirements have been completed
    successfully" — verifier: binary path `bin/graph_math_double` missing.
- Why Instruction not Control: the tmax loop ignores the processor pipeline
  entirely (see mechanism note), so a Control processor is literally inert on
  this benchmark — the system prompt is the only executable surface. Even
  setting the mechanism aside, the fix is *when/whether to keep going*, a
  decision the agent must make against the task text; a mechanical hook can't
  know each task's requirement set to enforce it.
- Why Instruction not Action: the agent already has full capability (Bash can
  read files, grep content, check processes). Nothing is un-doable — the gap
  is discipline, not action space.
- Retroactive check (A-corrective): yes — if the agent had, before stopping,
  re-read each requirement and diffed actual state against it, task_000748
  would have caught `0502 != E0502`, task_001090 the process name, task_000933
  the missing binary path. The blocker is confident early-stop, and the fix
  targets exactly that decision point.
- expected_global_gain: Flips part of the 9-task `no_tool_calls` premature-
  stop cluster by forcing literal re-verification of explicit requirements
  before exit; generalizes to any task with checkable outputs (the whole
  benchmark), not one task's domain.
- regression_risk: A verify pass adds a few steps. All 29 passing tasks
  already stop at `no_tool_calls` with median 13 / max 64 steps vs the 80
  cap, so added verification is very unlikely to push a passing task to
  max_steps. Verification cannot turn a correct solution incorrect. Residual
  risk: prompt drift causing the model to over-explore; mitigated by keeping
  the prompt concise and strategy-level (no task literals).
- cost_shift: +1 to ~6 extra Bash steps per task for the verify pass;
  modest token increase, bounded by max_steps=80. Net positive if it flips
  ≥2 tasks.
