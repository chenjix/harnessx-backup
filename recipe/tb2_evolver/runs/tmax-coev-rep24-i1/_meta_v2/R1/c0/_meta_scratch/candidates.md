# Candidates — Round 1 (focus: task_000010_644ab1c2)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a one-shot `UnmetRequirementExitGuard` Control processor that intercepts an
exit whose text *admits* an unresolved required-deliverable conflict (output
left at the wrong path/name/form) and injects one focused nudge to satisfy the
exact deliverable instead of abandoning the requirement.

- Tasks affected: task_000010_644ab1c2 (primary; assigned focus).
  Mechanism generalises to any `no_tool_calls` exit where the agent verbalises
  a knowingly-unmet requirement — the largest failure class this round
  (13 of 19 failures exit `done`/`no_tool_calls`).
- Signal: `final_pytest` failed on `test_operator_script_exists`
  ("Operator script /home/user/operator.py does not exist"); agent
  `exit_reason=done`, `finished=no_tool_calls`, 42 steps. The working solution
  existed at a *different* path.
- Verified (Read messages.json):
  - step 15/18: agent wrote a working `operator.py`.
  - step 20: `python3 /home/user/operator.py` → traceback: `import subprocess`
    → ... → `import operator` resolves to the agent's own file (stdlib module
    shadowing).
  - steps 21/53/57/61/69: agent repeatedly moves the file between
    `operator.py` and `k8s_operator.py`; the pipeline runs fine as
    `k8s_operator.py` (step 50/74: `api_success.log` shows manifests applied,
    `k8s_backup.tar.gz` created).
  - step 79-82: `CustomSelfVerifyProcessor` fires once; agent `ls`-checks and
    confirms **`k8s_operator.py`** exists (not the required `operator.py`).
  - step 83 (final): "The only issue is that the script can't be named
    `operator.py` due to Python's module system conflict ... The task is
    complete." → exits with the required deliverable absent.
- Why Control not Instruction: the existing self-verify checklist (an
  Instruction-style injected message) already told the agent to confirm each
  output at its exact path — the agent complied and still exited, because it
  had *consciously accepted* the wrong path. A static prompt rule cannot fire
  *conditionally on the agent verbalising an unmet requirement at the exit
  boundary*; that requires an `on_after_model` hook that inspects the exit
  turn's text and re-opens the loop exactly once. This is a mechanical
  exit-gate, i.e. Control.
- Why Control not Action: TB2 exposes only `Bash`; no capability is missing —
  the agent already produced the artifact and can `mv`/`cp` it. The gap is a
  loop-control decision at exit, not a missing action.
- Retroactive check (A-corrective): yes. If the guard had fired at step 83, the
  agent would have been re-prompted to reconcile the requirement; the workaround
  is trivial (`cp k8s_operator.py operator.py`, or run the required file from a
  non-shadowing cwd / via absolute path from elsewhere — the file only needs to
  *exist* at `/home/user/operator.py`, and the pipeline was already proven to
  work). The final-state test checks existence, which a single copy satisfies.
- expected_global_gain: recovers self-sabotaging exits across the dominant
  `no_tool_calls` failure class (agents that solve the task but ship the
  deliverable in the wrong place/form after hitting a secondary wall). Directly
  targets the TB2 "correct logic, wrong path" hard-failure mode from the
  playbook.
- regression_risk: content-gated + one-shot, so it stays silent on clean exits
  (no acknowledged-blocker phrasing) and cannot loop. Worst case: one extra
  model turn on a task where the agent used blocker-like phrasing but was
  actually complete — costs a few tokens, does not change the verdict. It fires
  strictly after the existing single-shot self-verify (order 91 > 90) so it does
  not interfere with that processor.
- cost_shift: negligible in aggregate — fires at most once per task and only on
  the subset of exits containing acknowledged-blocker phrasing; adds ~1 model
  turn on those.
