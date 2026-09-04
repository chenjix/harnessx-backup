# Candidates — R2 / c7

Assigned focus: `task_000010_644ab1c2` and `task_000015_89886d8d` both fail.

## Diagnosis summary

- **task_000010_644ab1c2** — `exit_reason=budget_exceeded`, 80 steps, 1113s.
  Two distinct verifier failures:
  1. `test_operator_script_exists` — the task text says the script MUST live at
     `/home/user/operator.py`; the agent authored a correct-looking script at
     `/home/user/k8s_operator.py` (msg 42) and never reconciled against the exact
     required path. This is the TB2 "correct logic, wrong path" structural
     failure (playbook: exact-path hard failure). **Harness-actionable.**
  2. `test_api_success_log` — only the ConfigMap was ever applied through the
     flaky port-forward; the durable-proxy / stale-port churn burned the budget.
     This service-churn root is owned by sibling pending hypotheses
     (h_bg_service_hygiene_v1) and is NOT re-proposed here.
- **task_000015_89886d8d** — `exit_reason=done`, accuracy `0.0000`. OCR schema
  task. The agent's OCR (msgs 2/4/12) recovered a garbled-but-readable mapping;
  the golden output keys were `product_id`/`category`/`order` (visible even in
  the garble: `> tproduet_id* ... category' ... ordert`), but the agent wrote
  output keys `department`/`sort_order` derived from the literal query-param
  names (msg 37 ROUTES). Zero records matched. This is a **model OCR/reasoning
  mapping-direction capability gap** — the correct answer was in context and the
  model misinterpreted it. No harness mechanism reliably conjures correct
  interpretation of garbled OCR; prior rounds already tried an OCR ladder and a
  rigor reminder (both pending). Logged as a capability gap; not patched here.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RequiredPathAuditProcessor` that, at the agent's first exit-intent turn,
extracts the exact backtick-quoted absolute output paths from the task
description and injects a REAL read-only `Bash` existence check (`ls -la` /
`MISSING`) over those exact paths, then appends one reconcile instruction to
move/create any MISSING deliverable to its exact required path before finishing.

- Tasks affected: `task_000010_644ab1c2` (primary — wrong exact path
  `k8s_operator.py` vs required `operator.py`). Generalizes to the recurring TB2
  "correct logic, wrong path" class documented in the playbook ("a file written
  to the wrong location or with a different extension is a hard failure
  regardless of content correctness"). The same wrong-exact-path shape is a
  cross-task structural hazard on every task whose text names an exact output
  path — the audit surfaces the mismatch as a concrete tool result on ALL such
  tasks, not one task id.
- Signal: `task_000010` `final_pytest` `test_operator_script_exists` asserts on
  `/home/user/operator.py` existence and fails; agent's only `operator.py`
  mention outside the prompt is `k8s_operator.py` (msg 42). Task text (msg 0)
  quotes the required path in backticks: `` `/home/user/operator.py` ``.
- Verified (Read):
  - `task_000010_644ab1c2` msg 0: "write a Python script at
    `/home/user/operator.py`" (exact required path, backtick-quoted).
  - `task_000010_644ab1c2` msg 42 (tool): the agent's authored path is
    `"/home/user/k8s_operator.py"` — a plausible-but-wrong filename.
  - `task_000010_644ab1c2` `result.json` `final_pytest`: `FAILED
    test_operator_script_exists` (assertion on `os.path.isfile` of the exact
    path).
- Why Control not Instruction: an always-on prompt rule ("verify your output
  paths") already exists in spirit via the self-verify checklist, and the model
  demonstrably skims past text reminders and re-declares done (msgs 20-26 of the
  service-tasks and the pending rigor-reminder journal note). The fix needs
  ground truth to land as a tool *result* the model cannot narrate past — that
  is a mechanical `on_after_model` hook that injects a real Bash existence
  check, not a prompt sentence. It also must fire uniformly across every task
  that names an exact output path, which a per-call tool cannot express.
- Why Control not Action: the agent already has `Bash` (the only allowed tool)
  and can run `ls` itself; the gap is not a missing capability but a missing
  *mechanical trigger* that forces the exact-path check at the exit decision
  point. No new tool is warranted (TB2 also hard-limits tools to `Bash`).
- Retroactive check (A-corrective): yes — had the audit fired at task_000010's
  exit, the `MISSING: /home/user/operator.py` line would have been in context
  as a tool result; the agent used only 80 steps on churn but had already
  authored a working script at `k8s_operator.py`, so a one-line `cp/mv` to the
  exact path (a trivial in-context fix) flips `test_operator_script_exists`. The
  second failure (api_success_log) is a distinct service-churn root owned by a
  sibling; this candidate does not claim to flip it.
- expected_global_gain: Closes the TB2 "correct logic, wrong path" structural
  hazard class — any task whose text names an exact output artifact path now
  gets a concrete MISSING/EXISTS tool result at exit. Directly targets
  task_000010's `test_operator_script_exists`; generalizes to every exact-path
  task where a plausible-but-wrong filename would otherwise silently score 0.
- regression_risk: Low. Fires at most once per task, only when the description
  quotes >=1 backtick absolute file path; tasks without a stated exact-path
  contract never arm (byte-for-byte unaffected). The injected command is
  read-only (`ls`/`test -e`), so it can never create/move/delete or clobber a
  correct artifact. Ordered after the other exit-intent hooks (self-verify=90,
  svc-deps=91) so exit-intent turns serialize (each acts only on a turn that
  still has no tool calls). Worst false-positive cost: one read-only Bash
  round-trip + one reconcile message on a task whose paths already all exist,
  after which the agent freely exits on the next turn.
- cost_shift: +1 read-only Bash round-trip + ~1 reconcile message on armed tasks
  that reach exit; +1-2 short `mv`/`cp` calls only on tasks that actually had a
  misplaced deliverable. ~0 on non-armed tasks. Net-positive vs a wasted
  0-reward run whose only defect was a wrong path.

## Note — task_000015 (capability gap, no harness fix)

`task_000015_89886d8d`: requires correct interpretation of a garbled-OCR
schema-mapping DIRECTION (map `dept`→`category`, `sort`→`order`, not to the
literal param names). The correct target keys were present in the OCR output and
the model still misread the mapping — a model reasoning/OCR capability gap, not a
harness deficiency. Prior rounds already attempted an OCR-robustness ladder and a
rigor-completion reminder (both pending); embedding the specific key mapping in a
prompt would violate the no-task-literals rule. No harness fix — skip.
