# Candidates — R1/c2

Assigned focus: `task_000028_7fe033ac` fails. Diagnose and fix the harness
capability the failure exposes.

## Diagnosis

`task_000028` is an HTTP-service task (nginx reverse proxy + C++ backend on a
unix socket, probed at `http://127.0.0.1:8080/`). The agent's work was
**correct**: the trajectory's last steps show `HTTP/1.1 200 OK`,
`Content-Length: 4`, body `150`, nginx running, server process alive. Yet
`reward=0`.

The reason is in `final_pytest.output_tail`:

```
/tmp/test_final_state.py:6: in <module>
    import requests
E   ModuleNotFoundError: No module named 'requests'
Interrupted: 1 error during collection
```

The external verifier's pytest module `import requests` at top level and the
container has no `requests`, so pytest fails at **collection** — the agent's
correct service is never even exercised. This is a *verifier-side* dependency
the agent cannot know about (it is never named in the task, and the base
container ships without it — the agent even had no `curl` and fell back to
`/dev/tcp`). Structural harness mismatch, not a model capability gap.

Confirmed generalisable: a sweep of all 50 R0 trajectories found **exactly the
same failure shape on a second, mechanistically-distinct task**
`task_000958_4bb2b05d` (a C++ SQLite-backed HTTP microservice on
`127.0.0.1:9090`). Same `import requests` collection error, `reward=0`.

Feasibility confirmed: `pip install` reaches PyPI in this sandbox — in
`task_000684` the agent ran `pip install numpy==1.26.0 scipy==1.11.4` and both
wheels **downloaded successfully** (18.2 MB @ 57 MB/s). So making `requests`
importable is achievable; the playbook's "internet blocked" prior does not
hold for this environment.

## Candidate C-001
[lens: capability-gap | lever: control | intent: corrective]

Add a `VerifierDepGuard` processor that, for HTTP/network-service tasks,
injects one idempotent best-effort `pip install requests` Bash call when the
model first tries to exit — so a downstream `requests`-based pytest verifier
can import and run.

- Tasks affected: `task_000028_7fe033ac`, `task_000958_4bb2b05d` (two distinct
  tasks — nginx+C++ frame counter vs C++ SQLite REST service — same root
  cause: verifier `import requests` fails at collection).
- Signal: `final_pytest.output_tail` on both tasks =
  `ModuleNotFoundError: No module named 'requests'` at
  `test_final_state.py` module import → `Interrupted: 1 error during
  collection`. `reward=0` on both. Agent-side work otherwise correct.
- Verified (Read):
  - `task_000028` final trajectory steps: agent probes `/dev/tcp/127.0.0.1/8080`
    and gets `HTTP/1.1 200 OK ... 150` (service correct); no `pip`/`requests`
    command anywhere in its 70 steps. Verifier tail = requests ImportError.
  - `task_000958` (55 steps, `exit_reason=done`): zero `pip`/`requests`
    commands; verifier tail = the identical requests ImportError.
  - `task_000684` step (evidence pip works): `pip install numpy==1.26.0
    scipy==1.11.4` → "Downloading numpy-1.26.0-...whl (18.2 MB) ... 57.7 MB/s".
- Why Control not Instruction: the gap is not knowledge the agent can act on —
  the verifier's `requests` dependency is never stated in the task and is not
  inferable from the description. A prompt rule telling the agent to
  "install requests for the grader" would be (a) task-class-specific guessing
  the agent has no basis for, and (b) unreliable (the model may skip it under
  time pressure). A mechanical hook guarantees the provisioning fires exactly
  when HTTP-service signals are present, at the deterministic exit boundary,
  once per task.
- Why Control not Action: no new agent-facing capability is needed — `Bash`
  already exists and TB2 forbids adding tools. The fix is a guaranteed
  mechanical step at the loop boundary, which is precisely a processor's job
  (mirrors the existing `CustomSelfVerifyProcessor` keepalive pattern).
- Retroactive check (A-corrective): **yes** — had `requests` been importable at
  verifier time, `task_000028`'s already-correct 200/150 service would have
  passed its collection and been graded on real behaviour; `task_000958` would
  at minimum have progressed past collection to real endpoint checks. The
  install is the exact blocker removed.
- expected_global_gain: flips the HTTP-service verifier-collection cluster
  (≥2 tasks here; the pattern recurs for any TB2 network-service task whose
  verifier imports `requests`). Removes a whole class of "correct-service,
  0-reward" losses.
- regression_risk: very low. Guarded by HTTP-service signal detection, so
  non-network tasks never trigger it. The install command is idempotent
  (`import requests` short-circuit), quiet on failure (`|| echo ...continuing`),
  and never mutates the agent's files or running services. Fires at most once.
  Worst case on a false-positive HTTP signal: one extra Bash turn that prints a
  no-op line. Ordering (`_order=95`, after self-verify's 90) means on a shared
  first-exit turn our real Bash install wins the tool-call slot and the
  self-verify checklist still fires on the subsequent exit attempt.
- cost_shift: +1 Bash turn (a few hundred tokens) on HTTP-service tasks only;
  negligible pip download cost (`requests` is small and cached after first
  install). Zero added cost on the majority of tasks that show no HTTP signal.
