# Candidates — Round 1 (rep19-i2)

Assigned focus: `task_000028_7fe033ac` fails. Diagnosis below shows it is
one member of a 6-task structural cluster, not an idiosyncratic failure.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Inject a one-time runtime reminder — on tasks that ask the agent to build a
service an external HTTP verifier will call — to ensure the system Python has
the standard HTTP client library (`requests`) installed before finishing, so
the verifier's `test_final_state.py` can be collected.

- Tasks affected (all reward=0, verifier `ModuleNotFoundError: No module named 'requests'`):
  task_000028_7fe033ac, task_000106_23215092, task_000809_760d7fa0,
  task_000958_4bb2b05d, task_001857_24daeef3, task_002063_8c8adcfe
- Signal: `final_pytest.output_tail` on all six contains
  `ImportError while importing test module '/tmp/test_final_state.py'` →
  `ModuleNotFoundError: No module named 'requests'` →
  `Interrupted: 1 error during collection`. Reward=0 for every one.
- Verified (Read):
  - task_000028 (assigned): the agent's service demonstrably works — its own
    final probe returns `HTTP/1.1 200 OK ... Content-Length: 3 ... 150`
    through nginx on 127.0.0.1:8080, logrotate + socket all correct
    (messages.json steps ~20-30). It still scored 0 solely because the
    verifier could not `import requests`. Task text: *"An automated verifier
    will make HTTP requests to http://127.0.0.1:8080/ to check your Nginx
    proxy and C++ server integration"*.
  - task_000106: builds a graph-analytics API service; task text asks for an
    API the verifier calls; `final_pytest` errors identically at
    `import requests`. Crucially this trajectory shows `pip3 install numpy`
    and `scipy` BOTH succeed (`Successfully installed numpy-2.2.6` /
    downloads from PyPI at 54 MB/s) — proving outbound pip works, so
    `pip install requests` would have fixed it.
  - task_000809 / task_000958 / task_002063: each asks to write an HTTP
    service / microservice; all three `final_pytest` tails carry the
    identical `import requests` collection error.
- Why Control not Instruction: the missing knowledge is a *structural fact of
  the sandbox topology* (verifier runs post-exit in system Python and imports
  `requests`) that the agent has no way to observe — the test files do not
  exist during its phase. A static system-prompt line competes with the whole
  prompt for a weak 9B model's attention and would fire on every task
  (including the ~40 non-service tasks it can't help), diluting signal. A
  Control hook delivers the reminder *only* when the task description matches
  the service+verifier shape, at the exact moment (first step) it is
  actionable, keeping the install itself agent-authored via Bash.
- Why Control not a force-install processor: unconditionally running
  `pip install requests` on every task mutates container state silently and
  wastes time/network on tasks that don't need it; the reminder is targeted
  and lets the agent skip it if already present.
- Retroactive check (A-corrective): yes. task_000028's service already passed
  every functional check the verifier would run; the ONLY blocker was the
  `import requests` collection error. Had the agent run `pip3 install
  requests` (proven to work in task_000106) the verifier module would collect
  and the functional asserts would pass. Same mechanism for the other five.
- expected_global_gain: flips up to 6 reward=0 tasks in the
  build-a-service-for-an-HTTP-verifier cluster (spans system_administration,
  data_querying, data_processing, debugging, software_engineering) — a broad,
  cross-domain structural win, not a single-task patch.
- regression_risk: low. The processor only appends one `user` message and only
  when BOTH a verifier-intent regex AND a service-shape regex match the task
  description; pure file/data/query tasks are untouched. Worst case on a
  matched task: one extra reminder message (~250 tokens) and one `pip install`
  command that is a no-op if already present. Contract check passes (append
  exactly +1 message on one step).
- cost_shift: negligible. ≈+250 tokens once per *matched* task (a minority of
  the set) plus at most one extra Bash `pip install` round-trip; no effect on
  unmatched tasks.
