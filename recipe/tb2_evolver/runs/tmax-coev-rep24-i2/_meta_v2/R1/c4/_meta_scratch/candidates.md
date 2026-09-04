# Candidates — R1 / c4

Assigned focus: `task_000106_23215092` (data_querying) fails.

## Diagnosis of the assigned task

The agent solved the task correctly: it fixed the cross-join SQL, built the
co-authorship graph, computed PageRank, and stood up a working Flask API on
`127.0.0.1:8000` that returned the exact required JSON shape (verified in the
trajectory via `urllib` probes, incl. the 404 case). Yet `final_pytest`
failed with:

```
/tmp/test_final_state.py:4: in <module>
    import requests
E   ModuleNotFoundError: No module named 'requests'
Interrupted: 1 error during collection
```

The **verifier's** test file imports `requests`, but `requests` is not
installed in the image, so pytest errors at *collection* — the correct
service is never even exercised. The agent had no way to know this: TB2's
verifier test files are injected *after* the agent exits (playbook: "not
present during execution"), and the agent probed its own service with
`urllib`/`curl` (both preinstalled), so it never surfaced the missing dep.

This is a **harness deficiency**, not a capability gap: internet/pip works
during the agent phase (the same trajectory successfully `pip install`ed
numpy and scipy), so a mechanical "ensure `requests` is present" step closes
it. It is not task-specific: it applies to any task that stands up an HTTP
service and is graded by an external client.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `GraderClientDepProcessor` that, once per task and only when the agent
has run a command matching an HTTP-service signature, injects a single
idempotent `pip install requests` Bash call at exit-intent so the verifier's
`import requests` succeeds.

- Tasks affected (all reward=0, `final_pytest` ModuleNotFoundError 'requests'):
  task_000106_23215092, task_000809_760d7fa0, task_000958_4bb2b05d,
  task_001857_24daeef3, task_000939_1592be48, task_002063_8c8adcfe,
  task_000028_7fe033ac  (7 of 31 failures)
- Signal: `final_pytest.output_tail` contains
  `ModuleNotFoundError: No module named 'requests'` and pytest reports
  "Interrupted: N error during collection" — the tests never run. All 7
  tasks' user prompts require standing up an HTTP service/microservice.
- Verified (Read):
  - task_000106 result.json final_pytest tail = the requests ImportError
    above; messages steps 21-46 show a *working* Flask service (`GET
    /author/<id>` returns the exact required JSON, 404 handled) probed via
    `urllib`. Agent never installs `requests`.
  - task_000809 / 000958 / 001857 / 000939 / 002063 / 000028: each
    result.json final_pytest tail = same `No module named 'requests'`; each
    user prompt asks for an HTTP service ("write a Python HTTP service",
    "implement a C++ HTTP microservice", "deploy it as a network service",
    nginx reverse-proxy upstream). Grep over messages confirms none of the 7
    ever runs `pip install requests`; all probe with `curl`/`urllib`.
- Why Control not Instruction: a prompt rule ("remember graders use
  requests") relies on the model both believing and acting on it, and would
  inject dataset-specific grader knowledge into the system prompt (against
  the evolution philosophy). The mechanical fix — install the lib when a
  service is detected — is deterministic, fires uniformly across the cluster,
  and needs no per-task reasoning. That is squarely Control (a cross-task
  `on_after_model` guard), matching the existing `CustomSelfVerifyProcessor`
  shape.
- Why Control not Configuration: no existing knob controls dependency
  provisioning; this is a missing mechanical step, not a mistuned one.
- Retroactive check (A-corrective): yes. On task_000106 the service was
  already correct; the *only* thing between it and a pass was pytest being
  able to `import requests`. Installing `requests` before exit makes
  collection succeed and the correct service is then graded. Same logic holds
  for the other 6 (service built, blocked solely at test collection on the
  requests import). For any where the underlying service is *also* wrong the
  install is a harmless no-op — it removes the collection blocker without
  masking a real defect.
- expected_global_gain: up to 7 failing tasks (all HTTP-service tasks whose
  grader imports `requests`) flip once collection succeeds; generalizes to
  any future service task graded via `requests`.
- regression_risk: very low. Fires at most once, only after a service
  signature is seen; `pip install requests` is idempotent and offline-safe
  (guarded no-op). Cannot corrupt a passing task's outputs — it only adds a
  Python package. Worst case on a non-service task: never fires (guarded).
- cost_shift: +1 short Bash step (~a few seconds, tiny token cost) only on
  tasks that stand up a service; zero on all others.

## Rejected alternatives

- Prompt-only nudge (Instruction): rejected — injects grader-specific
  knowledge into the system prompt and is non-deterministic. See C-001
  "Why Control not Instruction".
- New tool (Action): rejected — the agent already has Bash; the gap is a
  missing mechanical step, not a missing action.
