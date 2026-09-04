
## Round 4 (c0) — stdlib module-shadow advisor

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_module_shadow_advisor_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes a general Python footgun — a required-name script whose basename shadows a stdlib module (operator/queue/types/select/socket/json/csv/io/...) crashes with a circular-import error on python3 script.py, and the agent's instinctive rename directly violates the mandated filename. Advisory carries no task-specific literals and generalizes to any unseen collision of this shape."
regression_risk: "~Nil: fires <=1x/task and ONLY on the two-part shadowing signature (circular-import error AND a user-owned traceback frame re-entered through a stdlib frame). Ordinary intra-project circular imports (user-only frames) and all non-crashing runs never match (unit-tested). Mutates only the tool-result string (same contract as CustomEditToolProcessor/OcrQualityAdvisor); no message insert/drop/reorder."
cost_shift: "Negligible-to-slightly-positive: may prompt 1-2 corrective re-runs replacing wasted rename/re-debug churn on a matching task; exactly zero on the ~49 non-matching tasks. No forced extra model turns."
rollback_trigger: "Revert if any previously-passing task regresses to F attributable to the advisory, or if synthetic replay fails on the ModuleShadowAdvisor processor."
-->

### Why

Assigned focus task_000010_644ab1c2 (write the required operator script: backup +
socat 9090->8080 port-forward + pexpect CLI automation) died budget_exceeded at
80 steps, reward=0. final_pytest shows two failing tests, but the CLEAN
harness-fixable one is test_operator_script_exists: the required script path does
not exist. Root cause verified in the transcript: the agent named the required
script with a basename that shadows Python's stdlib operator module. Running the
script by path from its own directory puts that dir first on sys.path, so the
stdlib's from-operator-import (reached transitively via import subprocess ->
collections) imported the agent's OWN file, crashing with
"cannot import name deque from partially initialized module collections
(most likely due to a circular import)". The agent CORRECTLY read the message as
a naming conflict but then applied the WRONG fix — renamed the script to a
non-shadowing name — which made the script run but left the mandated path empty,
guaranteeing reward=0 on the file-existence check no matter how good the logic.
This is a general, well-known Python module-shadowing footgun; the harness can
recognise the exact runtime signature and steer to the correct fix (keep the
name, invoke without the script dir on sys.path) rather than the rename trap.

### Changes

- processors/module_shadow_advisor.py — new ModuleShadowAdvisor
  MultiHookProcessor. on_before_tool records Bash calls that run a Python script
  by path (python[3] ... name.py; not -c / -m); on_after_tool, if that call's
  result carries the module-shadowing signature (a circular-import /
  partially-initialized-module error whose traceback contains BOTH a user-owned
  .py frame and a stdlib frame — i.e. control re-entered a user file through a
  stdlib import), appends a ONE-TIME generic advisory: this is basename shadowing
  (lists classic colliders), do NOT rename if the filename is task-required,
  instead invoke without the script dir on sys.path (run from another dir, or
  PYTHONSAFEPATH=1 / python3 -P on 3.11+), then re-confirm the required output
  path still exists. Fires <=1x/task, only on the exact signature. Contract-safe:
  mutates only the tool-result string, no message insertion. _order=33 (after
  CustomEditToolProcessor 30 / OcrQualityAdvisor 32, before
  CustomSelfVerifyProcessor 90).
- config.yaml — R1 lineage + register ModuleShadowAdvisor via absolute file://
  path immediately before CustomSelfVerifyProcessor. Everything else
  byte-identical to R1.

### Evidence

- task_000010 result.json: exit_reason=budget_exceeded, reward=0; final_pytest
  test_operator_script_exists AssertionError the required script path does not
  exist.
- task_000010 messages step 62: writes the required script name (heredoc); step
  64: runs it by path; step 65 tool result: traceback re-entering the user
  script through /usr/lib/python3.10/collections/__init__.py at
  "from operator import eq as _eq", ending
  "ImportError: cannot import name deque from partially initialized module
  collections (most likely due to a circular import)"; step 66: renames the
  script (the anti-pattern that empties the mandated path).
- Detector unit-tested: matches the real traceback + the run-by-path command;
  rejects python3 -c, python3 -m pytest, a user-only intra-project circular
  import, and a plain SyntaxError.
- Validators: canonicalize {"ok": true, "checked_templates": 0}; dry_fire
  likely_bugs 0/0; contract violations 0; literals findings 0.

### Uncertainty

Only task_000010 shows this exact signature in the R3 set (idiosyncratic-filter
note in candidates.md): shipped as the assigned-focus exception because the
mechanism is a general Python footgun with zero literals and a ~zero regression
surface. Two ways to know the bet is wrong: (1) task_000010 stays F even though
the advisory fired — expected in part, since its SECOND failing test
(test_api_success_log) needs the port forwarding to actually work, which the
agent never got right (a model capability gap, logged separately); the advisory
only removes the guaranteed-zero rename trap and flips test_operator_script_exists.
(2) a previously-passing task regresses attributable to the advisory — unlikely
given the two-part signature gate, but the rollback trigger.

NEEDS_FROM_HUMAN: task_000010's second blocker (manifests never applied because
the socat/python port-forward 9090->8080 was never made to work reliably) is a
model capability gap in wiring up a persistent port forwarder, not harness-fixable
without task-specific injection — skip.
