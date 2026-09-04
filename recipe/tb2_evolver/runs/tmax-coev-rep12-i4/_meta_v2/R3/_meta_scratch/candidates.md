# Candidates — R3

## Candidate C-003
[lens: failure | lever: control | intent: corrective]

Fire the verifier-dependency ensure (`import requests` / `pyyaml`, else
`pip install`) **proactively mid-run** — not only at clean exit-intent — so
HTTP-service tasks that exhaust the step budget still have `requests` installed
when the external verifier's `test_final_state.py` is collected.

- Tasks affected (same mechanism, distinct tasks):
  - task_000796_828a72cf (security)
  - task_000910_16cc0daf (system_administration)
  - task_002108_a8cfbf2a (file_operations)
- Signal: `final_pytest.output_tail` on all three = pytest aborting at
  *collection* time — `ImportError while importing test module
  '/tmp/test_final_state.py' ... ModuleNotFoundError: No module named
  'requests' ... Interrupted: 1 error during collection`. All three:
  `agent.exit_reason=budget_exceeded`, `steps=80`. The existing exit-intent-
  only `VerifierDepGuardProcessor` (in the R2 pipeline) never fired on them.
- Verified (Read):
  - task_000796_828a72cf.result.json → collection ImportError, `import
    requests` at test line 5; agent.exit_reason=budget_exceeded, steps=80.
  - task_000910_16cc0daf.result.json → identical collection ImportError,
    `import requests` at test line 5; budget_exceeded, steps=80.
  - task_002108_a8cfbf2a.result.json → identical collection ImportError,
    `import requests` at test line 4; budget_exceeded, steps=80.
  - Guard-never-fired confirmation: grep of "VERIFIER DEP CHECK" in each
    task's `messages.json` = 0 occurrences (the exit-intent path was never
    reached because the run ended in budget_exceeded, not clean exit).
  - pip-reaches-PyPI confirmation: task_001013_7f3bf12e.messages.json contains
    `Successfully installed blinker-1.9.0 click-8.4.2 flask-3.1.3
    itsdangerous-2.2.0 jinja2-3.1.6 markupsafe...` — a full flask dependency
    chain installed from PyPI at full speed, so `pip install requests` would
    succeed in-container.
- Why Control not Instruction: the dependency is a property of the *hidden
  verifier*, never stated in the task, so no prompt rule can teach the agent
  to install it (and even if it "knew", a budget_exceeded run may never reach
  the step where it would). The fix must be a mechanical hook that fires
  regardless of the agent's own trajectory. It's a Control refinement of an
  existing processor (change *when* it fires), not a new capability (Action):
  the ensure command already exists; only its trigger was wrong.
- Why not Configuration: the prior processor has no knob that lets it fire on
  budget_exceeded runs — the exit-intent gate is hard-coded in its
  `on_after_model`. Fixing it requires new hook logic (a proactive prepend),
  so it's an authored Control processor, not a kwarg tweak.
- Retroactive check (A-corrective): partial-yes — installing `requests`
  removes a *guaranteed 0* (pytest cannot even collect the test module today).
  With the module present the verifier's real assertions run; whether each of
  the three then passes depends on the server work, which was not observable
  (tests never executed). At minimum it converts a certain-fail collection
  error into a real test outcome for all three, and any that built a correct
  server flip from 0 to 1. Since the install is idempotent, downside is nil.

- expected_global_gain: Removes a mechanical, guaranteed-0 wall on the
  HTTP-service subset of the budget_exceeded cluster (3 tasks confirmed this
  round, and any future HTTP task that runs long). Generalizes to every task
  whose verifier does `import requests`/`yaml` and whose run does not end in a
  clean exit.
- regression_risk: Very low. The ensure command is idempotent (`python3 -c
  'import X'` first; `pip install` only on failure; whole thing `|| true`), so
  on the ~passing set where `requests` is already present it is a pure no-op —
  one extra Bash round-trip printing "ok: requests". Prepending it to a
  mid-run tool-call turn adds one tool result to context but does not replace
  the model's own tool call (the run loop executes every call in a turn).
  Fires at most once per task.
- cost_shift: Negligible increase — one extra Bash call (a few hundred tokens
  of banner output) per task, once. No change to the long runaway tails.
- rollback_trigger: Revert if R4 pass_rate < 0.20 OR any previously-passing
  task regresses with a "VERIFIER DEP CHECK" banner implicated in its final
  steps (e.g. the prepended call disrupting the model's flow).
