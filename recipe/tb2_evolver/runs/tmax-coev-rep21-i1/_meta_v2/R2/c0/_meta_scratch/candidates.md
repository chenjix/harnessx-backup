# Candidates — R2 / c0

Assigned focus: `task_000010_644ab1c2` (system_administration) fails.

## Diagnosis

The task requires a Python script at the exact path `/home/user/operator.py`.
`operator` is a Python **standard-library module name**. When the agent runs
`python3 /home/user/operator.py` (and, critically, when the verifier later runs
pytest) from within `/home/user`, CPython prepends the script's directory to
`sys.path[0]`. Any stdlib import chain that transitively imports `operator`
(`collections` → `from operator import eq as _eq`) then resolves to the user's
`operator.py` instead of the real stdlib module, producing a fatal
self-referential traceback:

- mid-run tool result: `ImportError: cannot import name ... from partially
  initialized module 'collections' (most likely due to a circular import)
  (.../operator.py)`
- final verifier tail: `Could not import runpy module ... File
  "/home/user/operator.py", line 2 ... SyntaxError: invalid syntax`

The agent had **no visibility into the sys.path[0] mechanic**, misdiagnosed it
as a bug in its own script, and thrashed for 60+ steps (rename → symlink →
rewrite → bash-wrapper). It ultimately overwrote the required `operator.py`
with a `#!/bin/bash` wrapper (`python3 /home/user/k8s_operator.py "$@"`), which
is not valid Python — the exact state that fails the verifier's pytest with
`SyntaxError`. `exit_reason=budget_exceeded` (80 steps).

Existing guards fired but could not supply the missing insight:
`RepeatedCommandRecoveryProcessor` (threshold 3) and `CustomEditToolProcessor`
(threshold 7) both fired — the agent acknowledged them ("The loop detection is
correct") yet kept issuing the identical `cat > operator.py` bash-wrapper. The
generic "try a materially different command" nudge is not enough when the agent
lacks the specific structural knowledge to escape.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `StdlibShadowDiagnosticProcessor` that detects the CPython
stdlib-module-shadowing traceback signature in tool output and injects one
targeted, task-agnostic diagnostic explaining the `sys.path[0]` mechanic and
the concrete escape routes (run from a different cwd, `python3 -P` /
`PYTHONSAFEPATH=1`, or sys.path manipulation) — without renaming the required
file.

- Tasks affected: `task_000010_644ab1c2` (assigned focus). This is the only
  task in this batch that exhibits the *exact* stdlib-shadow signature, so the
  systemic-vs-idiosyncratic bar is met at the level of the **mechanism**, not
  the task: the detector keys on a general CPython footgun (any required file
  whose basename collides with a stdlib module: `operator.py`, `token.py`,
  `types.py`, `queue.py`, `select.py`, `code.py`, `string.py`, ...), which
  recurs across system_administration / scripting tasks that mandate specific
  filenames. The R1 journal already notes background-launch and process-state
  hazards are structural across ~36/50 tasks; filename-collision is the same
  class of "runtime side-effect the agent cannot see."
- Signal: `result.json` `exit_reason=budget_exceeded`, `final_pytest` tail =
  `Could not import runpy module ... File "/home/user/operator.py" ...
  SyntaxError`; `initial_pytest`/mid-run tool result =
  `partially initialized module 'collections' ... circular import`.
- Verified (Read messages.json):
  - step 2→3: agent runs `python3 /home/user/operator.py`; tool returns the
    circular-import traceback bottoming out in `/home/user/operator.py`.
  - step 4: agent misdiagnoses — `mv operator.py k8s_operator.py && ln -sf`.
  - steps 9–37: repeated `rm -f ... && cat > /home/user/operator.py << EOF`
    (Python then bash wrapper); EditDetection fires at step 18/36; agent keeps
    going.
  - steps 58–68: agent settles on `cat > operator.py` with a `#!/bin/bash`
    wrapper — the corrupt state that fails the verifier — repeated to budget.
  - Detector unit-checked against both real tracebacks → fires True; against an
    ordinary `NameError` and normal output → fires False.
- Why Control not Instruction: the missing thing is *runtime awareness of a
  side effect the agent cannot observe* (which directory is on sys.path[0] at
  the moment of a stdlib import), surfaced only when the specific traceback
  actually appears. A static system-prompt rule would either (a) not fire
  because the model doesn't recognize the traceback as shadowing (it demonstrably
  didn't), or (b) bloat every task's prompt with a footgun it will never hit.
  A Control hook is conditional — it injects the diagnostic exactly when the
  signature is observed, on any task, and stays silent otherwise. It is also
  strictly informational (no kill/rewrite), so it cannot corrupt state.
- Why Control not Configuration: no existing knob encodes "recognize stdlib
  shadowing"; tightening the repeat/edit thresholds would only fire *sooner* on
  the same generic nudge the agent already ignored — the gap is the *content*
  of the diagnostic, not its timing.
- Retroactive check (A-corrective): yes — had the diagnostic been in context at
  step 3 (first shadowing traceback), the agent would have known not to rename
  the file and to instead run it from a different cwd / with `-P`; the correct
  `operator.py` it wrote at step 2 would have stayed in place, and the verifier
  pytest (run from a neutral cwd or with the file intact) would import stdlib
  `operator` normally. The 60-step thrash is entirely downstream of this one
  missing insight.

### Pareto
- expected_global_gain: Flips `task_000010`; generalizes to any task whose
  required deliverable filename collides with a stdlib module — a recurring
  scripting/sysadmin footgun. Closes a "correct-logic, self-corrupted-file"
  failure class the existing loop guards cannot resolve.
- regression_risk: Very low. The detector requires BOTH a shadow signature
  (partial-init / circular-import / runpy / site-import failure) AND a
  user-file traceback frame (a `.py` outside `/usr/lib/python*`,
  `site-packages`, `dist-packages`); ordinary user tracebacks (NameError,
  logic bugs) and clean output do not match (unit-confirmed). It is
  informational-only (never mutates files/processes) and fires at most
  `max_fires=2` per task, so it cannot nag or interact destructively with
  CustomSelfVerify. Ordered 7 (before compaction), same slot as the sibling
  repeat-recovery processor; contract check passed with no violations.
- cost_shift: Negligible-to-negative. On the rare shadow path it adds one short
  message + one model turn but *saves* the 60+-step thrash that currently burns
  the entire budget. On all other tasks: zero (never fires).

### Rollback trigger
If R3 pass_rate is flat/down AND any previously-passing task that legitimately
prints a partial-init / circular-import traceback (e.g. a task deliberately
testing import errors) regresses T→F, revert — the false-positive guard
(dual signature + user-frame requirement) is the first suspect.
