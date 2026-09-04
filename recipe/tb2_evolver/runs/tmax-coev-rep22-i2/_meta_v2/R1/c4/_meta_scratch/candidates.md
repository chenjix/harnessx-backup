# Candidates — R1 c4

Assigned focus: `task_000106_23215092` (data_querying) fails.

## Diagnosis of the assigned task

The agent built a **functionally correct** solution:
- Correct self-join SQL for the co-authorship graph (verified weights match
  paper_authors by hand).
- networkx PageRank summing to 1.0 (verified in-trajectory).
- Flask API on 127.0.0.1:8000, `/author/<id>` returning the exact JSON schema,
  404 for missing authors, coauthors sorted by (weight desc, author_id asc).
- Agent tested every endpoint with `urllib` and confirmed correct output.

Yet `reward=0`. The verifier's `final_pytest` fails at **collection**, not
assertion:

```
/tmp/test_final_state.py:4: in <module>
    import requests
E   ModuleNotFoundError: No module named 'requests'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

The external integration test uses the `requests` HTTP client to query the
agent's service. `requests` is **not installed** in the container. The agent
tested with `urllib` (stdlib) so it never installed `requests`, and per the
TB2 playbook the verifier's test files are injected *after* the agent exits —
the agent has no way to see that the test imports `requests`.

This is a **harness deficiency**, not a model capability gap or a solution
defect: a correct, running service scores 0 purely because the grading client
library is absent from the container.

## Generalization — this is a recurring cluster, not a one-off

Grepped all 50 trajectories' `final_pytest.output_tail` for the same shape.
Three distinct tasks hard-fail at test collection with the identical
`ModuleNotFoundError: No module named 'requests'`:

- `task_000106_23215092` (data_querying) — Flask co-authorship graph API.
- `task_000958_4bb2b05d` (data_querying) — C++ HTTP microservice over SQLite.
- `task_002063_8c8adcfe` (software_engineering) — Rust/pyo3 validation service.

All three are HTTP-service tasks whose verifier queries the service with
`requests`. Different languages (Python/C++/Rust), different domains, same
root cause: the verifier depends on `requests` being importable and the
container ships without it, invisibly to the agent.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `MultiHookProcessor` (`HttpVerifierDepProcessor`) that detects
HTTP-service tasks (agent starts a server / binds a local port) and, on the
first exit-intent, injects a one-shot reminder that the external integration
test typically drives the service with the standard `requests` HTTP client —
so the agent must ensure `requests` is importable by the system Python before
finishing (e.g. `pip install requests` / `python3 -m pip install requests`),
independently of whatever client it used for its own testing.

- Tasks affected (corrective, >=2 distinct, same mechanism):
  `task_000106_23215092`, `task_000958_4bb2b05d`, `task_002063_8c8adcfe`.
- Signal: `final_pytest.output_tail` on all three ends with
  `import requests` / `ModuleNotFoundError: No module named 'requests'` /
  `Interrupted: 1 error during collection`. `initial_pytest.passed=true` and
  the agent's own endpoint tests passed — the solution is correct; only the
  grading client lib is missing.
- Verified (body-quoted):
  - `task_000106_23215092` steps: agent tests with `urllib.request`
    ("curl is not available. Let me use Python to test the API instead."),
    confirms `Author 1 response ... pagerank 0.204787` and `Got expected 404`,
    then `_tb2_self_verify` and exits. Never installs/imports `requests`.
    Final `final_pytest` = requests ImportError at collection.
  - `task_000958_4bb2b05d`: HTTP microservice task; `final_pytest` tail =
    `import requests / ModuleNotFoundError / Interrupted: 1 error during
    collection`.
  - `task_002063_8c8adcfe`: validation service task; `final_pytest` tail =
    identical requests ImportError at collection.
- Why Control not Instruction: this is a structural fact about the *verifier
  environment* (external test client lib absent from the container), not
  something the agent can learn from the task text or discover during the
  agent phase (verifier files are injected post-exit per the playbook). A
  static prompt rule would fire on every task including the many non-service
  tasks, adding noise and cost with no signal; a Control hook fires the nudge
  only when a server was actually started, exactly where it is load-bearing.
- Why Control not Action: the agent already has the only tool it will ever
  get (`Bash`) and `pip install requests` works in this environment (numpy /
  scipy pip-installs succeeded in the same trajectory). No new capability is
  missing — what is missing is the *knowledge* that the grading client lib
  must be present, delivered at the right moment. A per-call tool cannot
  express a uniform end-of-task guard.
- Retroactive check (A-corrective): yes — in all three tasks the solution
  was correct and the *only* failure was `requests` missing at test
  collection. A `pip install requests` before exit makes the test collectable;
  the already-correct assertions then run against the already-running service.
- expected_global_gain: flips the HTTP-service cluster that currently
  hard-fails on a collection-time ImportError (>=3 tasks in this 50-task
  set; this is the dominant failure shape in `data_querying`, the worst
  domain at 2/6). Generalizes to any future service task graded via
  `requests`.
- regression_risk: very low. The processor fires only when a server-start /
  port-bind command is observed in the agent's Bash history, and injects at
  most one extra user message on the first exit-intent. Non-service tasks
  (the majority) never trigger it. It never blocks a tool call, never edits
  the system prompt, never mutates existing messages — it only appends one
  message, mirroring the proven `CustomSelfVerifyProcessor` /
  `TaskTimeReminderProcessor` append pattern.
- cost_shift: negligible. One extra short user turn (+ the agent's one
  `pip install` command) on service tasks only; zero on all other tasks.

- Rollback trigger: if the next round shows the service cluster still failing
  at `requests` collection, or shows regressions on non-service tasks
  attributable to the injected turn, revert the processor.
