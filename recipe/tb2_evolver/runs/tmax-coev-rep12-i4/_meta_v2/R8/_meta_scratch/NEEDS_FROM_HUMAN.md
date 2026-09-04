# Needs from human — verifier-infrastructure faults (out of agent-phase harness scope)

These are NOT model-capability gaps and NOT fixable by any processor/tool/prompt
in the agent phase (they occur in the verifier phase, which runs after the agent
exits and shares no live state). Surfacing for the benchmark maintainers.

## task_000998_4d9c7852 — stdlib module-shadowing crashes the verifier bootstrap

- The task *mandates* the agent write its solution to `/home/user/operator.py`.
- The verifier's pytest invocation places `/home/user` on `sys.path` ahead of the
  stdlib, so `from operator import eq` (imported deep in CPython's
  `collections`/`contextlib` bootstrap) resolves to the agent's `operator.py`,
  producing `ImportError: cannot import name 'namedtuple' ... circular import`
  and `Interrupted: 1 error during collection`. The agent's actual work is NEVER
  scored — the whole test module fails to even import.
- This is a benchmark-authoring collision: the required output filename
  (`operator.py`) shadows a stdlib module the verifier transitively imports.
  The agent cannot both satisfy the task (write operator.py at that exact path)
  and avoid poisoning the verifier's path. No agent-phase harness mechanism can
  fix a `sys.path` ordering decision made by the post-exit verifier.
- Fix belongs upstream: either the verifier should run pytest with
  `rootdir`/`sys.path` not including `/home/user`, or the task should require a
  non-shadowing filename.
- Single task → below the >=2 idiosyncratic threshold anyway; logged, not patched.

## task_002071_157686dd — injected test has a Python SyntaxError

- `/tmp/test_final_state.py` line 54: `rf"\b{d.replace(' ', r'\s+')}\b"` →
  `SyntaxError: f-string expression part cannot include a backslash` at collection
  (Python 3.10). The injected verifier test is itself invalid on the runtime
  Python; no agent action can pass it. Upstream test-authoring bug.

## task_000709_12e0d757 — verifier invokes `python` (not present)

- Verifier subprocess: `FileNotFoundError: [Errno 2] No such file or directory:
  'python'`. The container only ships `python3`; the verifier's reproduce step
  shells out to `python`. Environment mismatch in the verifier, not the agent.

## task_001074_90fdfe60 — verifier command timed out after 180s

- `final_pytest` tail: `Error: command timed out after 180s`. A verifier-side
  timeout; unobservable whether the agent's deliverable was correct.
