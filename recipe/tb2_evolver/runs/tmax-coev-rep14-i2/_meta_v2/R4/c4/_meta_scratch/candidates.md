# Candidates — R4 c4

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Register a `VerifierClientDepEnsurer` `MultiHookProcessor` that, once per task
on the first substantive approved Bash call, prefixes an idempotent, silent,
`|| true`-terminated guard that ensures the standard `requests` client is
importable — so the external verifier's injected `test_final_state.py` can be
*collected* instead of aborting at `import requests`.

- Tasks affected (corrective, ≥2 distinct, same mechanism):
  task_000106_23215092 (data_querying, ASSIGNED FOCUS), task_000028_7fe033ac
  (system_administration), task_000939_1592be48 (software_engineering),
  task_000958_4bb2b05d (data_querying), task_001857_24daeef3 (debugging),
  task_002063_8c8adcfe (software_engineering). Six tasks, four domains.
- Signal: `final_pytest.rc=2` with output_tail
  `import requests` → `ModuleNotFoundError: No module named 'requests'` →
  `Interrupted: 1 error during collection`; every one has
  `initial_pytest.passed=true` (placeholder passed) and reward=0. `grep -l
  "No module named 'requests'" *.result.json` returns exactly these 6 tasks.
- Verified (Read):
  - task_000106 result.json: `initial_pytest.passed=true`; `final_pytest`
    `passed=false rc=2`, tail `test_final_state.py:4: import requests →
    ModuleNotFoundError → Interrupted: 1 error during collection`. Agent exited
    `done` at 35 steps; messages show it tested its Flask API on 127.0.0.1:8000
    via `urllib`, and its only pip installs were numpy/scipy (never requests).
  - task_000106 messages: `pip3 install numpy` → `Successfully installed
    numpy-2.2.6`; `pip3 install scipy` → `Successfully installed scipy-1.15.3`
    — proves runtime pip works in this eval (network not blocked here).
  - task_000028 / task_000939 / task_000958 / task_001857 / task_002063
    result.json: identical `import requests` collection abort; all
    `initial_pytest.passed=true`, reward=0; each issues 50+ Bash calls (so the
    fire-once guard reliably fires).
- Why Control not Instruction: the failure is 100% in the verifier phase on a
  test file that does not exist during the agent phase (TB2 sandbox topology),
  so the agent cannot be prompted to anticipate it — testing with `urllib`/
  `curl` is entirely valid and no system-prompt rule can conjure a dependency
  the agent has no reason to install. A mechanical `on_before_tool` guard that
  fires uniformly across tasks is the correct layer. Not Action: no new agent
  action space is needed; we mechanically pre-stage an environment dependency.
- Why unconditional not keyword-armed: prior versions of this fix (R1/R2
  `h_verifier_dep_requests_v1/v2`) armed on description keywords. That arming is
  a brittle proxy — the agent never sees the verifier's imports, so the
  description cannot reliably predict which tasks `import requests`. Evidence:
  the cluster grew from 5 → 6 this round (task_000939, software_engineering,
  joined). Firing unconditionally eliminates the arming-miss failure mode at
  negligible cost, because the guard is idempotent + silent + `|| true`-
  terminated + fire-once.
- Retroactive check (A-corrective): yes — if `requests` had been importable,
  pytest would have *collected* `test_final_state.py` and run the assertions
  against the agent's (initial_pytest-passing) solution instead of erroring the
  whole module at collection. For at least the assigned focus task_000106
  (functionally complete Flask API verified via urllib) this flips to pass; for
  any residual member where the solution was also wrong, the guard did its job
  and the remaining gap reclassifies to a capability gap to log separately.
- expected_global_gain: Unblocks a 6-task, 4-domain cluster (data_querying,
  system_administration, software_engineering, debugging) whose reward=0 is
  purely infrastructural — pytest collection aborting on a missing third-party
  client the agent had no reason to install and could not observe. Generalizes
  to any unseen task whose verifier `import requests`; zero task-specific
  literals (no task ids, ports, or endpoints in the code).
- regression_risk: Very low. Guard is `|| true`-terminated, so the agent's
  original command always runs unchanged after the `;`; it is a no-op when
  `requests` already imports (short-circuits before any install) and fires ≤1×
  per task. On an offline / pip-blocked image the install fails silently and
  behaviour is exactly today's (no new failure). It never mutates message
  history (contract-clean), only rewrites the `tool_input` of an approved
  ToolCallEvent — the same surface the existing pipeline processors use.
- cost_shift: Negligible. One near-instant `python3 -c 'import requests'` probe
  (+ at most one quiet `pip install` of a small pure-Python wheel) prepended to
  a single Bash call per task; no extra model turns.
- rollback_trigger: Revert if any previously-passing task regresses to F
  attributable to the prepended guard, or if synthetic replay fails on the
  VerifierClientDepEnsurer processor.
