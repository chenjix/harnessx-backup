# Candidates — Round 1 / c2

Assigned focus: `task_000028_7fe033ac` fails (reward 0).

## Diagnosis (verified against trajectory body)

`task_000028_7fe033ac` (system_administration): agent must fix an nginx reverse
proxy + a C++ unix-socket HTTP backend that returns a video frame count.

- `initial_pytest.passed = true`, `final_pytest.passed = false`, `rc = 2`.
- `final_pytest.output_tail`: `ImportError while importing test module
  '/tmp/test_final_state.py' ... import requests ...
  ModuleNotFoundError: No module named 'requests'` — the verifier fails at
  **collection** time, before it ever exercises the service.
- The agent's service is **fully correct**: last probe returns
  `HTTP/1.1 200 OK ... Content-Length: 3 ... 150` (the exact frame count), both
  nginx and `/app/server` are running, socket perms fixed. The task was solved;
  it scored 0 purely on a missing grading dependency.

Root-cause of the *harness* miss (verified):
- The R0 pipeline already contains `HttpVerifierDepProcessor` — a soft nudge that
  appends a `user` reminder telling the model to `pip install requests`.
- Grepping the full message log: **0** occurrences of `HttpVerifierReminder`,
  `pip install requests`, or an `import requests` check. The conversation has
  exactly **one** user message (the original task). The nudge produced no
  behavioural change — either it never surfaced to the model or the model
  ignored it. Either way, a soft nudge is not a reliable fix for a structural
  grading dependency.

This is a **harness deficiency**, not a model capability gap: the agent cannot
see the verifier's `import requests` (verifier files are injected post-exit;
tb2-playbook), so no amount of task reasoning closes it. The harness must
guarantee the dependency.

## Candidate C-201 — replace soft `requests` nudge with a structural install guarantee

- **Lens / Lever / Intent**: failure-recovery / new-processor (replace existing) / close-a-structural-grading-gap
- **Signal**: 1 assigned task confirmed 0-reward purely on
  `ModuleNotFoundError: No module named 'requests'` at verifier collection time,
  despite a correct running service; existing soft-nudge processor demonstrably
  inert (0 hits in the message log).
- **Change**: author `processors/http_verifier_dep_ensure.py::HttpVerifierDepEnsureProcessor`
  and swap it in for the R0 `HttpVerifierDepProcessor` in the pipeline. New
  behaviour:
  - Detect a service task via broad framework-agnostic Bash signals (adds
    `nginx`, `apache`, `node`, `proxy_pass`, `curl`, `wget`, plus the prior
    flask/fastapi/uvicorn/gunicorn/http.server/port-bind/`--port` set).
  - On **exit-intent** (`finish_reason in {end_turn, stop}` and no tool calls)
    of a detected service task, inject exactly one **real** `Bash` tool call
    (run loop executes injected `approved=True` calls) that makes `requests`
    importable: `python3 -c 'import requests' || pip install requests`,
    idempotent and `|| true` so a no-network container never turns it into
    `exit_reason=error`.
  - Fires **at most once**, **only** on service tasks. Ordered `_order=93`,
    after `CustomSelfVerifyProcessor` (90) so its checklist keepalive lands on
    the first exit-intent and this install fires on the next — they never
    collide on the same turn.
- **Why new-processor not prompt / knob**: the failure is invisible to the model
  (verifier files absent during the agent phase), so a system-prompt rule is
  exactly the class of fix that already failed here. A knob change is N/A — the
  legacy processor had no knob to make its nudge binding. Only a *structural*
  interception that runs the install itself is robust to model non-compliance.
- **Retroactive check (would-this-have-passed)**: on `task_000028_7fe033ac` the
  agent reached exit-intent with a correct running service; this processor would
  have injected `pip install requests` before the run ended, making the
  verifier's `import requests` succeed at collection, flipping reward 0→1 (the
  service already returns the correct `150`). Nothing else in that task was
  wrong.
- **expected_global_gain**: the whole HTTP-service cluster whose external
  verifier does `import requests` on containers lacking it. Generalizes because
  the signal is structural (service detected + exit-intent), not task-specific.
- **regression_risk**: low. Only service tasks are touched; on non-service tasks
  the flag never flips and nothing fires. Worst case on a service task with no
  network and no preinstalled `requests`: the injected command is `|| true`, so
  it prints a WARNING and finishes without erroring — identical net outcome to
  today (still 0) but no crash. One extra Bash round + one short follow-up user
  message on service tasks only.
- **cost_shift**: +1 tool round and +~90-token follow-up message per *service*
  task that reaches exit-intent (a small minority of the set). Negligible on the
  benchmark aggregate; zero on non-service tasks.
- **rollback trigger**: if the next round shows any service task newly failing
  with `exit_reason=error` traced to the injected Bash call, or a net drop in
  the service-task cluster, revert to the R0 soft-nudge processor.
