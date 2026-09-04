# Candidates — R5 c2

## Candidate C-001 — VerifierDepEnsurer (ensure `requests` for verifier)

**Three-axis tag:** lens=verifier-phase-infrastructure / lever=control / intent=unblock-guaranteed-zero-cluster

**Assigned focus:** `task_000028_7fe033ac` (system_administration: Nginx reverse
proxy + C++ HTTP socket backend on a UNIX socket).

### Signal (verified from trajectories)

- `task_000028_7fe033ac.result.json`: `initial_pytest.passed=true`,
  agent `exit_reason=done` at 52 steps (`finished=no_tool_calls`), yet
  `reward=0`. `final_pytest.output_tail`:
  `test_final_state.py:6: import requests` →
  `ModuleNotFoundError: No module named 'requests'` →
  `Interrupted: 1 error during collection`. The whole test module errors at
  collection — every assertion is skipped, so solution quality is irrelevant.
- `task_000028_7fe033ac.messages.json`: the only two "requests" mentions are in
  the task prompt (msg 0) and a compaction summary (msg 1). The agent never ran
  `pip install requests` and never imported it. Confirmed via
  `grep -ho "pip[0-9]* install ..."` — only `pip3 install numpy` / `scipy`
  appear across the cluster.
- **Cluster (verified `grep -l "No module named 'requests'" *.result.json`):**
  6 tasks — `task_000028_7fe033ac`, `task_000106_23215092`,
  `task_000939_1592be48`, `task_000958_4bb2b05d`, `task_001857_24daeef3`,
  `task_002063_8c8adcfe`. Every one is a network-service / HTTP-API task; every
  one fails with the identical `import requests` collection abort; none installed
  requests. The cluster GREW by one (task_000939) since prior rounds.
- **pip works in-run:** `task_000106` messages show `pip3 install numpy` →
  Successfully installed numpy-2.2.6 (16.8 MB wheel downloaded), `scipy-1.15.3`.
  Ensuring a dependency is viable in this environment, not blocked.

### Root cause classification

Harness deficiency, NOT a model capability gap. The verifier's
`test_final_state.py` is injected AFTER the agent session ends (TB2 sandbox
topology) and is not present during the agent phase, so the agent cannot observe
or anticipate the `import requests` requirement. Testing the service with
`urllib`/`curl` (which the agents do) is entirely valid. This is a guaranteed
reward=0 for a whole cluster for a purely infrastructural reason independent of
solution correctness.

### Intervention

New `MultiHookProcessor` `VerifierDepEnsurer` (processors/verifier_dep_ensurer.py).
- `on_task_start` arms only tasks whose description names a network listen
  surface (`_LISTEN_SURFACE` regex: http/rest/api/endpoint/microservice/
  server/socket/port/proxy/nginx/uvicorn/gunicorn/flask/fastapi/`:PORT`/
  127.0.0.1/localhost:N).
- `on_before_tool`, on the first substantive approved Bash call of an armed
  task, prefixes the command with an idempotent, silent, best-effort guard:
  `python3 -c 'import requests' 2>/dev/null || pip install -q requests ... || true ; <original>`.
- Fires ≤1×/task; rewrites only `tool_input` (a ToolCallEvent field) — never
  inserts/drops/reorders any message (contract-clean).

### Arming precision (verified by replaying `_LISTEN_SURFACE` over all 50 prompts)

Arms 18/50 tasks: all 6 cluster beneficiaries + 4 currently-PASSING tasks
(task_000011, task_000477, task_000760, task_001382) + 8 other-cause failures.
On the 4 passing and 8 other-cause tasks the guard is a strict no-op for
correctness: it only ADDS `requests` (never removes anything) and short-circuits
when requests already imports. The 34 non-armed tasks never see it.

### Retroactive check

*Variant: "if this processor had been live in R4, would task_000028 have
flipped?"* — The guard makes `requests` importable, so the verifier's
`test_final_state.py` would collect instead of aborting. Whether each of the 6
then PASSES depends on whether the agent's service is actually correct; but
every one currently scores 0 for the collection abort alone, so the floor can
only rise. For `task_000028` the agent's solution passed `initial_pytest` and
exited cleanly — a strong indication the collection abort was the sole blocker.

### Why control, not instruction/configuration

- **Not instruction:** the agent cannot be told to install `requests` — it has
  no way to know the verifier needs it (test file absent during agent phase),
  and telling every service agent to `pip install requests` speculatively would
  be a task-specific-knowledge injection the philosophy forbids, plus it would
  not fire deterministically. A processor guarantees the dependency exists.
- **Not configuration:** no existing knob addresses verifier-phase package
  availability.
- **Control** (a processor that intercepts the first Bash call and ensures the
  dependency) is the only lever that closes the gap deterministically and
  contract-safely.

### Pareto statement

- `expected_global_gain`: unblocks a 6-task network-service cluster (multiple
  domains: sys-admin, data_querying, release/deploy, DB reliability, support
  diagnostics, PR-review) all currently guaranteed reward=0 for an
  infrastructural reason. Generalizes to any unseen service task whose verifier
  imports `requests`.
- `regression_risk`: very low. Guard is `|| true`-terminated (offline → no-op →
  status quo, no new failure); no-op when requests already present; original
  command runs unchanged after `;`; fires ≤1×/task on armed service tasks only;
  the 4 armed passing tasks cannot regress on correctness (guard only adds a
  package). Rewrites only tool_input — contract-clean (auto-check + contract
  validator report 0 violations).
- `cost_shift`: negligible. One fast import probe (+ at most one quiet one-time
  pip install) prepended to a single Bash call on 18 armed tasks; no forced
  extra model turns; zero on the 34 non-armed tasks.
- `rollback_trigger`: revert if any previously-passing task (esp. task_000011,
  task_000477, task_000760, task_001382) regresses to F attributable to the
  guard, or if synthetic replay fails on the processor.
