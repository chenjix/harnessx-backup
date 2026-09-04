# Candidates — R2 c0 (focus: task_000010_644ab1c2)

## Candidate C-001
[lens: capability-gap | lever: control | intent: corrective]

Add `StdlibShadowGuard`: detect a stdlib-module-name shadow-induced circular
import in Bash output and inject a one-time recovery strategy that keeps the
required filename but stops it poisoning the interpreter for the verifier.

- Tasks affected: task_000010_644ab1c2 (primary). Mechanism is a general
  class — any task that requires a Python file whose basename equals a stdlib
  module name (`operator.py`, `types.py`, `queue.py`, `string.py`, `code.py`,
  `select.py`, `token.py`, `calendar.py`, `test.py`, …) placed in a sys.path
  directory. It is a single instance in *this* round's failing set, but the
  failure is invisible to the agent and fatal to the grader, so the harness
  fix (teach the escape when the signature appears) generalizes to the class.
- Signal: `final_pytest.output_tail` on task_000010 =
  `Could not import runpy module` + `ImportError: cannot import name
  'namedtuple' from partially initialized module 'collections' (most likely
  due to a circular import)` with a traceback frame
  `File "/home/user/operator.py", line 10, in <module>`. `initial_pytest`
  shows the task logic (backup, port-forward, apply) is otherwise doable;
  `reward=0`.
- Verified (Read messages.json):
  - idx 0 (task): "write a Python script at `/home/user/operator.py` …" —
    the required filename is a stdlib module name.
  - idx 16: agent's own `python3 -c "import pexpect"` returns the SAME
    circular-import traceback ending `File "/home/user/operator.py", line 10,
    in <module> import su…` — the agent directly observed the poisoning.
  - idx 17: agent correctly diagnosed "naming conflict … conflicts with
    Python's built-in `operator` module" but had no escape.
  - idx 27-30: it renamed to `k8s_operator.py`, confirmed `pexpect available`,
    ran the script successfully (idx 36, backup + api_success.log correct),
    then at idx 39-40 renamed back to `/home/user/operator.py` to satisfy the
    task and exited — re-poisoning the interpreter for the grader.
  - result.json: `final_pytest.passed=false`, tail = circular-import crash at
    `/home/user/operator.py`; task effects were actually correct.
- Why Control not Instruction: the failure surfaces only *inside a tool
  result* (the circular-import traceback) and is invisible in the task text;
  a static prompt rule ("beware stdlib names") would fire on every task and
  add noise while still not naming the concrete offending file at the moment
  it matters. The Control hook fires exactly when the structural signature
  appears, extracts the offending file/module from the traceback, and delivers
  actionable, file-specific guidance — a shape a prompt rule cannot express.
- Why Control not Action: the agent already has `Bash`; it does not lack a
  capability to fix the file, it lacks *knowledge of the escape at the moment
  the poisoning is observable*. No new action surface is needed — only
  post-processing of the existing tool's output.
- Retroactive check (A-corrective): yes — at idx 16 the agent saw the exact
  traceback and understood the conflict but not the fix; had the guidance
  (transparent-shim / guard-under-`__main__` / verify-from-grader-CWD)
  appended to that tool result, it had the whole remaining session (it went on
  to idx 51) to implement a shim `operator.py` that re-exports stdlib and put
  its logic under `__main__`, leaving both the deliverable name AND a working
  interpreter — flipping the grader from crash to pass.
- expected_global_gain: closes a fatal, agent-invisible verifier-crash class
  (correct solution → 0). Recurs whenever a task demands a stdlib-named Python
  file; the guard is dormant on all other tasks.
- regression_risk: very low. Fires only when the output contains the
  circular-import / "partially initialized module" / "Could not import runpy"
  signature AND a non-stdlib-install traceback frame whose basename is a
  stdlib module. Appends to a tool result only (never blocks a call, never
  edits the system prompt, fires ≤1×/task). Frames inside
  lib/python*/site-packages/dist-packages are excluded, so ordinary
  third-party circular imports do not trigger it.
- cost_shift: negligible — one appended guidance block (~250 tokens) at most
  once, only on the rare tasks that hit the signature. Zero cost on the other
  ~49 tasks.
