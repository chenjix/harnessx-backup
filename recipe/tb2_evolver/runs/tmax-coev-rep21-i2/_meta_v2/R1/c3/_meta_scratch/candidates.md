# Candidates — R1/c3

Assigned focus: `task_000028_7fe033ac` fails.

## Diagnosis of the assigned task

The agent actually SOLVED `task_000028_7fe033ac`: it fixed the nginx socket
path, fixed and compiled `server.cpp`, ran it, wrote the logrotate config, and
verified end-to-end that `http://127.0.0.1:8080/` returns `150` through nginx
(step "Integration test PASSED!"). Reward is still 0 because the *verifier*
phase crashes:

```
/tmp/test_final_state.py:6: in <module>
    import requests
E   ModuleNotFoundError: No module named 'requests'
!!! Interrupted: 1 error during collection !!!
```

The verifier's pytest module imports `requests` to make HTTP calls, but the
eval container's system Python does not ship `requests`, so the test never
collects and the working solution is never scored. Per the tb2-playbook, the
verifier's test files are injected *after* the agent session ends — the agent
cannot see or infer this dependency. This is an **environment deficiency**,
not a model capability gap.

This is systemic, not idiosyncratic: **5 of 41 failing tasks** fail with the
identical `ModuleNotFoundError: No module named 'requests'` at verifier
collection time.

## Candidate C-001
[lens: capability-gap | lever: control | intent: corrective]

Add a `VerifierDepEnsureProcessor` (Control) that, at task start and only for
service-shaped tasks, best-effort ensures the standard Python `requests`
client is importable in the system Python.

- Tasks affected (>=2 distinct, same mechanism):
  - task_000028_7fe033ac (system_administration — nginx + C++ HTTP socket service)
  - task_000106_23215092 (data_querying — builds a graph analytics API service)
  - task_000958_4bb2b05d (data_querying — C++ HTTP microservice)
  - task_001857_24daeef3 (debugging — diagnostic video stream *service*)
  - task_002063_8c8adcfe (software_engineering — launches a validation service)
- Signal: `final_pytest.output_tail` on all five contains
  `ModuleNotFoundError: No module named 'requests'` at
  `test_final_state.py` collection; `grep -oh "No module named ..."` across the
  50 result.json files returns `requests` 10× (5 tasks × 2 pytest runs) and
  nothing else.
- Verified (body-quoted):
  - task_000028 step (tool result): agent's own integration test prints
    `Response body: 150 / Integration test PASSED!` — the service works; only
    the verifier import fails. `result.json.final_pytest.output_tail` =
    `import requests / ModuleNotFoundError`.
  - task_000106 `result.json`: `finished: no_tool_calls`, `reward 0`,
    `final_pytest` = same `import requests` ImportError. Body shows the agent
    ran `pip install scipy` successfully (`Successfully installed scipy-1.15.3`)
    — proving pip installs succeed in this container — but never installed
    `requests` because it had no reason to.
  - task_000958 / task_001857 / task_002063 `result.json.final_pytest` = same
    `import requests` ImportError.
- Why Control not Instruction: the agent has no way to *know* the verifier
  needs `requests` (test files are absent during the agent phase), so a prompt
  rule would be guessing; and even a correct guess relies on the model acting
  on it every time. A mechanical `on_task_start` hook fires uniformly and
  deterministically — it fixes the environment, not the agent's knowledge.
- Why Control not Action: no new agent capability is required; the fix is a
  pre-loop environment mutation, invisible to the agent's action space.
- Retroactive check (A-corrective): yes — if `requests` had been importable at
  verifier time on these five tasks, `test_final_state.py` would have collected
  and run. task_000028's service already returns the correct answer through
  nginx, so its test would have exercised a passing service. For the others,
  ensuring collection is a necessary precondition to any nonzero score.
- expected_global_gain: unblocks up to 5 currently-failing service tasks whose
  only failure is the missing verifier dependency; generalizes to any future
  HTTP-service task the verifier probes with `requests`.
- regression_risk: minimal. Gated on a service-task heuristic so non-service
  tasks are untouched; the install is idempotent (skipped when `requests`
  already imports); every failure path is swallowed (`|| true`); the processor
  never mutates `event.messages` (contract-neutral) and never raises. Worst
  case for a service task where the heuristic matches but the verifier does not
  need `requests`: one wasted ~1s pip check.
- cost_shift: negligible. One extra sandbox exec at task start for
  service-shaped tasks only; zero model tokens (pre-loop, no message injection).
