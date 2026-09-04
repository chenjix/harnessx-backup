# Candidates — Round 1 (tmax-coev-rep27-i1)

Baseline R0: 41/50 (0.82). Failing by domain: system_administration 1/5,
data_querying 4/6, scientific_computing 4/5, software_engineering 4/5,
data_processing 5/6.

Diagnosis of the 9 failures (from `final_pytest.output_tail`):
- task_000028 (sysadmin): verifier `import requests` → ModuleNotFoundError.
- task_000958 (data_querying): verifier `import requests` → ModuleNotFoundError
  (collection error, ALL tests blocked).
- task_000140 (sysadmin): lingering `vm_service` processes — cleanup logic.
- task_000010 (sysadmin): task mandated creating `/home/user/operator.py`,
  which shadows the stdlib `operator` module and breaks the verifier's
  Python. Task-design collision, not harness-fixable — skip (NEEDS_FROM_HUMAN).
- task_000118 (sysadmin): peak log dir size exceeded threshold — capability/logic.
- task_000264 (data_querying): SQLite query plan didn't use an index — capability.
- task_001937 (sci-computing): wrong numeric answer (60 vs 50) — capability.
- task_000933 (sw-eng): expected binary not built into tarball — capability/logic.
- task_001818 (data_processing): text-casing mismatch in DB row — capability.

Only ONE root cause recurs across ≥2 distinct tasks with the same mechanism:
the `import requests` verifier-collection failure. The rest are one-off
capability/logic gaps (logged as skips) — no harness lever closes them and
patching domain knowledge into the prompt is out of scope.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Replace `CustomSelfVerifyProcessor` with `ServiceVerifyDepsProcessor`: same
one-shot self-verify checklist, plus an item telling the agent that for
network-service tasks the automated checker probes the service from Python
(commonly `import requests`) and to ensure `requests` is importable
(`python3 -c "import requests" || pip install requests`) before finishing.

- Tasks affected: task_000028_7fe033ac, task_000958_4bb2b05d (2 distinct
  tasks, 2 distinct domains: system_administration + data_querying).
- Signal: both `final_pytest.output_tail` end with
  `ModuleNotFoundError: No module named 'requests'` at verifier import time;
  both tasks build a localhost HTTP service (task_000028 Nginx+C++ backend on
  127.0.0.1:8080; task_000958 C++ HTTP microservice on 127.0.0.1:9090).
- Verified (Read):
  - task_000028 result.json `final_pytest`: `test_final_state.py:6: in
    <module> import requests → ModuleNotFoundError`. Agent body: tested with
    `curl` ("curl: command not found"), never installed `requests`, exited
    with a "task complete" summary.
  - task_000958 result.json `final_pytest`: `test_final_state.py:4: in
    <module> import requests → ModuleNotFoundError`, `1 error in collection`
    (blocks ALL tests). Agent body: used `curl`/`ss` to probe its own
    server, never installed `requests`.
  - Feasibility check: pip installs succeed in this environment — observed
    at run time in task_000684 (numpy download), task_001652 (pillow),
    task_001031 (biopython). So `pip install requests` the checklist nudges
    toward will actually work; internet is NOT blocked here.
- Why Instruction not Control: a Control processor cannot itself run a
  sandbox command to install the package — it only hooks the run loop. The
  agent *can* install it and simply doesn't know the verifier needs it (it
  never sees the verifier file). This is a knowledge gap about a general TB2
  verifier convention, so the right lever is extending the existing
  self-verify instruction the agent already reads. The change is delivered
  as a processor swap only because the checklist string lives inside a
  read-only module; the lever pulled is Instruction (checklist content).
- Why Instruction not Action: no new capability is needed — `pip` and
  `python3` already exist; the agent just needs the prompt to point at the
  missing dependency.
- Retroactive check (A-corrective): yes — for task_000958 the missing import
  was the ONLY failure (collection error before any assertion), so making
  `requests` importable flips it outright. For task_000028 the import error
  was blocking collection of the whole final-state module; with `requests`
  present the real service assertions get to run (the agent's own body shows
  the Nginx/backend integration was being iterated toward working). At worst
  it removes the guaranteed-0 collection crash and gives the service a chance
  to score.
- expected_global_gain: closes a 2-task cross-domain cluster
  (service-building tasks whose verifier imports `requests`); generalizes to
  any future service task with the same verifier convention.
- regression_risk: low. The change is additive to a checklist that already
  fires once at exit; it does not alter file/service verification steps. The
  extra `pip install requests` is a no-op (`|| pip install`) when requests is
  already present or when the task has no service. Singleton group and hook
  set are identical to the processor it replaces, so no double-injection.
- cost_shift: negligible — one extra checklist paragraph (~90 tokens) in the
  single self-verify turn, and at most one extra Bash call on service tasks.

## Skipped (model capability gaps / task-design — no harness fix)
- task_000010: task-mandated filename `operator.py` shadows stdlib → verifier
  Python breaks. Task-design collision; not harness-fixable.
- task_000140: process-cleanup logic gap.
- task_000118: log-rotation size logic gap.
- task_000264: SQLite index/query-plan knowledge gap.
- task_001937: numeric-computation error.
- task_000933: build/packaging logic gap.
- task_001818: text-casing normalization gap.
These are single-task, distinct-mechanism capability gaps; embedding domain
knowledge to fix them violates the evolution philosophy.
