# Candidates — R7

## Candidate C-001
[lens: success | lever: instruction | intent: preservative-lock]

Restore the R3 accepted enriched `system_prompt.txt` (survey → restate
requirements → decompose → functional-verify → loop-escape) alongside the
incumbent config, which the R6 no-op silently dropped.

- Tasks affected (habit fired in accepted incumbent, R3 best draw 26/50):
  passing cluster the enriched prompt was validated to protect —
  task_000090_d0fa254a, task_000257_3cd35e11, task_000533_01bba2c2,
  task_001883_6e356e21 (the R3 gate credited gained=090,257,533,1883 vs the
  pre-instruction incumbent). These are exactly the tasks the survey/restate/
  decompose/functional-verify content was accepted for.
- Signal: config.yaml uses
  `recipe.tmax_eval.prompt_builder.SiblingSystemPromptBuilder`, which resolves
  `system_prompt.txt` next to the active config YAML. The R6 output dir
  contains only the 5-line DEFAULT prompt (md5 914fb4a8, byte-identical to
  R0) — the 51-line R3 enriched prompt (md5 ba441c35) that the accepted R3
  incumbent depended on was NOT co-located. So the r6-traj (21/50) ran with
  the default prompt, NOT the incumbent's prompt.
- Verified (Read + md5):
  - R3/system_prompt.txt = md5 ba441c35 (51 lines: survey/restate/decompose/
    loop-escape/functional-verify). This is the content the R3 journal entry
    (accepted, 26/50) documents as C-001 for R3 and C-001/C-002 for R1.
  - R6/system_prompt.txt = md5 914fb4a8 (5 lines, identical to R0 default) —
    the enriched content is absent from the round the r6-traj was measured on.
  - config.yaml (R3==R6, md5 6cc7bf8a) references the sibling builder, so the
    prompt file MUST be co-located to take effect. It was not in R6.
  - r6-traj measured 21/50 vs R3 best 26/50; part of that gap is plausibly the
    missing prompt, not pure sampling variance.
- Why Instruction not Control: the content is a strategy the agent applies
  agent-side (how to survey/decompose/verify), already validated and accepted
  as an Instruction change in R1 and R3. A Control hook would mechanically
  inject or enforce it, bypassing the agent's own scoping that made the R3
  cluster short and correct. This candidate does not author anything new — it
  restores the exact accepted artifact to the location the config's builder
  reads from.
- Retroactive check (B-preservative-lock): yes — if this enriched prompt were
  REMOVED (which is exactly what happened in R6), the R3-credited passing
  cluster (090/257/533/1883) loses the survey/restate/functional-verify
  scaffolding it was accepted for, and the round drops toward the R0 default-
  prompt baseline (21/50). Restoring it protects those flips.
- expected_global_gain: Restore the accepted R3 incumbent's true behavior
  (best 26/50) that the no-op accidentally reverted to default-prompt (21/50).
  Generalizes across domains because the content is domain-agnostic strategy
  (survey/decompose/functional-verify), validated as accepted twice.
- regression_risk: Near-zero. This is the byte-identical accepted R3 artifact
  restored to its intended location. The only "risk" is that the r6-traj 21/50
  was pure variance and the prompt makes no difference — in which case this is
  a neutral restore of a validated incumbent, not a harmful change. No new
  code, no processor surface added.
- cost_shift: Small positive prompt tokens/task (+~46 lines of system prompt),
  offset by fewer thrash steps on multi-step tasks (the decompose + loop-escape
  content was accepted partly for cutting budget_exceeded thrash). Net near-
  neutral to slightly negative on cost.
- rollback_trigger: If R8 measures at/below the default-prompt baseline (~21)
  AND median steps/cost rise, the enriched prompt is not carrying its weight on
  this model — revert to the default 5-line prompt.
