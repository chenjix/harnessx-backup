# Candidates — R4

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `VerifierDepGuardProcessor` that, at the agent's exit-intent turn,
injects a real `Bash` tool call ensuring common verifier test-harness
libraries (`requests`, `pyyaml`) are importable in the container — pip-
installing only the ones missing.

- Tasks affected: task_000028_7fe033ac, task_000958_4bb2b05d (both STUCK 0000
  across R0–R3; same mechanism).
- Signal: both `final_pytest` tails end with
  `ModuleNotFoundError: No module named 'requests'` →
  `ERROR collecting test_final_state.py` → `Interrupted: 1 error during
  collection`. `initial_pytest` PASSES for both (initial-state tests don't
  import requests), proving the agent + verifier share the same container
  Python and the module is simply absent. Both are HTTP-microservice tasks
  whose verifier probes the endpoint via `requests`.
- Verified (Read):
  - task_000028 last tool turn: `=== Testing HTTP endpoint === HTTP/1.1 200 OK
    Content-Length: 3  150` — the C++ server is built, listening on the socket
    and returning the correct frame count (150); nginx master/worker running;
    logrotate.conf correct. The agent's work is complete; the ONLY blocker is
    the verifier's `import requests` collection error (result.json
    final_pytest: `ImportError while importing test module
    '/tmp/test_final_state.py' ... No module named 'requests'`).
  - task_000958 task text: "Write a C++ HTTP server listening exactly on
    127.0.0.1:9090 ... require an Authorization header ..."; result.json
    final_pytest: identical `ModuleNotFoundError: No module named 'requests'`
    collection error.
  - Network/pip proven available in this environment: task_000684 tool#10
    `Collecting numpy==1.26.0  Downloading numpy-1.26.0-...whl (18.2 MB) ...
    58.3 MB/s` — a real PyPI download succeeded, so `pip install requests`
    will succeed at exit time. (Prior rounds' "internet blocked" assumption is
    contradicted by this trajectory.)
- Why Control not Instruction: the agent cannot infer this dependency — the
  task descriptions never mention `requests`; it is a hidden property of the
  verifier module, not of the task. A prompt rule telling the agent to
  "install requests" would be task-specific domain knowledge injected into the
  system prompt (forbidden) and the agent has demonstrably narrated past
  textual reminders (R1/R2/R3 evidence). A mechanical Bash tool call the run
  loop executes cannot be narrated past and requires no domain knowledge from
  the model. This is the "verifier needs dynamic context the agent can't
  infer" harness-deficiency shape.
- Why Control not Action: no new action space is needed — `Bash` already
  exists and pip works; the gap is a missing mechanical guarantee at exit, not
  a missing capability.
- Retroactive check (A-corrective): yes — both tasks' agent-side work is
  complete and correct (000028 endpoint returns the right body; 000958 builds
  the required server). The sole failure is a collection-time ImportError on a
  common test lib; if `requests` had been importable when the verifier ran,
  pytest would have collected and executed the tests against the already-
  correct final state.
- expected_global_gain: flips the "verifier imports a common test lib absent
  from the image" cluster (2 STUCK HTTP-service tasks) and generalizes to any
  future HTTP-service task whose verifier probes via `requests`/`pyyaml`.
- regression_risk: near-zero. The injected command imports each module first
  and only pip-installs when missing; installing an already-present package is
  a no-op, so every currently-passing task is unaffected. All shell steps are
  `|| true`-guarded so the tool call cannot error the run. Fires at most once
  per run, only at exit intent (a state passing tasks reach cleanly). It runs
  after the lifecycle self-verify processor (order 95 > 90), so it never
  collides with that processor's exit-intent snapshot — if lifecycle fires
  first, this sees a non-exit turn and fires on the next exit-intent turn.
- cost_shift: +1 Bash round-trip and one short user turn only on runs reaching
  exit intent; the dep-check output is tiny and bounded. Negligible.
