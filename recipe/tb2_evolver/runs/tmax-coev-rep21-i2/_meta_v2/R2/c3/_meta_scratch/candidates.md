# R2 Candidates — c3 (focus: task_000028_7fe033ac)

## Candidate C-001 — ensure verifier HTTP-client dep (`requests`) in system python3

**Three-axis tag:** lens=environment-deficiency / lever=control / intent=unblock-verifier-collection

### Signal
Assigned focus `task_000028_7fe033ac` has `reward=0` but the failure is NOT in
the agent's solution. `result.json.final_pytest`:

```
ImportError while importing test module '/tmp/test_final_state.py'
/tmp/test_final_state.py:6: in <module>
    import requests
E   ModuleNotFoundError: No module named 'requests'
Interrupted: 1 error during collection
```

The agent solved the task — its own tool output shows the nginx→C++ backend
integration returning the expected `150` through the reverse proxy. The
verifier's pytest module (injected after the session ends) imports `requests`
under the container's system `python3` (`/usr/lib/python3.10`), which the base
image does not ship, so scoring crashes at *collection* before any assertion.

### Verified body evidence
- `task_000028_7fe033ac.result.json`: `final_pytest.passed=false, rc=2`, tail =
  the `import requests` ModuleNotFoundError at collection; `initial_pytest.passed=true`.
- Cluster: `grep "No module named" *.result.json` → exactly 4 tasks, all the
  string `'requests'` (8 hits = 4 tasks × 2 pytest runs):
  `task_000028_7fe033ac`, `task_000106_23215092`, `task_002063_8c8adcfe`,
  `task_001857_24daeef3`. The first three are pure verifier-collection failures
  (`final_pytest` present, agent `exit_reason` done/loop_detected). No other
  module ever appears in a `No module named` error across all 50 result.json.
- pip works in these containers: `task_000106_23215092.messages.json` contains
  `Successfully installed numpy-2.2.6` and `Successfully installed scipy-1.15.3`
  during the agent phase → installing `requests` into the same container is
  feasible and persists to the verifier's final-state inspection.
- API verified against source: `TaskStartEvent.task_description` exists
  (`harnessx/core/events.py:86`); `get_current_sandbox()` returns the active
  sandbox (`harnessx/sandbox/base.py:188`); it is set (`harness.py:1196
  _sandbox_ctx.set`) BEFORE `run_loop` (`harness.py:1292`) inside which
  `on_task_start` fires (`runloop.py:206`) — so the sandbox is live at task_start.
  `sandbox.exec(command, timeout=...)` matches (`base.py:57`).

### Retroactive check (variant: would-it-have-fired)
On the 4 matched R0 trajectories the task descriptions all contain service
signals (`nginx`, `reverse proxy`, `http`, `socket`, `backend`, `port`), so
`_SERVICE_HINT_RE` matches and the ensure-command would have run at task_start,
placing `requests` in system python3 before the verifier imported it →
collection would succeed and the 3 pure-collection failures become scorable.
On the 46 non-matched-error tasks, the guard `python3 -c 'import requests'`
short-circuits if already importable, and non-service tasks skip entirely.

### Why control-lever, not instruction/action
The agent literally cannot know about `requests` — the verifier test files do
not exist during its session (tb2-playbook Sandbox topology). Putting anything
in the system prompt (instruction lever) would be dead guidance: no strategy
lets an agent infer a dependency of a file it never sees. This is a runtime
environment gap → a `MultiHookProcessor` (control) that hardens the container
before the model runs is the only correct lever.

### Pareto statement
- **expected_global_gain**: Unblocks the 3 (up to 4) HTTP/socket-service tasks
  whose only failure is verifier `import requests` at collection. Generalizes
  to any future service task the verifier probes over HTTP with `requests`.
- **regression_risk**: Very low. Service-gated heuristic → non-service tasks
  untouched. Idempotent import-guard → already-present `requests` = no-op.
  Contract-neutral (yields event unchanged, never mutates messages; passed the
  auto contract check). Best-effort → all failure paths swallowed; on an
  offline index the task is no worse than today.
- **cost_shift**: Negligible. One pre-loop sandbox exec (~1s, guarded) on
  service-shaped tasks only; zero model tokens. If a task still hits the loop
  detector, that is orthogonal (unchanged).
- **rollback_trigger**: If R2 shows these tasks still fail with the same
  `import requests` collection error, the container index is offline at agent
  time and no install can help — revert. Also revert if any previously-passing
  service task regresses (would indicate the exec disrupted container state,
  which the contract-neutral/best-effort design makes implausible).

### Note on novelty
The R1 journal has a `pending` (never scored, never reverted) hypothesis
`h_verifier_requests_dep_v1` proposing the same class of fix, but it was NOT
merged into the R1 baseline `config.yaml` I evolve from (grep confirms the
processor is absent from R1/config.yaml). This round ships it into the live
config with a hardened install command (`python3 -m pip` targeting the system
interpreter explicitly, plus pip/pip3 fallbacks). New hypothesis_id:
`h_verifier_requests_dep_v2`.
