
## Round 2 — active required-output-path audit (c7)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T07:10:00Z
hypothesis_id: h_required_path_audit_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the TB2 correct-logic-wrong-path structural failure class: any task whose text names an exact absolute output artifact path now gets a concrete EXISTS/MISSING tool result at its exit turn, so a deliverable placed at a plausible-but-wrong filename/extension is surfaced and fixed before finishing. Directly targets task_000010 test_operator_script_exists (k8s_operator.py vs required operator.py); generalizes to every exact-path task where a wrong filename would otherwise silently score 0."
regression_risk: "Low - fires at most once per task, only when the description quotes at least one backtick absolute file path; tasks without a stated exact-path contract never arm (byte-for-byte unaffected). The injected command is read-only (ls/test -e), so it can never create/move/delete or clobber a correct artifact. Ordered after self-verify (90) and svc-deps (91) so exit-intent hooks serialize; each acts only on a turn that still has no tool calls."
cost_shift: "+1 read-only Bash round-trip + ~1 reconcile message on armed tasks that reach exit; +1-2 short mv/cp calls only on tasks that actually had a misplaced deliverable; ~0 on non-armed tasks. Net-positive vs a wasted 0-reward run whose only defect was a wrong output path."
rollback_trigger: "If R3 shows task_000010 still failing test_operator_script_exists with the processor firing (the model ignored the MISSING tool result), OR any previously-passing exact-path task regresses to done/reward=0 or budget_exceeded with this processor firing, revert the RequiredPathAuditProcessor entry."
-->

### Why

Assigned focus: task_000010_644ab1c2 (budget_exceeded, 80 steps) and
task_000015_89886d8d (done, accuracy 0.0000). They have distinct causes.

task_000010 fails two verifier tests. (1) test_operator_script_exists: the task
text says the script MUST live at the exact path /home/user/operator.py; the
agent authored a correct-looking script at /home/user/k8s_operator.py (msg 42)
and never reconciled against the exact required name - the canonical TB2
correct-logic-wrong-path structural failure (playbook: a file at the wrong
location/extension is a hard failure regardless of content). (2)
test_api_success_log: only the ConfigMap was applied through a flaky
port-forward; the service-churn budget-burn root is owned by sibling pending
hypotheses (h_bg_service_hygiene_v1) and is NOT re-proposed here.

The stock/self-verify pipeline only narrates a TEXT "verify your outputs"
reminder, which the model demonstrably skims past and re-declares done. This
round is the mechanical escalation for the exact-path half: extract the
backtick-quoted absolute output paths from the task text and, at the first
exit-intent turn, run a REAL read-only Bash existence check over those exact
paths so the ground-truth EXISTS/MISSING listing lands in context as a tool
result the model cannot narrate past, then append one reconcile instruction to
move/create any MISSING deliverable to its exact required path before finishing.

task_000015 is a model OCR/reasoning capability gap: the garbled OCR (msgs
2/4/12) carried the correct output keys (product_id/category/order - visible
even garbled), but the agent wrote output keys department/sort_order derived
from the literal query-param names (msg 37 ROUTES), so zero records matched
(accuracy 0.0000). The correct answer was in context and the model
misinterpreted the mapping direction - no harness mechanism reliably conjures
correct interpretation of garbled OCR, and prior rounds already tried an OCR
ladder and a rigor reminder (both pending). Logged as a capability gap; not
patched here (see candidates.md Note).

### Changes

- processors/required_path_audit.py - new RequiredPathAuditProcessor
  (MultiHookProcessor, singleton group tb2_required_path_audit, _order=93).
  On on_task_start parses backtick-quoted absolute file paths from the task
  description (prefers paths near a produce/output verb). At the first
  exit-intent turn (finish_reason in {end_turn,stop}, no tool calls), if it has
  at least one required path, rewrites the model turn into a single REAL
  read-only Bash existence check (ls -la / MISSING) over those exact paths, then
  queues one reconcile instruction on the next on_before_model. Bounded to one
  fire per task; never mutates the filesystem; keyed off generic path syntax,
  never off task ids; contract/dry-fire/canonicalize clean. (C-001)
- config.yaml - registered the new file://...::RequiredPathAuditProcessor after
  ServiceDepsReminderProcessor; everything else byte-identical to R1/c2.

### Evidence

- task_000010_644ab1c2 msg 0 (prompt): "write a Python script at
  /home/user/operator.py" - exact required output path, backtick-quoted.
- task_000010_644ab1c2 msg 42 (tool): agent's authored path
  /home/user/k8s_operator.py - plausible-but-wrong filename.
- task_000010_644ab1c2 result.json final_pytest: FAILED
  test_operator_script_exists (assertion on os.path.isfile of the exact path).
- task_000015_89886d8d msgs 2/4/12 (tesseract output) show product_id /
  category / order in the garble; msg 37 ROUTES uses department/sort_order;
  final_pytest Accuracy metric 0.0000 below 0.98 - capability gap, skipped.

### Uncertainty

Assumes that once the MISSING /home/user/operator.py line is a concrete tool
result in context, the model acts on it with a one-line mv/cp - a small
in-budget fix (it had already authored a working script). The mechanism is
stronger than a text reminder because ground truth arrives as a tool result, but
for task_000010 the run must also reach an exit-intent turn before
budget_exceeded; the flip therefore also depends on the service-churn budget-burn
not consuming all 80 steps (owned by a sibling). If R3 shows task_000010 still
failing test_operator_script_exists with this processor firing, the model is
ignoring the MISSING result and the next escalation is a Control hook that offers
a disambiguated candidate-path list. Watch any exact-path task that flips F with
this processor firing.
