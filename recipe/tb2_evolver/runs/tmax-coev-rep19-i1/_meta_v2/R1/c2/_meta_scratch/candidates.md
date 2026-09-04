# Candidates — Round 1 / c2

Assigned focus: `task_000028_7fe033ac` (system_administration) fails.

## Diagnosis

The agent's solution was **functionally correct**: nginx routed to the
correct UNIX socket, the C++ server compiled and ran in the background,
`/tmp/video_backend.sock` existed, and an in-container HTTP GET to
`127.0.0.1:8080/` returned `HTTP/1.1 200 OK` with body `150` (the correct
frame count). Logrotate config created. `_tb2_self_verify` fired and the
agent confirmed reachability.

Yet `reward = 0`. Root cause from `result.json.final_pytest`:

```
ERROR collecting test_final_state.py
    import requests
E   ModuleNotFoundError: No module named 'requests'
Interrupted: 1 error during collection
```

The **verifier's** pytest (run inside the same container after the agent
exits) failed at *collection* because `requests` was not installed. The
agent could not see the verifier file, but it left the container without a
library the automated checker needed — a state the agent could have made
robust before exiting.

This is not idiosyncratic. A second failing task in the same round shows the
same root class from a different angle (`task_000010_644ab1c2`): the agent
created `/home/user/operator.py`, which **shadowed the stdlib `operator`
module**, so the verifier's Python crashed at collection
(`SyntaxError` while importing `operator` → cascade). Both failures =
"agent left the container in a state where the verifier's Python interpreter
could not even start."

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Extend the exit-time self-verification checklist (injected by the benchmark's
`CustomSelfVerifyProcessor`) with two **general** environment-hygiene items:
(a) if an automated checker would likely run a program against your work,
ensure that program's dependencies are importable now (don't assume common
libraries are preinstalled; install if missing); (b) don't shadow
standard-library / common package module names with files you create in the
working/home directory. Shipped as a `CustomSelfVerifyProcessor` subclass
(`EnvHygieneVerifyProcessor`) that overrides only the injected checklist text.

- Tasks affected: task_000028_7fe033ac, task_000010_644ab1c2
  (same root class: verifier's in-container Python could not start against a
  functionally-correct-or-in-progress solution).
- Signal: `final_pytest.output_tail` shows a *collection*-time failure, not
  an assertion failure — `ModuleNotFoundError: No module named 'requests'`
  (028) and stdlib-module shadow producing `SyntaxError` at import (010).
  The agent's own HTTP self-test returned `200 OK` with the correct body
  (028), so the work itself passed.
- Verified (Read):
  - task_000028 messages.json step (line ~570): agent's final HTTP test
    returns `HTTP/1.1 200 OK ... Content-Length: 150 ... 150`; result.json
    `final_pytest.passed=false` with the `requests` ImportError at collection.
  - task_000010 result.json `final_pytest.output_tail`: `/home/user/operator.py`
    line 3 `exec python3 ...` triggers `SyntaxError` while the interpreter
    imports the stdlib `operator` module during pytest bootstrap.
- Why Instruction not Control: the fix is *knowledge of a condition to check
  before exiting*, not a mechanical transform of tool I/O. A Control hook
  cannot know which library a hidden verifier needs, nor which file names
  collide with modules the verifier imports — only the agent, having built
  the solution, can reason about that. The self-verify checklist is exactly
  the instruction surface for exit-time conditions; we extend it rather than
  add a new mechanical guard. (Implementation is a processor subclass only
  because the checklist text is baked into read-only benchmark code; the
  lever is still Instruction — we change *what the agent is told to check*.)
- Why not Configuration: no existing knob carries this guidance; it is new
  checklist content, not a threshold.
- Retroactive check (A-corrective): yes — had the agent been prompted at
  exit to confirm check-time deps were importable, on task_000028 it would
  have `python3 -c "import requests"` (fails) and `pip install requests`
  (a service task where an HTTP client is the obvious check tool), flipping
  the collection error. For task_000010, the shadow-module prompt would have
  surfaced the `operator.py` name collision before exit.
- expected_global_gain: targets the worst domain cluster (system_administration
  0/5). Two failing tasks share the exact collection-time root cause; the
  guidance generalizes to any service/output task validated by an in-container
  Python checker.
- regression_risk: Low. The change is additive checklist text injected only
  on the one-shot exit turn (same firing conditions as the existing
  processor). It cannot alter passing tasks' logic; worst case is a few extra
  Bash calls (an `import` probe / a `pip install`) on the final turn. Risk of
  a spurious/unneeded install is bounded — the guidance is conditional
  ("if a checker would likely run a program"), not "always install requests".
- cost_shift: Small increase — a longer exit checklist plus at most a couple
  of verification Bash calls per task on the final turn. No change to the main
  solve loop. Net positive vs. flipping collection-time zero-reward failures.
