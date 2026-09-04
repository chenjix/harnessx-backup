# Candidates — R2

## Context discovery (critical)

The tmax eval path (`recipe/tmax_eval/run_eval.py` → `agent_loop.run_agent`) is a
**self-contained agent loop that does NOT execute the harnessx processor pipeline**.
The only part of `config.yaml` it consumes is the system prompt, resolved by
`_resolve_system_prompt`: it reads `templates/*.j2` (or `system_prompt.txt`) sitting
next to the config, else falls back to the bare 5-line `agent_loop.SYSTEM_PROMPT`.

Implication: R1's `LoopDetectionProcessor` insertion was inert in this eval path
(the 29→30 delta is within the repeat-spread noise per `history/OVERVIEW.md`). The
only lever that actually changes agent behavior here is the **system prompt**
(Instruction lever). R1's config used `_StaticSystemPromptBuilder` (`_TB2_SYSTEM`),
but since that builder is never invoked by the eval loop and no `templates/` dir
existed, the agent ran with the bare default prompt — which contains zero guidance on
environment survey, planning, exact output paths, or pre-exit verification.

## Candidate C-002
[lens: failure | lever: instruction | intent: corrective]

Replace the bare default system prompt with a structured strategy template
(`templates/tb2_agent.j2`) encoding general terminal-task discipline: read the task
and note exact required output paths; survey the environment first; decompose and keep
a running plan; write outputs to the exact specified paths; and — before stopping —
explicitly verify every required deliverable exists at its exact path and its content
matches the task's stated format/values/constraints.

- Tasks affected (failing, same mechanism — output missing or unverified/off-spec at exit):
  - task_000748_c9807703 — sed dropped the leading `E`, wrote `0502` instead of `E0502`,
    declared done without checking output content against the requirement.
  - task_000933_1f27096a — tarball produced but missing the expected extracted binary;
    stopped without verifying contents.
  - task_001706_24462a09 — error_log written with 0 entries, expected 2; stopped unverified.
  - task_001090_c61c71f2 — process name `bash` not `monitor`, JSON shape wrong vs spec;
    stopped without validating against the stated output shape.
  - task_000118_3043e92d — peak log size 209MB exceeded the stated 45MB constraint; the
    stated numeric constraint was never checked.
  - (output-file-missing shape also present in 578/1031/264/010/015, several of which
    additionally ran long and hit a chat timeout.)
- Signal: `finished=no_tool_calls` with `final_pytest.passed=false`; assertion messages
  dominated by `does not exist` / `missing` / value-mismatch against a stated spec.
  Current prompt = 5-line `agent_loop.SYSTEM_PROMPT` with no survey/plan/verify guidance.
- Verified (Read):
  - task_000748 last two assistant turns: "The script is working correctly / complete"
    then summarizes that it strips `error[E` down to `0502` — never re-checks that the
    required literal `E0502` survived; verifier asserts `'E0502' in '0502\n'` → fail.
  - task_001090 final_pytest: `Process name is 'bash', expected 'monitor'` and JSON list
    vs dict mismatch — agent stopped without comparing output to the stated shape.
  - task_000933 final_pytest: `Extracted graph_math_double binary not found` — stopped
    without verifying tarball contents.
  - task_000578 / task_001031 final_pytest: required `results.json` never created at the
    exact path — no exit-time `ls` check of the deliverable.
- Why Instruction not Control: the eval loop does not run the processor pipeline at all,
  so a `MultiHookProcessor` (Control) would never fire on this benchmark — the ONLY
  behavioral lever exposed is the system prompt. A Control hook is structurally inert
  here; Instruction is the only viable mechanism. The guidance is general strategy
  (survey / plan / exact-path / verify-before-exit), not task-specific knowledge — it
  contains no task IDs, constants, or paths from the training set.
- Retroactive check (A-corrective): yes — for 748, an exit-time content check of the
  errors file against the required `E0502` literal surfaces the dropped character before
  stopping; for 933/1706/1090/118 a Definition-of-Done pass that inspects each deliverable
  and compares against the stated shape/constraint catches the defect while the agent is
  still able to fix it. These are precisely the tb2-playbook's top-rated levers
  (explicit plan + double-confirmation before exit).
- expected_global_gain: Targets the largest harness-addressable failing cluster
  (premature/unverified completion + wrong-path deliverables), spanning debugging,
  data_science, data_querying, data_processing, system_administration. Generalizes
  because "verify each stated deliverable before exit" applies to every task class.
- regression_risk: Low. All 30 R1-passing tasks already produce correct deliverables;
  a stronger prompt that asks for a survey + verification pass adds a few Bash calls but
  does not change a correct solution into an incorrect one. Main risk is extra token/step
  cost on already-passing short tasks — bounded by max_steps=80 and small verification
  commands. Rollback trigger: if pass_rate drops below R1 (30/50) or any currently-passing
  short task regresses to max_steps from added verification churn, revert to the bare prompt.
- cost_shift: Mild increase — a survey command up front plus 1–3 verification commands
  before exit per task. Offset partially by fewer wasted steps thrashing on wrong paths.
  Net expected: small positive token cost, justified by the flip potential.
