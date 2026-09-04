# Candidates — R1 c2 (focus: system prompt specificity)

## Candidate C-001 — Step-budget awareness + convergence discipline in the system prompt

**Three-axis tag:** lens=exit-behavior / lever=SystemPrompt / intent=close-failure-cluster

### Signal (harness-observable)
6 of 25 failures exit with `exit_reason=budget_exceeded` at exactly
`steps=80` (the hard `max_steps=80` cap in `recipe/tmax_eval/agent_loop.py`
and `harness_runner.py`). Meanwhile 3 tasks that *passed* also hit 80,
so the cap itself is not fatal — thrashing before the cap is.

Failing budget_exceeded tasks:
- task_000028_7fe033ac (anchor, sysadmin, 635s)
- task_001321_658ce4a8, task_000015_89886d8d, task_001701_95e3bbcb,
  task_001673_86224c91, task_000329_a3ac56b0

### Verified body evidence (anchor trajectory)
Reconstructed tool-call sequence for task_000028 shows the agent
reached a *working* state early — an HTTP GET to the nginx proxy
returned `HTTP/1.1 200 OK ... 150` (a real backend response) around
step ~23 — then **kept re-testing and re-editing already-working
files** instead of confirming and stopping:
- repeated `python3 -c` raw-socket probes of the same endpoint
  (steps 12,13,17,18,19,23)
- repeated `cat server.cpp | grep 'while(true)'` inspections
  (steps 14,15,16)
- repeated nginx rebuild/restart cycles and a full `server.cpp`
  rewrite + recompile late in the run (steps 21,25,26)

The agent never maintained an explicit "what remains" checklist, so
it re-litigated solved sub-goals and exhausted all 80 steps.

The current system prompt (5 lines) says only "inspect the
environment, edit files, and run commands" and "when complete, stop"
— it gives **no** notion that a hard step budget exists, and **no**
guidance to avoid redoing verified-working work. The
`TaskTimeReminderProcessor` is configured with `warn_at` but **no
`timeout_seconds`**, so it is disabled at runtime — the agent gets
*zero* budget signal from any source.

### Cross-trajectory check (>=2 others)
- task_000015_89886d8d (software_engineering, budget_exceeded 80,
  490s) — same class: long-running multi-artifact task, ran out of
  steps.
- task_001673_86224c91 (data_querying, budget_exceeded 80, 654s) —
  same class.
- COUNTER-evidence: task_000024/001781/001089 hit 80 steps and
  *passed*. So the intervention must not punish legitimate
  long tasks — it should only nudge economy/convergence, never
  force an early stop. A pure prompt nudge (no processor that
  truncates or forces exit) satisfies this.

### Retroactive check
Variant used: **"would the changed prompt have altered this
trajectory?"** For task_000028, guidance to (a) plan the required
outputs up front and track which remain, and (b) not re-verify or
re-edit a sub-goal already confirmed working, directly targets the
observed thrash (re-probing a 200-OK endpoint, re-grepping the same
source, rewriting a compiling server). Freed steps could have been
spent confirming the frame-count semantics rather than churning.

### Intervention
Replace the sibling `system_prompt.txt` (read by
`SiblingSystemPromptBuilder`, already the active builder) with a
prompt that keeps the existing directives and **adds general
work-economy strategy**:
1. survey the environment and enumerate required deliverables first;
2. keep a short running checklist of remaining vs. done sub-goals;
3. do not re-run a check or re-edit a file once its result is
   confirmed correct — spend steps on unmet requirements;
4. you operate under a finite step budget; prefer decisive progress
   over repeated re-verification of the same state;
5. verify each deliverable's *content* (not just existence) once,
   then stop.

All items are task-agnostic strategy. No task IDs, paths, filenames,
constants, or answers. The generalization test passes: an unseen
multi-step terminal task benefits from planning + not re-doing
confirmed work.

### Why SystemPrompt, not a processor/tool
- Assigned focus is system-prompt specificity.
- A processor that force-stops on step N would regress the 3
  long-but-passing tasks (hard collateral). A prompt nudge changes
  disposition without removing the agent's ability to keep working
  when work genuinely remains — strictly lower regression risk.
- The `CustomSelfVerifyProcessor` already handles the *premature
  exit* (`done`-but-wrong) side; the gap left open is the *opposite*
  failure — non-convergence / thrash — which no current mechanism
  or prompt line addresses.

### Pareto statement
- expected_global_gain: the `budget_exceeded` thrash cluster (6
  fails). Even flipping 1–2 is net positive; the guidance is generic
  enough to also tighten `done`-cluster tasks that waste steps.
- regression_risk: low. Risk is that a genuinely long task stops one
  step too early — mitigated by pairing economy language with the
  existing self-verify checklist (verify-then-stop, not stop-early).
  No processor/threshold change, so passing clusters keep their
  current control flow byte-for-byte.
- cost_shift: neutral-to-down. Fewer redundant re-probes/rebuilds =
  fewer tokens on thrash tasks; +~40 lines of static system prompt
  per task is negligible vs. multi-hundred-step runs.

### Rollback trigger
If next round shows the budget_exceeded cluster unchanged AND any
previously-passing long task (024/1781/1089) regresses to a
premature `done` fail, revert the prompt to the R0 5-line version.
