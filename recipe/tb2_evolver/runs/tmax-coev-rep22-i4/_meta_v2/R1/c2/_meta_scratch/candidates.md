# Candidates — R1/c2

Assigned focus: `task_000028_7fe033ac` fails.

## Diagnosis

`task_000028` built a fully-correct service (nginx reverse proxy → C++ backend
on a UNIX socket returning the ffprobe frame count `150`; verified `HTTP/1.1 200
OK` with body `150` via `/dev/tcp`). `initial_pytest.passed=true`,
`final_pytest` failed with:

```
/tmp/test_final_state.py:6: in <module>
    import requests
E   ModuleNotFoundError: No module named 'requests'
Interrupted: 1 error during collection
```

The external verifier's `test_final_state.py` does `import requests`, the
container lacks it, so grading fails at *collection* time — reward 0 despite a
correct, running service. This is a harness deficiency, not a model capability
gap: the verifier files are injected only after the agent exits, so the agent
cannot possibly know about this dependency.

The existing `HttpVerifierDepProcessor` was meant to cover this but did **not
fire** on task_000028: it inspects only the Bash *command string*, and the two
service-start commands were `nohup /app/server >> /app/server.log 2>&1 &` (bare
binary, no port token) and `nginx -c /app/nginx.conf` (port lives in the conf
file, not the command). The `_SERVICE_SIGNALS` regex matched neither, so the
reminder never appeared.

The same root cause recurs across the round:
- `task_001857`: bare `/home/user/diagnostic_server &` → same detection miss.
- `task_000297`: python `http.server` on `127.0.0.1:8080` → reminder *did* have
  a matchable signal, but the agent ran `python3 -c 'import requests'`, saw
  `ModuleNotFoundError`, and finished anyway — reminder ignored.

All three are reward 0 solely due to missing `requests`; each had
`initial_pytest.passed=true`. `pip install requests` is confirmed to work in
these containers (tasks 000010/000140/000378/001090/001937/002063 installed
`requests 2.34.2` successfully), so the fix is actionable.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the reminder-only `HttpVerifierDepProcessor` with
`HttpVerifierDepInstallProcessor`: broaden service detection to also match
generic daemon / background-executable launch shapes, and on exit intent for a
detected service task inject ONE real `Bash` tool call that idempotently makes
`requests` importable (`python3 -c 'import requests' || pip install requests`).

- Tasks affected: task_000028_7fe033ac, task_000297_01ba10b6,
  task_001857_24daeef3 (all reward 0, all `import requests` collection error).
- Signal: `final_pytest.output_tail` = `ModuleNotFoundError: No module named
  'requests'` at `test_final_state.py` collection on all three; each
  `initial_pytest.passed=true` (the actual work was correct).
- Verified (Read of messages.json):
  - task_000028 idx ~162,182: service started via `nohup /app/server ... &` and
    `nginx -c /app/nginx.conf`; final endpoint test returns `200 OK` body `150`;
    grep of injected `HttpVerifierReminder` = 0 → old regex never matched.
  - task_001857: only service-launch command is `/home/user/diagnostic_server
    2>&1 &` (bare binary) → old regex never matched; `HttpVerifierReminder` = 0.
  - task_000297 idx 7-8: agent ran `python3 -c 'import requests'` →
    `ModuleNotFoundError` (exit 1), then declared success at idx 29 without
    installing → reminder-style nudge insufficient.
- Why Control not Instruction: a prompt rule cannot fire (the agent never sees
  the verifier's `import requests`), and task_000297 proves that even an explicit
  in-context reminder is ignored. The gap is a mechanical missing step that must
  fire uniformly and deterministically across service tasks — Control territory.
  The install must actually execute, not be suggested; only a synthetic
  tool-call injection (the proven `CustomSelfVerifyProcessor` keepalive pattern)
  guarantees that.
- Why Control not Action: adding a tool cannot help — the container already has
  `pip`/`Bash`; the problem is the step is never *taken*, not that it *can't* be.
- Retroactive check (A-corrective): yes — if `requests` had been importable by
  the system interpreter at the verifier phase, `test_final_state.py` would have
  collected and run against the (already-correct, still-running) services on all
  three tasks. The install command is confirmed to succeed in these containers.

- expected_global_gain: flips up to 3 currently-failing service tasks (6% of the
  50-task round) that are correct-but-ungraded; generalizes to any HTTP/network
  service task the `requests`-based verifier grades, including detection shapes
  the old regex missed (bare binary, nginx/systemd/service launches).
- regression_risk: low and bounded. Fires the install only on tasks where a
  service launch was observed; on non-service tasks it is a pure pass-through
  (majority of the round untouched). The install is idempotent — a no-op when
  `requests` is present — and orders after `CustomSelfVerifyProcessor` so its
  one-shot keepalive still lands first. Worst case on a false-positive service
  detection: one extra Bash round-trip that installs an unused package (still
  leaves the task correct). The `_BG_EXECUTABLE` matcher requires a trailing
  background `&`, so ordinary foreground commands don't trip it.
- cost_shift: +1 Bash tool call (and one short pip install) per detected service
  task at exit, only on tasks that started a service. Negligible token cost;
  removes 3 hard zeros. Net strongly positive.

- Rollback trigger: if the round regresses net pass-rate, or if replay shows the
  injected Bash call crashes / the containers block `pip install requests`
  (offline), revert to the R0 reminder-only processor.
