# Candidates — R1 / c3

Focus (assigned): task_000106_23215092 fails.

## Diagnosis of assigned task

task_000106 (data_querying): agent was asked to build a Flask HTTP API on
127.0.0.1:8000 exposing `GET /author/<id>`. The agent built it correctly,
started it (nohup), tested every endpoint with `urllib.request`, verified 404
behaviour, and confirmed correct PageRank / H-index / coauthor output. The
solution is functionally correct. Yet `reward=0`.

Root cause is NOT the agent's solution. `final_pytest` aborted at **collection**:

    /tmp/test_final_state.py:4: in <module>
        import requests
    E   ModuleNotFoundError: No module named 'requests'
    Interrupted: 1 error during collection

The verifier's injected test module drives the running service with the
third-party `requests` library. The base image lacks `requests`; the agent
used the stdlib (`urllib`) so had no reason to install it — and the verifier
test file does not exist during the agent phase, so it cannot be inspected.
Collection aborts → every test errors at once → guaranteed reward=0 regardless
of correctness. This is a **harness deficiency**, not a model capability gap.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `VerifierDepEnsurer` control processor that, on HTTP-service tasks,
prefixes the agent's first Bash call with an idempotent, silent, best-effort
`requests` install so the verifier's `test_final_state.py` can be collected.

- Tasks affected (5 distinct failing tasks, all reward=0, same mechanism):
  task_000106_23215092 (data_querying), task_000028_7fe033ac
  (system_administration), task_000958_4bb2b05d (data_querying),
  task_001857_24daeef3 (debugging), task_002063_8c8adcfe (software_engineering).
- Signal: `final_pytest.passed=false, rc=2` with `output_tail` containing
  `ERROR collecting test_final_state.py` + `ModuleNotFoundError: No module
  named 'requests'` + `Interrupted: 1 error during collection`, while
  `initial_pytest.passed=true`. Grep over all `*.result.json` returns exactly
  these 5 tasks with `No module named 'requests'`; all are HTTP/microservice
  tasks; in none did the agent `import requests` or `pip install requests`.
- Verified (Read of task_000106.messages.json):
  - step 15-16: agent starts server with `nohup python3 coauthor_api.py &`.
  - step 18: `curl: command not found` → agent falls back to `urllib.request`.
  - steps 50-62: agent tests all endpoints via `urllib.request`, all correct;
    never touches `requests`.
  - result.json final_pytest: `import requests` collection error.
  For the other four: result.json `final_pytest.output_tail` shows the identical
  `import requests` collection abort; each task_prompt describes an HTTP
  server / microservice with a listen surface (Nginx upstream socket + service,
  C++ HTTP microservice answering queries, video stream processing service,
  data-processing pipeline launched as a service). None of the four agents used
  or installed `requests`.
- Why Control not Instruction: the agent has no *signal* that the grader's
  test imports `requests` — the test file is absent during the agent phase and
  the agent's own verification with `urllib`/`curl` is entirely valid. A prompt
  rule ("install requests") would be task-specific domain knowledge injected
  into the system prompt (against SOUL policy) and would still fire
  unreliably — the agent already believes it has verified correctly. A
  deterministic mechanical guard that ensures the dependency exists is the
  right layer. Not Action: no new agent capability is needed; the container
  already has pip + network (numpy/scipy were fetched on demand in this run).
- Why scoped (arm only HTTP-service tasks): a global `pip install requests`
  on every task would add cost/noise on non-service tasks and slightly widen
  regression surface. The `_SERVICE_RE ∧ _LISTEN_RE` gate arms only tasks
  whose description names an HTTP/REST server AND a concrete endpoint/port,
  matching exactly the failing cluster's shape.
- Retroactive check (A-corrective): yes — if `requests` had been importable in
  the container at agent-exit on these 5 tasks, `test_final_state.py` would
  have *collected* and run against the running services (which the agents left
  alive), instead of aborting at import. task_000106's solution was already
  correct, so it flips to a real pass; the other four at minimum get scored on
  merit instead of a guaranteed collection-abort 0.
- expected_global_gain: unblocks a 5-task cluster spanning 5 domains that is
  currently guaranteed-0 for a purely infrastructural reason; generalizes to
  any future HTTP-service task whose verifier imports `requests`.
- regression_risk: very low. The guard is `|| true`-terminated and runs the
  agent's original command unchanged after `;`, so it cannot break a command;
  it is a no-op when `requests` already imports; it fires at most once per task
  and only on armed (HTTP-service) tasks, so non-service passing clusters are
  untouched. Worst case on an offline image: the `pip install` silently fails
  and the task is exactly as it is today.
- cost_shift: negligible. One extra `python3 -c 'import requests'` probe (and,
  once, a quiet pip install of a small pure-Python-ish wheel) prepended to a
  single Bash call on armed tasks only. No extra model turns.

Rollback trigger: if post-flight replay or the next round shows any regression
on a previously-passing HTTP-service task attributable to the prepended guard,
revert this processor.
