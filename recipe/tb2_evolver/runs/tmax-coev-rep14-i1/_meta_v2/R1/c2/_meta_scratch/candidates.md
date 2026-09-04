# Candidates

## Candidate C-001
[lens: capability-gap | lever: instruction | intent: corrective]

Add a general "verifier-environment readiness" rule to `system_prompt.txt`:
on tasks that build/run a network service an automated grader will probe,
ensure the standard verification client (Python `requests`, plus curl/wget
awareness) is importable in the container before finishing.

- Tasks affected: task_000028_7fe033ac, task_000958_4bb2b05d
- Signal: `final_pytest.output_tail` on both tasks is a pytest **collection
  error**, not an assertion failure: `ImportError while importing test module
  '/tmp/test_final_state.py' ... import requests ... ModuleNotFoundError: No
  module named 'requests'`. Both are HTTP-service tasks (nginx+C++ socket
  server; C++ HTTP microservice on 127.0.0.1:9090). `initial_pytest.passed=true`
  on both — the environment was fine until the grader's own `import requests`.
- Verified (Read):
  - task_000028 messages step ~10 body: agent's own probe shows the service
    works — `HTTP/1.1 200 OK ... Content-Length: 3 ... 150`; final assistant
    turn confirms all 5 objectives met. The agent also hit `bash: curl:
    command not found` and `bash: wget: command not found`, proving the
    container lacks common HTTP clients — yet never checked/installed the
    Python `requests` module the grader needs. The ONLY reason reward=0 is the
    grader's collection-time `ModuleNotFoundError: No module named 'requests'`.
  - task_000958 result: identical grader collection error
    (`/tmp/test_final_state.py:4: import requests → ModuleNotFoundError`). Same
    verifier mechanism. (Note: this task also has an independent functional
    bug — its C++ service returns HTTP 500 and the agent looped to
    budget_exceeded — so requests-readiness alone will not flip it, but the
    shared verifier-dependency blocker is real and recurring.)
- Why Instruction not Control/Action: the capability is already present — the
  agent has `Bash` and can run `pip install requests` at will; it simply did
  not know that a service-grader needs `requests` importable in the container.
  A Control processor cannot run sandbox Bash inside the model loop to install
  packages, and no new tool is needed (Bash already covers install). This is
  the textbook Instruction cell: capability + control are fine, the agent
  lacks the *when/under-what-condition* knowledge. The rule is phrased as a
  general strategy (any service+grader task), embeds no task IDs, paths, or
  constants, and passes the generalization test.
- Retroactive check (A-corrective): yes for task_000028 — its solution was
  functionally complete and passed the agent's own end-to-end HTTP probe; had
  the agent ensured `import requests` worked before exiting, the grader would
  have collected and run its assertions against a correct service. Partial for
  task_000958 — removes the shared collection blocker but that task has an
  additional independent 500-error bug outside this candidate's scope.
- expected_global_gain: flips the "verifier collection ImportError on
  service tasks" cluster (>=2 tasks share the exact mechanism); generalizes to
  any future task where a Python grader imports `requests`/an HTTP client.
- regression_risk: low. On non-service tasks the added paragraph is inert
  guidance the model can ignore. Worst case a couple of extra Bash calls
  (`python3 -c "import requests"` / a `pip install`) on service tasks; no
  change to processor pipeline, tools, or config knobs.
- cost_shift: negligible — a few extra Bash calls only on service-shaped
  tasks; system prompt grows by ~1 short paragraph (~120 tokens/turn).
