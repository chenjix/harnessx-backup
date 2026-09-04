# Candidates — R5 c3

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Register `VerifierDepEnsurer`: on HTTP-service tasks, prefix the first Bash
call with an idempotent, silent, `|| true`-terminated guard that ensures
`requests` is importable, so the verifier's `test_final_state.py` (which begins
`import requests`) can be *collected* instead of aborting the whole test module.

- Tasks affected (>=2 distinct failing, same mechanism):
  task_000106_23215092 (data_querying), task_000028_7fe033ac
  (system_administration), task_000958_4bb2b05d (data_querying),
  task_001857_24daeef3 (debugging), task_002063_8c8adcfe (software_engineering),
  task_000939_1592be48 (software_engineering) — 6 tasks, 4 domains.
- Signal: `final_pytest.passed=false, rc=2` with output_tail
  `import requests -> ModuleNotFoundError: No module named 'requests' ->
  Interrupted: 1 error during collection`; `initial_pytest.passed=true` on all
  six. `grep "No module named 'requests'" *.result.json` returns exactly these
  six task_ids, no others.
- Verified (body-quoted):
  - task_000106_23215092 result.json: `initial_pytest.passed=true`;
    `final_pytest` rc=2 output_tail shows `import requests` ->
    `ModuleNotFoundError` -> `Interrupted: 1 error during collection`. messages:
    agent built Flask API on 127.0.0.1:8000, tested every endpoint via urllib,
    exited `done` at 37 steps, never installed/mentioned requests
    (`grep -c requests messages.json = 0`). It also `pip3 install`ed numpy/scipy
    in-run successfully — proves runtime pip works.
  - task_000028_7fe033ac result.json: same collection abort; description is an
    Nginx reverse-proxy + C++ HTTP socket backend microservice; exit `done`
    52 steps; never installed requests.
  - task_002063_8c8adcfe result.json: same collection abort; description is a
    pyo3/Rust validation *service*; `pip install maturin` succeeded in-run
    (pip works); exit `done` 47 steps; never installed requests.
  - task_000958 / task_001857 / task_000939 result.json: identical
    `import requests` collection abort; each an HTTP/network service task.
- Why Control not Instruction: the agent cannot be *told* to install requests —
  the verifier test file does not exist during the agent phase (TB2 sandbox
  topology), so there is no observable signal that `requests` will be needed,
  and testing with urllib/curl is entirely valid. This is a mechanical
  environment-provisioning gap that must fire uniformly across the task class,
  not a knowledge gap. A prompt rule "always pip install requests on server
  tasks" would be a task-class-specific literal that also wastes a turn on the
  many armed tasks that fail for unrelated reasons; a mechanical guard is the
  narrower, contract-clean fix.
- Why Control not Action: no new capability is missing — Bash + pip already
  suffice; only the timing/uniformity of the install is the gap. A new tool
  cannot express a cross-task before-tool guard.
- Retroactive check (A-corrective): yes — with `requests` importable, pytest
  collects `test_final_state.py` and runs the assertions instead of aborting.
  For the three `exit=done` tasks whose solution the agent verified functionally
  correct (task_000106, task_000028, task_002063), the guard directly unblocks
  the flip. For the three `exit=budget_exceeded/error` tasks (task_000958,
  task_001857, task_000939) the guard removes the collection abort; if their
  solutions were also incomplete, the residual is a separate capability gap that
  becomes visible only once collection succeeds — the guard still did its job.
- expected_global_gain: Unblocks a 6-task HTTP/network-service cluster (4
  domains) whose reward=0 is caused purely by an infrastructural
  collection-abort the agent cannot anticipate, independent of solution quality.
  Generalizes to any unseen "build/deploy an HTTP service" task the verifier
  drives with `requests`.
- regression_risk: Very low. Detector arms 13/50 tasks: 6 beneficiaries, 5
  other-cause failures (guard is a no-op, cannot regress them), and 2
  already-passing (task_000011, task_001382). Both passing tasks have
  `final_pytest.passed=true` with no `requests` error, so the guard's `import
  requests` check short-circuits and behaviour is unchanged. Guard is
  `|| true`-terminated; if the image is ever offline the pip step fails silently
  and the task is exactly as today (no new failure). Fires <=1x/task, only
  rewrites tool_input, never mutates message history.
- cost_shift: Negligible. One fast import probe (+ at most one quiet pip install
  of a small pure-Python wheel) prepended to a single Bash call on armed tasks
  only; zero on the 37 non-armed tasks; no extra model turns.
- rollback_trigger: Revert if any previously-passing service task (esp.
  task_000011 / task_001382) regresses to F attributable to the guard, or if
  synthetic replay fails on the VerifierDepEnsurer processor.
