# Candidates — R2

## Candidate C-002
[lens: failure | lever: instruction | intent: corrective]

Add a general "verify network services end-to-end with a Python `requests`
client (installing it if missing)" strategy paragraph to the sibling
`system_prompt.txt`, applied to the class of tasks that build/run an
HTTP/network service.

- Tasks affected: task_000958_4bb2b05d (data_querying), task_000028_7fe033ac
  (system_administration). Both build a C++ HTTP microservice that an external
  verifier probes with the Python `requests` library.
- Signal: `final_pytest.output_tail` on both tasks ends with
  `ModuleNotFoundError: No module named 'requests'` →
  `Interrupted: 1 error during collection` (rc=2). The verifier's test module
  fails to *import* before a single assertion runs, so every test errors out
  regardless of whether the agent's service is correct. This is the ONLY
  recurring structural verifier-import crash in the round (grep over all
  failing result.json: exactly these two tasks).
- Verified (Read):
  - task_000958 final assistant turns: agent built the C++ server, it was
    running (pid 739) and responded to `GET /chain?id=1` with `[1,2,3,4]`
    (a correct-looking lineage) — yet `final_pytest` reports rc=2, zero tests
    run, killed on `import requests`. The agent verified with a non-`requests`
    client and never installed `requests`, so the shared-container verifier
    could not even collect its tests.
  - task_000028 final assistant turns: agent's summary explicitly notes
    "curl and wget were not available... nc was not available... Python
    urllib failed", then it finally tested via a Python client showing
    `Status: 200 Body: b'150\n'`. Verifier again dies on `import requests`
    (rc=2). (028 also has a genuine accuracy bug — frame count 150 — so it is
    not expected to flip on this change alone; it is cited only as the second
    instance of the same import-crash mechanism.)
- Why Instruction not Control: only the agent's `Bash` tool can `pip install`
  into the shared container; a `MultiHookProcessor` runs inside the harness
  process and cannot mutate the sandbox's Python environment, so no Control
  hook can make `requests` importable for the verifier. The gap is that the
  agent does not *know* to reach for `requests` when self-testing a service —
  a knowledge/timing gap, which is the Instruction lever's domain.
- Why not Configuration: no existing processor knob governs which client the
  agent uses to test a service.
- Retroactive check (A-corrective): partial-yes. task_000958's service was
  built and responding correctly at exit; had the agent tested it with a
  `requests`-based client it would have `pip install requests`, the verifier's
  import would then succeed, and its (apparently correct) endpoints could pass
  — plausibly flipping 958 to reward=1. task_000028 would still fail on the
  frame-count accuracy bug even with the import fixed, so it is NOT claimed as
  a flip — only as corroborating evidence that the import-crash mechanism is
  systemic, not a one-off.
- expected_global_gain: rescues the "external verifier probes the agent's
  network service with `requests`, but `requests` is absent → whole test
  module errors at collection" failure shape. Generalizes to any current or
  future HTTP/socket-service task in the benchmark (multiple domains already
  produce such tasks: data_querying, system_administration). Turning a
  zero-tests-collected crash into a normally-graded run is strictly
  non-negative for correct solutions.
- regression_risk: very low. The added guidance only fires the agent's own
  judgement on tasks that run a service; it appends a verification step and a
  benign `pip install requests`. Non-service tasks (the majority, all already
  passing clusters) see an extra paragraph they will not act on. No processor
  pipeline change, so no mechanical regression surface. The paragraph is a
  general strategy — no task IDs, constants, ports, or paths from the training
  tasks are embedded.
- cost_shift: negligible. Adds at most one `pip install requests` and a couple
  of `requests`-based test calls on the small subset of service tasks; no
  effect elsewhere. Net token movement < 1% expected.
