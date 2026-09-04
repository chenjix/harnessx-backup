# Candidates — R4 c0 (focus: task_000010_644ab1c2)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Detect the stdlib-module-shadowing circular-import crash on `python3
<script>.py` and append a one-time advisory that names the real cause and the
correct fix (invoke without the script dir on sys.path), explicitly steering
away from the rename anti-pattern that breaks a task-mandated filename.

- Tasks affected: task_000010_644ab1c2 (single verified instance this round;
  see idiosyncratic-filter note below — shipped because the mechanism is a
  general Python footgun with zero task-specific literals and a near-zero
  regression surface).
- Signal: `exit_reason=budget_exceeded`, `reward=0`; `final_pytest`
  `test_operator_script_exists` AssertionError `/home/user/operator.py` does
  not exist. A tool result carries the module-shadowing traceback shape: a
  `partially initialized module ... most likely due to a circular import`
  error whose traceback re-enters a user-owned `*.py` file THROUGH stdlib
  frames.
- Verified (Read):
  - task_000010 step 62 (assistant): writes the required script with
    `cat > /home/user/operator.py`.
  - task_000010 step 64 (assistant tool_call): `python3 /home/user/operator.py`.
  - task_000010 step 65 (tool result): full traceback —
    `File "/home/user/operator.py", line 3` → `import tarfile` → … →
    `File "/usr/lib/python3.10/collections/__init__.py", line 36` →
    `from operator import eq as _eq` → `File "/home/user/operator.py", line 4`
    → `ImportError: cannot import name 'deque' from partially initialized
    module 'collections' (most likely due to a circular import)`. The stdlib
    `from operator import …` resolved to the agent's own `operator.py`.
  - task_000010 step 66 (assistant tool_call): `mv /home/user/operator.py
    /home/user/k8s_operator.py` — the rename anti-pattern; from here on the
    required file no longer exists at its mandated path, so the verifier's
    file-existence assertion is guaranteed to fail regardless of logic.
- Why Control not Instruction: the fix must fire *conditionally on a specific
  runtime tool-result signature* (the exact shadowing traceback), which a
  static system-prompt rule cannot condition on — a prompt line "beware module
  shadowing" would either be dead weight on ~49 non-matching tasks or too vague
  to trigger the correct recovery at the decisive moment. A Control
  `on_after_tool` hook attaches the advisory precisely when and where the crash
  appears, with zero footprint elsewhere. Not Action: no new capability is
  needed — the agent already has Bash and can re-invoke with `cd /tmp && …`;
  the gap is recognising the cause and not renaming, i.e. steering, not a new
  action space.
- Retroactive check (A-corrective): yes — had the advisory been attached to
  the step-65 result, the agent would have kept `/home/user/operator.py` and
  re-invoked it from another directory (or with `PYTHONSAFEPATH=1`) instead of
  renaming; `test_operator_script_exists` would then pass. (Honest caveat: the
  task's second failing test, `test_api_success_log`, depends on the port
  forwarding actually working, which the agent never got right — that residual
  is a model capability gap, not this candidate's target. This candidate flips
  one of the two failing tests and removes the guaranteed-zero rename trap; the
  broader value is preventing the same trap on unseen required-filename tasks.)

- expected_global_gain: Closes a general Python harness footgun — a required
  script whose basename shadows a stdlib module (`operator`, `queue`, `types`,
  `select`, `socket`, `tokenize`, `platform`, `email`, `string`, `json`, `csv`,
  `io`, …). Any unseen task of this shape currently drives the agent to the
  rename anti-pattern that violates the mandated path and hard-fails the
  file-existence check. The advisory contains no task-specific literals and
  generalises to every such collision.
- regression_risk: Essentially nil. Fires at most once per task and ONLY when a
  `python3 <script>.py` call produces the two-part shadowing signature
  (circular-import error AND a user-owned frame re-entered through a stdlib
  frame). Ordinary intra-project circular imports (only user frames) and all
  non-crashing runs never match — verified by unit tests over the real
  traceback plus negative cases. Mutates only the tool-result string (same
  contract as CustomEditToolProcessor / OcrQualityAdvisor); inserts/drops/
  reorders no messages.
- cost_shift: Negligible-to-slightly-positive. On a matching task it may prompt
  1-2 corrective re-runs that replace wasted rename/re-debug churn; exactly zero
  on the ~49 non-matching tasks. No forced extra model turns.

### Idiosyncratic-filter note

Only task_000010 exhibits this exact signature in the R3 trajectory set (grep
over all `*.messages.json` for the shadowing traceback returns one hit). Per the
analyze skill, single-task observations normally go to NEEDS_FROM_HUMAN. This is
shipped as the assigned-focus exception: the brief assigns task_000010 and asks
me to fix the harness capability the failure exposes; the exposed capability
(recognise stdlib shadowing, don't rename a required file) is a well-known,
fully generalizable Python footgun class, the advisory carries no task-specific
literals, and the detector is signature-gated so the regression surface on the
rest of the benchmark is ~zero. This is the low-risk / plausible-generalization
corner where a narrow Control advisory is defensible over a no-op.
