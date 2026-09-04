# Evolve journal — tmax-coev-rep13-i2

## Round 1 — ensure verifier `requests` dep

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_verifier_requests_dep_v1
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000106_23215092, task_000809_760d7fa0, task_000939_1592be48, task_000958_4bb2b05d, task_001857_24daeef3, task_002063_8c8adcfe]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=22/50; +4/-0 gained=task_000106_23215092,task_000809_760d7fa0,task_000939_1592be48,task_001832_dd672877; score 0.4400 >= incumbent(mean) 0.3600 - tol 0.0400
expected_global_gain: "Flips a 7-task HTTP-service cluster (14% of benchmark) blocked purely by the verifier's `import requests` collection error, not by solution quality"
regression_risk: "Processor arms only on HTTP-service task descriptions; on other tasks it is a strict no-op. On armed tasks the guard is idempotent, silent, once-per-task, `|| true`-terminated so it can never abort the agent command"
cost_shift: "Negligible — at most one extra idempotent shell prefix per armed task; zero added model tokens"
rollback_trigger: "If none of the 7 predicted tasks flip AND any previously-passing service task regresses (e.g. guard interferes with a server-start command), revert the VerifierDepEnsurer processor"
-->

### Why

The single largest failing cluster in R0 (7 of 32 failures, spanning 6
domains) is "build/start an HTTP server exposing endpoint X on port Y" tasks
where the external verifier's `test_final_state.py` begins with `import
requests`. The base image lacks `requests`, so pytest aborts at COLLECTION
time (`ModuleNotFoundError: No module named 'requests'` ->
`Interrupted: 1 error during collection`) and every test in the module errors
at once — including tests the agent's solution would have passed. The agent
cannot anticipate this: it self-tests its server with `urllib.request`/`curl`
(no `requests` needed) and the verifier's test file does not exist during the
agent phase. This is a harness/environment deficiency, not a model capability
gap. Crucially, pip works in this environment (other R0 tasks successfully
downloaded numpy/scipy/flask/SpeechRecognition), so ensuring the dependency is
viable — the generic tb2-playbook "internet blocked" prior does not hold here.

### Changes

- `processors/verifier_dep_ensurer.py` — new `VerifierDepEnsurer`
  MultiHookProcessor. Arms on HTTP-service task descriptions; on the first
  approved Bash call prepends a one-time idempotent silent
  `python3 -c 'import requests' || pip install -q requests || true ;` guard.
- `config.yaml` — register `VerifierDepEnsurer` early in the pipeline (order
  10, before other before-tool processors). DeadLoopBreaker + system_prompt.txt
  retained unchanged and vendored under this round's output_dir.

### Evidence

- `task_000809_760d7fa0` first user msg: "Write and start a Python HTTP server
  listening on `127.0.0.1:8000` ... endpoint `GET /query?t=<seconds>`". Agent
  built `/app/server.py` and self-tested with `urllib.request.urlopen(...)`;
  never installs `requests`. Verifier tail: `import requests ->
  ModuleNotFoundError -> Interrupted: 1 error during collection`.
- `task_000106_23215092`: Flask "Co-authorship graph API service"; ran
  `pip3 install flask/numpy/scipy` OK (pip works) but not `requests`; same
  collection error.
- `task_002063_8c8adcfe`, `task_000939_1592be48`, `task_000028_7fe033ac`,
  `task_000958_4bb2b05d`, `task_001857_24daeef3`: all HTTP-server tasks, all
  fail with the identical `import requests` collection error. Regex over all
  failing result.json confirms `requests` is the ONLY missing module and recurs
  on exactly these 7 tasks.

### Uncertainty

The two `budget_exceeded` tasks (958, 1857) may have incomplete servers, so
they are upside rather than guaranteed flips. If the verifier for some service
tasks also imports other third-party libs, a subset may still error at
collection on a *different* module — in that case the next round should
generalize the guard to a small set of common verifier deps, or read the
per-module collection error to widen it. Rollback if zero of the 7 flip and any
passing service task regresses.
