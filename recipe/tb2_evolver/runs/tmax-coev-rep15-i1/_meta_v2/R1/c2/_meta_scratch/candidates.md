# Candidates — R1 / c2

Focus (assigned): `task_000028_7fe033ac` fails.

## Diagnosis

`task_000028_7fe033ac` (system_administration): the agent built the required
video-analysis microservice **correctly** — the trajectory shows a working
end-to-end probe: `HTTP/1.1 200 OK ... 150` through the nginx proxy after it
fixed the socket, the C++ server, logrotate, and even the unix-socket
permission bug. Yet `reward=0`. The `final_pytest.output_tail` shows the real
cause:

```
/tmp/test_final_state.py:6: in <module>
    import requests
E   ModuleNotFoundError: No module named 'requests'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

The external verifier probes the HTTP service using the Python `requests`
library and imports it at module load. In this container `requests` is not
installed, so the verifier crashes at **collection time**, before any assertion
runs — a hard 0 regardless of solution correctness. Per the tb2 playbook the
verifier files are injected *after* the agent exits and are invisible during
the run, so the agent has no way to know this dependency exists.

This is systemic, not idiosyncratic:
- `task_000958_4bb2b05d` (data_querying): identical shape — builds a C++ HTTP
  microservice on `127.0.0.1:9090`; `final_pytest` fails with the same
  `ModuleNotFoundError: No module named 'requests'` collection crash.
- Counter-evidence that the fix is viable: `task_000344_e265c898` (PASS) and
  `task_001673_86224c91` (PASS) are also HTTP-service tasks whose
  `test_final_state.py` imports `requests` — and there it resolved cleanly.
  So `requests` availability varies by container image; ensuring it is present
  before finishing converts the failing images into passing ones.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `ServiceDepsReminderProcessor`: a one-shot, exit-time Control hook that
detects (from the agent's own Bash commands) that the task builds/probes a
network HTTP/socket service, and then reminds the agent to make the Python
`requests` HTTP client importable via an idempotent check-then-install before
finishing.

- Tasks affected (corrective, >=2 distinct, same mechanism):
  - `task_000028_7fe033ac` — verifier `import requests` collection crash.
  - `task_000958_4bb2b05d` — verifier `import requests` collection crash.
- Signal: `final_pytest.rc=2`, `output_tail` = `ModuleNotFoundError: No module
  named 'requests'` at `test_final_state.py` import; both tasks are HTTP-service
  builds (nginx unix socket / C++ httplib on a local port).
- Verified (body):
  - task_000028 assistant final verification step ran
    `exec 3<>/dev/tcp/127.0.0.1/8080 ... GET / HTTP/1.1` and got
    `HTTP/1.1 200 OK ... 150` — service demonstrably correct; the agent never
    ran `import requests`/`pip install requests` (grep count = 0), so the
    verifier's dependency was never satisfied.
  - task_000958 first user message: "Write a C++ HTTP server listening exactly
    on `127.0.0.1:9090` ... You may download and use the header-only
    `cpp-httplib` library"; `final_pytest` same `import requests` crash;
    agent grep for requests install = 0.
- Why Control not Instruction: the missing piece is not knowledge the model can
  reason its way to — the verifier files are injected *after* the agent exits
  and are unreadable during the run (tb2 playbook), so no static prompt rule
  makes the agent aware of *this specific environment gap* at the right moment.
  A mechanical hook that (a) recognises the network-service shape from the
  agent's actual Bash activity and (b) fires exactly once at exit is the
  precise, self-limiting mechanism. It is not Action: the agent already has the
  only tool it can use (`Bash`); the gap is a missing prompt-turn at exit, which
  a processor injects — a new tool would add nothing the Bash install can't do.
- Retroactive check (A-corrective): yes. If, at task_000028's exit attempt, the
  agent had run `python3 -c 'import requests' || pip install requests`, the
  verifier's `test_final_state.py` would have imported cleanly and its
  assertions (which the working service satisfies) would have passed. Same for
  task_000958. The passing tasks 000344/001673 prove the assertions pass once
  the import resolves.
- expected_global_gain: flips the two `import requests` verifier-crash failures
  in this round (000028, 000958) and generalizes to any future network-service
  task landing in an image without `requests` — a recurring, structural class
  on this benchmark (multiple HTTP-service tasks per round).
- regression_risk: low. The hook only fires on tasks matching a network-service
  signal AND only at the agent's exit attempt, at most once. The injected
  command is idempotent (`import` check first; install is `|| ... || true`), so
  on images where `requests` already exists it is a no-op that costs one extra
  cheap Bash turn. It cannot break non-service tasks (signal never matches) and
  serializes after `CustomSelfVerifyProcessor` (`_order=91` vs `90`) so the two
  exit hooks do not both rewrite `tool_calls` on the same turn. Worst realistic
  case on a service task where install is impossible (no index reachable): the
  agent is no worse off than status quo.
- cost_shift: +1 short Bash turn (a few hundred tokens) on the subset of tasks
  that build a network service; zero on all other tasks. Negligible net cost,
  clearly outweighed by the recovered passes.
