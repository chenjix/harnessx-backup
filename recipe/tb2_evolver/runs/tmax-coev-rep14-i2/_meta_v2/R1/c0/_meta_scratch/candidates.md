# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `ModuleShadowRecoveryProcessor` (`on_after_tool`) that detects the
Python "script-name shadows an importable module" circular-import signature
in Bash output and injects a general, path-preserving recovery hint.

- Tasks affected: task_000010_644ab1c2 (assigned focus). Failure *class*:
  any task requiring a script at a fixed path whose basename collides with an
  importable module — `operator.py`, `queue.py`, `types.py`, `email.py`,
  `select.py`, `code.py`, `test.py`, etc. The single training instance is the
  assigned focus; the mechanism is language-level and recurs across this whole
  name-collision family.
- Signal: `final_pytest` fails `test_operator_script_exists` — the required
  file `/home/user/operator.py` does not exist at task end even though the
  agent had a fully working script. `agent.finished = "no_tool_calls"`,
  `exit_reason=done`, 74 steps (budget wasted on the loop).
- Verified (body, messages.json):
  - Step running `python3 /home/user/operator.py` returns:
    `AttributeError: partially initialized module 'functools' has no attribute
    'lru_cache' (most likely due to a circular import)` with a traceback that
    re-enters `/home/user/operator.py` from inside the stdlib import chain
    (`from operator import eq as _eq`). This is the exact `_CIRCULAR_RE`
    signature the processor keys on.
  - The agent then repeatedly (steps ~50-74) concludes "there's a fundamental
    Python naming conflict that cannot be resolved while keeping the file at
    the required path" and finally `mv /home/user/operator.py
    /home/user/k8s_operator.py`, deliberately abandoning the required path →
    verifier hard-fails. It also burned multiple `finish_reason=length`
    repetition loops on the same wrong conclusion.
- Why Control not Instruction: the correct recovery (`python3 -I/-P`, run from
  another cwd, or trim `sys.path[0]`) must fire *exactly at the moment the
  crash appears in tool output* and only for tasks that actually hit it.
  Baking a paragraph about sys.path shadowing into the system prompt would tax
  every unrelated task's context for a rare trap, and the model already
  demonstrably ignores general "verify your outputs" guidance here. A
  mechanical `on_after_tool` hook that triggers only on the diagnostic error
  string is the narrow, zero-cost-when-absent fix. Not Action: no new
  capability is needed — Bash already runs Python; the gap is recognizing a
  recoverable error, which is post-processing of an existing tool's return.
- Retroactive check (A-corrective): yes. Had the hint been in context right
  after the first circular-import crash, the agent would have run
  `python3 -I /home/user/operator.py` (or `cd /tmp && python3 ...`) with the
  file left at `/home/user/operator.py`; its script already produced the
  correct backup + applies when run under a non-shadowing name, so
  `test_operator_script_exists` and the behavior tests would both pass.

- expected_global_gain: flips the assigned failing task and any future
  script-name-shadows-module task; the trap is a common real-world Python
  footgun in "write a script at exact path X" tasks.
- regression_risk: near-zero. The processor is inert unless a Bash result
  contains the very specific "partially initialized module ... circular
  import" / "cannot import name ... from partially initialized module" text,
  which does not appear in healthy runs. Bounded to `max_hints=3` per task so
  it cannot loop. Only appends to a tool result; never blocks a call, never
  touches the system prompt or message history shape.
- cost_shift: negligible. Adds a short (~120-word) string at most 3 times, and
  only on tasks that were already crashing — expected to *reduce* cost by
  ending the repetition/loop thrash the assigned task exhibited (74 steps,
  multiple length-truncation loops).
