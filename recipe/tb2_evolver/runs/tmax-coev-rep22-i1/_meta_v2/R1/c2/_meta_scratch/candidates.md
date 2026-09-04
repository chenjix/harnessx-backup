# Candidates

## Candidate C-001
[lens: capability-gap | lever: instruction | intent: corrective]

Add a general "verification-environment robustness" rule to the system
prompt: when a task requires standing up a network service (HTTP server,
socket backend, reverse proxy) that an automated/external verifier will
exercise, ensure the common client dependencies the verifier is likely to
use are present in the container — in particular a standard Python HTTP
client library — because the verifier's test scripts run inside this same
container after the agent exits and abort at import/collection time if a
dependency is missing.

- Tasks affected: task_000028_7fe033ac, task_000958_4bb2b05d
- Signal: `final_pytest.output_tail` on both tasks shows
  `ImportError while importing test module '/tmp/test_final_state.py'` →
  `ModuleNotFoundError: No module named 'requests'` → `Interrupted: 1 error
  during collection`. Both are "build & run an HTTP service the verifier
  probes" tasks. In both, `initial_pytest.passed=true` (env was fine before
  the agent) and the agent's own service works.
- Verified (Read):
  - task_000028_7fe033ac: agent's final verification (messages step ~"final
    verification") shows `HTTP/1.1 200 OK ... 150` returned through nginx —
    the solution is functionally correct — yet `final_pytest` fails purely at
    `import requests` collection. Agent used `urllib` as a client because
    `curl`/`wget`/`requests` were all absent (`bash: line 1: curl: command
    not found`, `wget: command not found`), i.e. it never established that a
    verifier would need `requests`.
  - task_000958_4bb2b05d: builds a C++ `cpp-httplib` server on
    `127.0.0.1:9090`, `curl` probes succeed (`curl -s -H "Authorization:
    Bearer ..." http://127.0.0.1:9090/...`), server runs — but `final_pytest`
    fails at the identical `import requests` collection error. Same root
    cause, different domain (data_querying vs system_administration) and
    different port/protocol — a genuine cross-task cluster.
- Why Instruction not Control: a `MultiHookProcessor` runs in the harness
  loop, not inside the sandbox container, so it cannot `pip install` a
  package into the agent's environment; only the agent (via Bash) can. The
  gap is that the agent does not *know* the verifier's runtime needs this
  dependency — a knowledge/when gap, which is exactly the Instruction lever.
  There is no tool output to post-process (Control) and no missing action
  primitive (Action — Bash already installs packages).
- Retroactive check (A-corrective): yes — both agents produced working
  services; the only reason `reward=0` is the verifier's own `import requests`
  failing to collect. Had the agent ensured `requests` was installed while
  setting up the service, the test module would have imported and the
  functionally-correct solution would have been scored.
- expected_global_gain: flips the 2-task "verifier test module import fails"
  cluster and generalizes to any future HTTP/service task whose injected
  verifier depends on a standard client library the base image lacks.
- regression_risk: low. On non-service tasks the rule does not trigger. On
  service tasks where the dependency is already present or the network is
  blocked, `pip install requests` is a cheap no-op / harmless failure and
  does not alter the graded artifacts. Worst case: 1 extra Bash step.
- cost_shift: negligible — at most one additional `pip install` Bash call on
  service-shaped tasks; no change to the vast majority of tasks.
