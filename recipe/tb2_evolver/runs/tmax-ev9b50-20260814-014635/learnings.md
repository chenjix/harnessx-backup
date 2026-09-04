# Terminal-Bench 2 — Evolution Journal

## Round 1 — add loop detection

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-14T02:00:00Z
hypothesis_id: h_loop_detect_v1
levers: [control]
predicted_affected: [task_000578_cebe85a5, task_001321_658ce4a8, task_000958_4bb2b05d, task_001031_a8f0eb37, task_001653_c4cafa73]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=30/50; +5/-4 gained=task_000684_1a33ef37,task_000965_069bb95f,task_001032_1adaccb9,task_001653_c4cafa73 lost=task_000748_c9807703,task_001090_c61c71f2,task_001701_95e3bbcb,task_001706_24462a09; score 0.6000 >= incumbent(mean) 0.5800 - tol 0.0400
expected_global_gain: "Closes a 5-task identical-consecutive-call loop cluster spanning 4 domains; 2 of them are hard exit_reason=error (HTTP 400) crashes, so removing the failure mode is structurally valuable beyond raw pass count."
regression_risk: "Near-zero. Every R0 passing task has max consecutive identical Bash call = 1; the raise threshold of 5 (warn at 3) is never approached by legitimate work. name-only strategy disabled (name_warn_threshold=9999) so single-tool TB2 does not get spammed."
cost_shift: "Net negative (savings). Loops that ran 65-158 messages / all 80 steps get cut at ~5-8 repeats; no added cost on non-looping tasks."
rollback_trigger: "If any R0-passing task regresses to loop_detected/early exit, or pass_rate drops, revert. Also revert if the 5 predicted tasks still fail with the SAME loop shape (indicates the model re-loops after the nudge and the harness lever is insufficient)."
-->

### Why

R0 scored 29/50. Among the 21 failures, a distinct mechanical cluster
of 5 tasks share a shape: the agent issues **byte-identical consecutive
Bash tool calls** dozens of times in a row. On task_001321 the same call
repeated 55 consecutive times (65 total); on task_000958 the same SQL
query repeated 62 times until max_steps; task_000578 repeated a Go-file
heredoc 37 times until the context overflowed into an HTTP 400 crash.
The agent even emits self-aware text ("Let me try a different approach")
yet keeps issuing the identical call — a mechanical repeat the model
cannot self-interrupt. No repetition-loop detector exists in the current
pipeline; `CustomEditToolProcessor` only counts file-write commands, so
non-write loops (query/run) slip through.

### Changes

- `config.yaml` — insert built-in
  `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
  after `CompactionProcessor`, before `ParseRetryProcessor`.
  Params: window_size=12, warn_threshold=3, threshold=5,
  name_warn_threshold=9999 (name-only strategy disabled for single-tool
  TB2), compaction_drop_threshold=5. No new code authored — reuse of a
  built-in processor.

### Evidence

- `task_001321_658ce4a8` messages.json: Bash call
  `"/home/user/extractor < /home/user/raw_dump.txt | head -20"` repeated
  55 consecutive times; ends exit_reason=error (HTTP 400).
- `task_000578_cebe85a5`: `cat > .../main.go << 'EOF' ...` repeated 37
  consecutive times (78 total) → HTTP 400.
- `task_000958_4bb2b05d`: SQL-query Bash call repeated 62 consecutive
  times → max_steps (80).
- `task_001031_a8f0eb37`: `python3 << 'EOF' from mpi4py ...` repeated 59
  consecutive times → max_steps.
- `task_001653_c4cafa73`: `cat > /home/user/etl.c << 'EOF' ...` repeated
  26 consecutive times.
- Regression baseline: measured all 29 R0 passing tasks — max consecutive
  identical Bash call = 1 across every one. Thresholds are never touched.

### Uncertainty

The warn-at-3 nudge may not break the model out of a loop it is
mechanically committed to; in that case raise-at-5 still converts the
crash/step-burn into a clean `loop_detected` exit (protects the replay
gate and reclaims steps) but does not flip the task to pass. If the 5
predicted tasks still fail with the same loop shape next round, the
control lever alone is insufficient and we should escalate (e.g. inject
a divergence hint on the warn, or a different tool affordance).

### Not addressed this round (noted for future rounds)

- **Timeout cluster** (9 tasks: 010,015,118,396,684,965,1032,1089,1818):
  `finished: error` / "timed out" with token counts 4k-21k, well below
  the 140k compaction threshold → infra/model-chat timeout, not context
  bloat. Not harness-fixable via processor; skip unless infra changes.
- **Premature-completion cluster** (7 tasks: 028,140,264,505,933,1653,
  1781): `no_tool_calls` — agent declares done but verification fails.
  Genuine correctness gaps (505 even noted a discrepancy then quit). The
  existing CustomSelfVerifyProcessor already fires one verify prompt;
  deeper fix likely a capability gap, not harness. Revisit if a
  verification-loop mechanism shows promise.

## Round 2 — structured system-prompt template

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-14T03:00:00Z
hypothesis_id: h_prompt_survey_verify_v1
levers: [instruction]
predicted_affected: [task_000748_c9807703, task_000933_1f27096a, task_001706_24462a09, task_001090_c61c71f2, task_000118_3043e92d, task_000578_cebe85a5, task_001031_a8f0eb37, task_000264_ab8c7253]
cited_candidates: [C-002]
gating_outcome: accepted
gating_attribution: score=29/50; +4/-5 gained=task_000578_cebe85a5,task_000748_c9807703,task_001321_658ce4a8,task_001706_24462a09 lost=task_000329_a3ac56b0,task_000965_069bb95f,task_001032_1adaccb9,task_001536_acfe6c35; score 0.5800 >= incumbent(mean) 0.6000 - tol 0.0400
expected_global_gain: "Closes the largest harness-addressable cluster: premature/unverified completion + wrong-path deliverables (748,933,1706,1090,118 and output-missing 578,1031,264). A survey+plan+verify-before-exit prompt generalizes to every task class."
regression_risk: "Low — all 30 R1-passing tasks already produce correct deliverables; a stronger prompt does not turn a correct solution incorrect. Main downside is a few extra Bash calls (survey + verification) per task."
cost_shift: "Mild + : one survey command up front plus 1-3 verification commands before exit, partially offset by less wrong-path thrashing."
rollback_trigger: "If pass_rate drops below R1 (30/50), or a currently-passing short task regresses to max_steps from verification churn, revert to the bare default prompt (delete templates/tb2_agent.j2)."
-->

### Why

**Key structural discovery:** the tmax eval path (`recipe/tmax_eval/run_eval.py`
→ `agent_loop.run_agent`) is a self-contained loop that does **not** run the
harnessx processor pipeline. The only part of `config.yaml` it consumes is the
system prompt, resolved by `_resolve_system_prompt`, which reads a
`templates/*.j2` (or `system_prompt.txt`) sitting next to the config, else falls
back to the bare 5-line `agent_loop.SYSTEM_PROMPT`. Consequently R1's
`LoopDetectionProcessor` insertion was inert here (29→30 is within repeat-spread
noise per `history/OVERVIEW.md`), and the R0/R1 agents ran with the bare default
prompt that has zero guidance on environment survey, planning, exact output
paths, or pre-exit verification. The dominant harness-addressable failure cluster
in R1 is `finished=no_tool_calls` with a deliverable that is missing, truncated,
or off-spec — the agent declared done without verifying its own output against the
task's stated requirements. The only behavioral lever exposed on this benchmark is
the system prompt.

### Changes

- `templates/tb2_agent.j2` — new structured system-prompt template (general
  strategy: read task + note exact output paths; survey env first; decompose/plan;
  write to exact paths; mandatory Definition-of-Done verification of every
  deliverable's existence + content + constraints before stopping; background-service
  discipline; no `apt-get update`). No task-specific literals.
- `config.yaml` — swap `SystemPromptProcessor.system_builder` from
  `_StaticSystemPromptBuilder` to `TemplateSystemPromptBuilder` pointing at the
  new template via absolute `file://` path (keeps config coherent; the eval loop
  picks the template up via `_resolve_system_prompt`). Rest of pipeline unchanged.

### Evidence

- `task_000748_c9807703` last assistant turns: "The script is working correctly /
  complete" then summarizes stripping `error[E` down to `0502` — never re-checks
  the required `E0502` literal survived; verifier asserts `'E0502' in '0502\n'`.
- `task_001090_c61c71f2` final_pytest: `Process name is 'bash', expected 'monitor'`
  and JSON list-vs-dict mismatch — stopped without validating output shape.
- `task_000933_1f27096a` final_pytest: `Extracted graph_math_double binary not
  found` — stopped without verifying tarball contents.
- `task_000578_cebe85a5` / `task_001031_a8f0eb37` final_pytest: required
  `results.json` never created at the exact path — no exit-time `ls` check.
- Confirmed `_resolve_system_prompt` now returns the new 3874-char template with a
  Definition-of-Done section (verified in-session), proving the change reaches the
  agent on this eval path.

### Uncertainty

The prompt won't fix pure single-chat transport timeouts (010/015/028/264 etc. die
on `chat failed: timed out`) or genuine capability/algorithm gaps (396 deviation,
1937 grid value, 1781 deadlock logic, security classifier 505/1701) — those are not
Instruction-addressable. If the survey/verify discipline adds enough steps that a
short passing task now runs long or a mid-work timeout gets triggered more often,
pass_rate could dip; the rollback trigger covers that. If R3 shows the verify
discipline flips several of the predicted tasks, consider tightening the prompt's
step-efficiency guidance.

## Round 3 — no-repeat loop-break instruction

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-14T04:00:00Z
hypothesis_id: h_loop_break_prompt_v1
levers: [instruction]
predicted_affected: [task_000015_89886d8d, task_000958_4bb2b05d, task_001701_95e3bbcb]
cited_candidates: [C-003]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the 3-task max_steps identical-repeat loop cluster (015/958/1701, 3 distinct domains) by re-expressing R1's loop-detection intent on the ONLY lever that reaches the agent on this eval path (system prompt). Generalizes to any future task where the model mechanically repeats a stuck call to the step limit."
regression_risk: "Near-zero on pass-rate. All 29 R2 passing tasks have max identical-consecutive-command run = 1 — legitimate work never repeats a command byte-for-byte, so the new rule never fires on a passer. Only added surface is one prompt section; the proven R2 template body is preserved verbatim."
cost_shift: "Net negative (savings). The three targets each burned all 80 steps (015=282s, 958=188s, 1701=67s); breaking the loop earlier reclaims steps/tokens. No added cost on non-looping tasks."
rollback_trigger: "If any R2-passing task regresses (esp. to early no_tool_calls from the pivot rule firing prematurely), or the 3 targets still hit max_steps with the SAME identical-repeat shape (indicates prompt discipline can't break a loop the model is mechanically committed to — escalate: the eval loop itself would need a repeat-guard, which is outside the config surface and belongs in NEEDS_FROM_HUMAN)."
-->

### Why

R1 diagnosed a mechanical identical-consecutive-Bash-call loop cluster and shipped
`LoopDetectionProcessor` (control lever). R2 then discovered the decisive structural
fact: the tmax eval path (`recipe/tmax_eval/agent_loop.run_agent`) does NOT run the
harnessx processor pipeline — it consumes ONLY the resolved system prompt. So R1's
loop detector is inert on this benchmark. The loop cluster therefore persists: in R2,
three tasks hit `finished=max_steps` at the full 80 steps, all from repeating a single
byte-identical command dozens of times. The correct home for the loop-breaking intent
is the ONE lever that reaches the agent here: the system prompt (Instruction).

### Changes

- `templates/tb2_agent.j2` — preserve the full proven R2 template and add a
  "Never Repeat — break out of stuck loops" section: forbid issuing a byte-identical
  command twice in a row, cap same-sub-goal same-failure retries at ~2 before a forced
  strategy pivot, narrate the pivot, and prefer a best partial deliverable over looping
  on one blocker until the step limit. General strategy, no task-specific literals.
- `config.yaml` — repoint `TemplateSystemPromptBuilder.template_path` to the R3
  template (absolute `file://`). Processor pipeline unchanged.

### Evidence

- `task_001701_95e3bbcb` messages.json: last 25 assistant tool calls are the identical
  command `grep -E "^[a-z_]+\(" /usr/include/json-c/json_object.h`; agent started
  productively then looped on the json-c API to the step limit. max_steps, 80 steps.
- `task_000958_4bb2b05d`: 24 consecutive identical `pkill -9 -f "./server" ... timeout 5
  ./server 2>&1 & ...` calls to step 80. max_steps.
- `task_000015_89886d8d`: 60 consecutive identical `python3 -c "from PIL import Image..."`
  calls, then thrashes, ends max_steps at 80 steps.
- Regression baseline: measured all 29 R2 passing tasks — max identical-consecutive-
  command run = 1 across every one. The loop rule never fires on a passer.
- Confirmed `_resolve_system_prompt` returns the new 5243-char template containing the
  "Never Repeat" section (verified in-session), so the change reaches the agent.

### Uncertainty

The prompt rule may not break a loop the model is mechanically committed to (the same
risk R1's warn-nudge faced). If so the three targets stay at max_steps with the same
shape and the only remaining fix — a hard repeat-guard in `agent_loop.run_agent` — is
outside the evolvable config surface (system-prompt only) and would need a human change
to the eval loop. Conversely, an over-aggressive pivot could make the model give up too
early on a solvable sub-goal; the rollback trigger watches for R2-passing regressions.
