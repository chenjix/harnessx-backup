# Candidates — R1 (_meta_v2)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `VerifierDepEnsurer` processor that, on HTTP-service tasks, prepends a
one-time idempotent silent `pip install requests` guard to the agent's first
Bash call, so the external verifier's `test_final_state.py` can be collected.

- Tasks affected (7 distinct failing tasks, 6 domains):
  task_000028_7fe033ac (system_administration),
  task_000106_23215092 (data_querying),
  task_000809_760d7fa0 (data_processing),
  task_000939_1592be48 (software_engineering),
  task_000958_4bb2b05d (data_querying),
  task_001857_24daeef3 (debugging),
  task_002063_8c8adcfe (software_engineering).
- Signal: `final_pytest.output_tail` on all 7 is identical in shape —
  `ModuleNotFoundError: No module named 'requests'` followed by
  `!!! Interrupted: 1 error during collection !!!` and `1 error`. i.e. ZERO
  tests ran; the module aborted at import time on `import requests`. Regex over
  all failing result.json confirms `requests` is the only missing module and it
  recurs on exactly these 7 tasks. Each task's `task_description` names an HTTP
  server + endpoint/port (verified below).
- Verified (Read, trajectory bodies):
  - task_000809 first user message: "Write and start a Python HTTP server
    listening on `127.0.0.1:8000`. It must expose the endpoint
    `GET /query?t=<seconds>`". Agent built `/app/server.py` (stdlib
    `http.server`) and self-tested it with `urllib.request.urlopen(...)` — it
    NEVER imports/installs `requests`; its own tests pass. Verifier tail:
    `import requests -> ModuleNotFoundError -> Interrupted: 1 error during
    collection`.
  - task_000106 first user message names a "Co-authorship graph API service"
    (Flask); agent ran `pip3 install flask`/`numpy`/`scipy` successfully
    (proves pip works here) but never `requests`. Verifier tail: same
    `import requests` collection error.
  - task_002063 built `/home/user/pr-review/server.py` ("CRC Verification
    Service"); verifier tail: same `import requests` collection error.
  - task_000939 built `/home/user/deploy_server.py` (Deployment Token REST
    API); ran `pip3 install flask` OK; verifier tail: same collection error.
  - Environment fact (verified across tasks): `pip3 install {numpy, scipy,
    flask, SpeechRecognition, pydub}` all downloaded + installed successfully in
    R0 — the sandbox HAS outbound network, so installing `requests` is viable
    (contradicts the generic tb2-playbook "internet blocked" prior for THIS
    environment).
- Why Control not Instruction: the agent's behavior is already correct — it
  writes a working server and verifies it with the stdlib. Telling it in the
  prompt to `pip install requests` would (a) embed a task-specific literal, (b)
  rely on the model choosing to obey, and (c) be pointless for its own testing
  (urllib works). The gap is purely environmental — a dependency the *hidden
  verifier* needs — so a mechanical hook that guarantees the dependency
  regardless of agent cooperation is the correct, narrower fix. It is not
  Action (no new agent capability is required; `Bash` already exists) and not
  Configuration (no existing knob controls environment dependencies).
- Retroactive check (A-corrective): yes (partial-to-full). If `requests` had
  been importable, `test_final_state.py` would COLLECT and run instead of
  erroring out globally at import. On the `exit_reason=done` tasks (28, 106,
  809, 939, 2063) the agent self-verified a working server, so the collected
  tests plausibly pass — a real flip. On the two `budget_exceeded` tasks (958,
  1857) the server may be incomplete, so those are upside, not guaranteed. Even
  a subset flipping is a clear net gain; the fix removes a hard collection wall
  that masked otherwise-correct solutions.
- expected_global_gain: flips a 7-task cluster (14% of the benchmark) that is
  currently blocked by a single verifier-dependency collection error, not by
  solution quality. Generalizes to ANY future HTTP-service task verified via
  `requests`.
- regression_risk: Very low. The processor arms ONLY when the task description
  matches both an HTTP-service pattern AND an endpoint/port/listen pattern;
  non-service tasks are never touched. On armed tasks the guard runs once,
  short-circuits to a no-op when `requests` already imports, and is fully
  `|| true`-terminated so it cannot abort the agent's own command or change its
  stdout/stderr. Worst realistic case is one extra ~1s pip call on a service
  task that already had `requests`.
- cost_shift: Negligible. At most one extra idempotent shell prefix per armed
  task; zero model tokens added (no message-history mutation).
