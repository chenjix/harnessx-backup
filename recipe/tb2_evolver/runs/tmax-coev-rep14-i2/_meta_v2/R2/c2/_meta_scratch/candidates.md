# Candidates — R2 c2

## Candidate C-001 — VerifierDepEnsurer (ensure `requests` importable on HTTP-service tasks)

**Three-axis tag:** lens = verifier-phase infrastructure failure /
lever = **control** (before-tool command rewrite) / intent = **corrective**
(unblock a guaranteed-zero-reward failure mode the agent cannot anticipate).

**Assigned focus:** `task_000028_7fe033ac` (system_administration —
Nginx reverse proxy + C++ HTTP socket backend on `127.0.0.1:8080`, verifier
issues HTTP requests).

### Signal

`task_000028_7fe033ac.result.json`:
- `initial_pytest.passed = true`
- agent `exit_reason = done`, 23 steps, `finished = no_tool_calls` (agent
  believed it was done)
- `final_pytest.passed = false, rc = 2`, `output_tail`:
  ```
  /tmp/test_final_state.py:6: in <module>
      import requests
  E   ModuleNotFoundError: No module named 'requests'
  Interrupted: 1 error during collection
  ```

The agent built the Nginx config + C++ server correctly and never had a reason
to touch `requests` (grep of the transcript: `requests` appears 1x, in the task
context, never in an agent command; no `pip install requests`). The verifier's
`test_final_state.py` does not exist during the agent phase (TB2 sandbox
topology), so the agent cannot inspect the dependency requirement. Every test
in the module errors at collection time, so a correct solution scores 0 for a
purely infrastructural reason.

### Verified body evidence (cross-task cluster)

Scanned all 50 `result.json`. **5 tasks** abort at collection on exactly
`No module named 'requests'`:
- `task_000028_7fe033ac` (assigned) — Nginx + C++ HTTP backend :8080
- `task_000106_23215092` — Flask-style HTTP API :8000 (tested with urllib)
- `task_000958_4bb2b05d` — HTTP service
- `task_001857_24daeef3` — multi-protocol service, HTTP `/health` :8080 + TCP :9090
- `task_002063_8c8adcfe` — HTTP service

Environment supports runtime pip installs: `task_000106_23215092.messages.json`
step ~34 shows `pip3 install numpy` → `Successfully installed numpy-2.2.6`
(16.8 MB downloaded at 55 MB/s), step ~44 `Successfully installed scipy-1.15.3`.
So ensuring the dependency at runtime is viable, not blocked, in this eval.

### Retroactive check (variant: "would the mechanism have flipped the focus?")

If `requests` had been importable, `test_final_state.py` collects, and the
agent's Nginx+C++ solution (which passed the agent's own urllib/curl testing)
is scored on its merits instead of aborting at import. The failure is entirely
in collection, not in any assertion, so the mechanism directly addresses the
zero-reward cause. **yes** for the 5 collection-abort tasks. For the 5 armed
tasks that fail for other reasons and the 3 armed passing tasks the guard is a
no-op (see regression_risk).

### Why control (before-tool rewrite), not instruction or configuration

- **Not instruction**: the agent cannot see the verifier test file and testing
  with `urllib`/`curl` is entirely valid, so no system-prompt guidance
  ("install requests") is generalizable or honest — it would be injecting a
  task-class-specific verifier detail into the prompt. The correct fix operates
  in the verifier-facing environment, not the agent's reasoning.
- **Not configuration**: no existing pipeline knob controls dependency staging;
  no threshold tweak addresses a missing module.
- **Control** is exactly right: a scoped, idempotent before-tool command
  rewrite that stages the dependency the verifier needs, using the same
  `tool_input`-rewrite interception surface the existing pipeline uses.

### Arming precision (Pareto protection)

Detector = SERVICE surface AND LISTEN surface in the task description.
Across the 50-task round the detector arms **13 tasks**:
- 5 = the requests-collection cluster → direct beneficiaries.
- 5 = fail for other (non-requests) reasons → guard is a harmless no-op.
- 3 = already passing (`task_000011`, `task_000683`, `task_001382`) → guard is
  a no-op: `python3 -c 'import requests'` short-circuits before any install
  when the module is present, and the original command runs unchanged after the
  `;`; install output is redirected away, so stdout/stderr semantics are
  preserved. Zero regression risk on passing tasks.

v2 over the R1-c3 draft: broadened the SERVICE regex (`multi-protocol service`,
`run/deploy … service`, "verifier will make HTTP requests") so `task_001857`
arms — the v1 detector missed it even though its verifier aborted on the
identical `import requests` error.

### Pareto statement

- `expected_global_gain`: unblocks a 5-task HTTP-service cluster (5 domains)
  whose verifier pytest aborts at collection on `import requests` — currently
  guaranteed reward=0 for a purely infrastructural reason the agent cannot see.
- `regression_risk`: very low. Guard fires ≤1×/task, only on armed tasks; it is
  `|| true`-terminated and runs the original command unchanged after `;`; it is
  a no-op when `requests` already imports; install output is silenced. The 3
  armed passing tasks are untouched in behavior. If the environment ever blocks
  outbound pip the guard degrades to the current state (no new failure).
- `cost_shift`: negligible. One import probe (+ at most one quiet pip install of
  a small pure-Python wheel) prepended to a single Bash call on armed tasks
  only; no extra model turns.
- `rollback_trigger`: revert if any previously-passing HTTP-service task
  (esp. task_000011/000683/001382) regresses to F attributable to the prepended
  guard, or if replay fails on the guard.
