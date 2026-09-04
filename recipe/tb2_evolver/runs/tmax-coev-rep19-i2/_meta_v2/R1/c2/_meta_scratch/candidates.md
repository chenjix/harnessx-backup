# Candidates — R1/c2

Assigned focus: `task_000106_23215092` fails.

## Diagnosis of task_000106_23215092

The agent's solution was **functionally correct**: it fixed the SQL self-join,
computed PageRank with networkx (installing numpy+scipy on the fly), built a
Flask API on `127.0.0.1:8000`, and verified every endpoint in-session (author
lookups, 404 on unknown author, correct coauthor sort). `result.json` shows
`reward=0` but the `final_pytest` failure is:

```
ImportError while importing test module '/tmp/test_final_state.py'
ModuleNotFoundError: No module named 'requests'
Interrupted: 1 error during collection   (rc=2)
```

The *verifier's own* test module failed to load — a **collection-time**
ImportError, before any assertion ran. The agent used Flask/urllib for its own
code, so it never needed `requests` and never installed it. The verifier phase
runs against the container's final site-packages (tb2-playbook "Sandbox
topology") and queries the live service with `requests`; with `requests`
absent, every test errors and the task scores 0 regardless of correctness.
This is a **harness deficiency**, not a model capability gap: the agent cannot
read the verifier's test files (injected after the session) and has no way to
discover the client-library dependency.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a one-shot `ServiceClientDepGuard` processor that, on tasks whose prompt
signals a *live network service left running for out-of-process verification*,
injects a single directive telling the agent to make the Python `requests`
library importable in the environment before it stops (install if absent).

- Tasks affected (>=2 distinct, same mechanism): `task_000106_23215092`,
  `task_000028_7fe033ac`, `task_000809_760d7fa0`, `task_000958_4bb2b05d`,
  `task_001857_24daeef3`, `task_002063_8c8adcfe` — **6 failing tasks**, all
  with the identical `final_pytest` collection error `ModuleNotFoundError: No
  module named 'requests'` (rc=2).
- Signal: `result.json.final_pytest.output_tail` on all six contains
  `ImportError while importing test module '/tmp/test_final_state.py'` →
  `ModuleNotFoundError: No module named 'requests'` → `Interrupted: 1 error
  during collection`. All six task prompts require building/leaving a network
  service running (HTTP/socket on a fixed loopback port).
- Verified (body-quoted):
  - `task_000106_23215092` — task: "Create a Python HTTP API ... listens on
    `127.0.0.1:8000` ... Leave your API running ... so that our automated
    integration tests can query it." Agent built a working Flask API, verified
    all endpoints in-session with `urllib`; never installed `requests`.
    Verifier: `ModuleNotFoundError: No module named 'requests'`.
  - `task_000028_7fe033ac` — task: C++ HTTP socket server on
    `/tmp/video_backend.sock`, "Run it in the background". Agent wrote C++ (no
    Python deps). Verifier: same `requests` collection error.
  - `task_000958_4bb2b05d` — task: "Write a C++ HTTP server listening exactly
    on `127.0.0.1:9090`". Verifier: same `requests` collection error.
  - `task_001857_24daeef3` — task: C++ service, "run the service in the
    background ... listen on `127.0.0.1:8080`". Verifier: same error.
  - `task_002063_8c8adcfe` — task: "The service must listen on
    `127.0.0.1:8080`". Verifier: same error.
  - `task_000809_760d7fa0` — task: "Write and start a Python HTTP server
    listening on `127.0.0.1:8000`". Verifier: same error.
- Why Control not Instruction: the agent cannot *learn* this from the task
  text — the dependency belongs to verifier files it never sees, and no static
  system-prompt rule can be justified as general model knowledge ("the grader
  uses requests" is harness-internal, not task strategy). The nudge must be
  injected *conditionally* (only for the service-task shape) and *once*, keyed
  on runtime task content — a mechanical hook, exactly Control. A blanket
  system-prompt line would fire on every non-service task too and edges into
  encoding harness internals into the prompt.
- Why Control not Action: no new agent action is missing — `Bash`/`pip` already
  exist. The gap is that the agent lacks the *knowledge that the verifier needs
  a specific client lib*; a processor delivers that knowledge at the right
  moment without expanding the action space.
- Retroactive check (A-corrective): **yes** — for all 6, the agent's service
  was built and running; the only blocker was the missing `requests` at
  verifier collection time. Had the agent ensured `requests` importable (the
  nudge's instruction), the test module would collect and the graded
  assertions (which the agent's correct services satisfy in-session) would run.
- expected_global_gain: flips up to 6 failing service tasks that are otherwise
  correct; generalizes to the whole "leave an HTTP/socket service running for
  external verification" class, where the verifier client lib is a structural
  requirement invisible to the agent.
- regression_risk: LOW. Trigger requires BOTH a service-bind signal AND a
  run/leave-running signal, so it does not fire on non-service tasks. On the
  ~9 service tasks that already pass or fail for other reasons (e.g.
  task_000140/378/1035/1937 fail on real assertion mismatches with `requests`
  already present), the nudge is idempotent: it checks `import requests`, does
  nothing if present, and explicitly warns against removing a working install.
  No message-contract risk (single user-message append, mirrors
  StepBudgetVerifyProcessor; contract auto-check passes).
- cost_shift: small positive. One injected user message + at most one
  `python3 -c 'import requests'` check and an occasional `pip install requests`
  on the fraction of service tasks lacking it. Negligible vs. 6 potential flips.
