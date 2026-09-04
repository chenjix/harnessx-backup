# Candidates — R2 / c0

Assigned focus: `task_000010_644ab1c2` (system_administration) fails.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a one-shot `ModuleShadowRecoveryProcessor` (`on_after_tool`) that detects a
stdlib module-shadow / circular-import crash in Bash output and injects a
run-time workaround, so the agent stops "fixing" the crash by renaming/moving
the required deliverable to a safe name.

- Tasks affected: `task_000010_644ab1c2` (single distinct task in this batch
  exhibits the exact mechanism; the mechanism — required deliverable name
  colliding with a stdlib module — is a generic Python footgun spanning the
  "correct logic, wrong path" class named in the tb2-playbook, so the fix is
  written for the class, not the task).
- Signal: `result.json` → `final_pytest` fails `test_operator_script_exists`
  (`/home/user/operator.py` absent) with `finished=no_tool_calls`,
  `exit_reason=done`. The decisive tool result carries a stdlib circular-import
  traceback.
- Verified (Read, `task_000010_644ab1c2.messages.json`):
  - msg 0 (task): "write a Python script at `/home/user/operator.py`".
  - msg 49 (assistant tool_call): `mv /home/user/k8s_operator.py
    /home/user/operator.py && python3 /home/user/operator.py &` — the `mv`
    placed the file at the CORRECT path.
  - msg 50 (tool result): `AttributeError: partially initialized module
    'functools' has no attribute 'lru_cache' (most likely due to a circular
    import)` — the shadow crash, walking through `/usr/lib/python3.10/...`.
  - msg 51 (assistant): "The naming conflict is real. ... I need to keep the
    script at `/home/user/k8s_operator.py`" → it MOVED THE FILE BACK, deleting
    the correct deliverable.
  - msg 58 (final exit): declares complete with the file at `k8s_operator.py`.
- Why Control not Instruction: the failure is a mechanical crash-signature the
  agent must be redirected on AT THE MOMENT it occurs (msg 50), before it
  reasons itself into the destructive undo (msg 51). A system-prompt rule about
  module shadowing would be generic background text the agent already "knows"
  (it correctly diagnosed the shadow) yet still acted wrongly — the gap is not
  knowledge but a just-in-time redirect keyed on the actual traceback. A
  reactive `on_after_tool` hook fires exactly when the signature appears and
  passes through silently otherwise (zero cost on the vast majority of tasks).
- Why not the R1/c0 exit-gate guard (`UnmetRequirementExitGuard`, order 91):
  that fired only at EXIT on acknowledged-blocker phrasing — too late here,
  because by exit the agent had already destroyed the deliverable and no longer
  described it as a blocker. This candidate fires earlier, at the crash, and is
  keyed on the mechanical traceback rather than natural-language phrasing.
- Retroactive check (A-corrective): yes — at msg 50 the file was ALREADY at the
  correct path `/home/user/operator.py`; the only thing that lost the point was
  the agent's subsequent `mv` back to `k8s_operator.py`. Had the hint fired
  after msg 50, telling the agent to keep the file put and re-run from a
  non-shadowing cwd (`cd /tmp && python3 /home/user/operator.py`), the existence
  check `os.path.isfile('/home/user/operator.py')` would have passed.
- expected_global_gain: recovers the "required filename shadows a stdlib module"
  slice of the "correct logic, wrong path" hard-failure class (operator.py,
  test.py, random.py, queue.py, token.py, types.py, email.py, string.py, ...).
  A future task with any such deliverable name is protected.
- regression_risk: near-zero. Signature-gated on TWO conjuncts (a
  circular-import phrase AND a stdlib path in the traceback) and one-shot, so it
  is silent unless a genuine shadow crash occurs; worst case is one extra user
  message + one model turn on a task that legitimately hit a stdlib
  circular-import. Cannot loop (fires at most once/task). Ordered after all
  existing exit/self-verify processors so it cannot perturb their singleton
  slots.
- cost_shift: negligible aggregate — +1 user message and at most +1 model turn
  only on the rare subset of tasks that emit the shadow-crash signature; zero on
  all others.
- rollback_trigger: if R3 attribution shows `task_000010_644ab1c2` still
  failing AND any T->F regression on a task that previously exited cleanly,
  revert the processor.

Note (not a harness fix): `task_000010_644ab1c2` also fails
`test_api_success_log` — `config.yaml` was never applied (the pexpect apply
loop / port-forward retry broke on the second manifest; the visible log is from
a partial earlier run). That is a model capability gap in the agent's own
script logic, not a harness deficiency; no harness intervention embeds that
without task-specific knowledge. C-001 removes the path-existence blocker; if
the log test remains the residual failure, that is an agent-logic gap to note,
not to patch in the harness.
