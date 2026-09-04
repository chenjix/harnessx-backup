# Candidates

## Candidate C-002
[lens: capability-gap | lever: instruction | intent: corrective]

Add a "verifier-readiness" rule to the system prompt: the automated
verifier runs Python-based checks against the final container state
after you exit, so before finishing, ensure the container's Python
environment can import the common third-party libraries such checks
typically rely on (e.g. an HTTP client) — install any that are
missing.

- Tasks affected: task_000028_7fe033ac, task_000958_4bb2b05d
- Signal: `final_pytest.rc=2` with
  `ModuleNotFoundError: No module named 'requests'` raised at pytest
  *collection* time (verifier's `test_final_state.py` `import requests`
  fails). Both tasks are HTTP/network-service tasks whose description
  says an "automated verifier will make HTTP requests". `reward=0`
  despite the agent's endpoint working.
- Verified (Read):
  - task_000028_7fe033ac `result.json` final_pytest output_tail:
    `/tmp/test_final_state.py:6: in <module> import requests` →
    `E ModuleNotFoundError: No module named 'requests'` →
    `Interrupted: 1 error during collection`. In the trajectory the
    agent's own probe (msg tool_call `chatcmpl-tool-92c8085f752ab704`)
    returned `HTTP/1.1 200 OK ... 150` — the service WAS correct; the
    verifier simply could not import its test dependency.
  - task_000958_4bb2b05d `result.json` final_pytest output_tail:
    `/tmp/test_final_state.py:4: in <module> import requests` →
    `E ModuleNotFoundError: No module named 'requests'` →
    `Interrupted: 1 error during collection`.
  - Cross-check that installs work offline in these containers:
    task_000956_7e92337f trajectory shows
    `Installing collected packages: python-Levenshtein\nSuccessful...`
    and task_001031_a8f0eb37 shows a biopython wheel `Downloading`
    succeeding — so `pip install requests` is reachable at runtime.
- Why Instruction not Control: a `MultiHookProcessor` cannot run Bash
  inside the task container, and the verifier's test files are injected
  only AFTER the agent exits (tb2-playbook: "verifier test files are
  injected after the agent session ends and are not present during
  execution"), so no processor can detect the missing import or install
  the package. Only the agent, via its single `Bash` tool, can
  `pip install`. The gap is therefore knowledge of *when* to ensure
  verifier dependencies exist — an Instruction rule, not a mechanical
  hook. Also not Action: the agent already has `Bash`; it lacks the
  habit, not the capability.
- Retroactive check (A-corrective): yes for task_000028 — the agent's
  endpoint already returned the correct body and both services were
  left running; had `requests` been importable, the verifier's test
  module would have collected and the HTTP assertions would have passed
  on the already-correct service. Partial for task_000958 — it also hit
  `budget_exceeded`, so the underlying work may be incomplete, but the
  collection crash is an independent hard blocker that this rule removes;
  worst case it stays failed at the same reward.
- expected_global_gain: closes a recurring failure mode across the
  HTTP/network-service cluster where the agent's work is correct but the
  verifier's Python test module can't be collected. Generalizes to any
  task whose verifier imports a common third-party lib not preinstalled;
  strongest confirmed flip is task_000028.
- regression_risk: low. The rule is a best-effort, idempotent readiness
  step ("install if missing"); if the container is offline the install
  is a no-op and the agent proceeds. It adds a few Bash calls at most.
  It does not alter the agent's core task logic and cannot break a task
  whose verifier had no missing deps. Small risk: agent over-installs and
  spends a couple extra steps — bounded and cheap.
- cost_shift: mildly positive (small increase) — 1-3 extra short Bash
  calls near the end of HTTP/service tasks; negligible token cost, no
  new model round-trips beyond the agent's own tool loop.
- rollback_trigger: if any previously-passing task regresses (e.g. an
  install command errors and the agent loops on it) or global pass_rate
  drops, revert the prompt to the R1 sibling.
