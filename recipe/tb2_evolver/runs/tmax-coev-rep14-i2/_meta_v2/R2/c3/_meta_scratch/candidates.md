# Candidates — R2/c3 (focus: task_000106_23215092)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Register a `VerifierDepEnsurer` `MultiHookProcessor` that prefixes the first
substantive Bash call of a network/service task with an idempotent, silent,
`|| true`-terminated guard ensuring `requests` is importable, so the verifier's
`test_final_state.py` (which begins `import requests`) can be *collected*
instead of aborting the entire test module at collection time.

- Tasks affected (same mechanism, all reward=0, `import requests` collection
  abort in `final_pytest`): task_000106_23215092 (assigned focus),
  task_000028_7fe033ac, task_000958_4bb2b05d, task_001857_24daeef3,
  task_002063_8c8adcfe.
- Signal: `final_pytest.passed=false, rc=2` with output_tail
  `ModuleNotFoundError: No module named 'requests'` →
  `Interrupted: 1 error during collection`; `initial_pytest.passed=true`;
  `exit_reason=done`. `grep "No module named" *.result.json` returns exactly
  these 5 tasks, all `'requests'`.
- Verified (Read):
  - task_000106 result.json: `initial_pytest.passed=true`;
    `final_pytest.passed=false, rc=2`, tail = `import requests` →
    `ModuleNotFoundError: No module named 'requests'` →
    `Interrupted: 1 error during collection`.
  - task_000106 messages.json steps 27-70: agent stands up the Flask API on
    `127.0.0.1:8000`, tests `/author/1`, `/author/2`, `/author/999` (404) via
    `urllib.request` — all correct responses (step 50/52/54/58/70). It
    `pip3 install`s numpy (step 33-34, 16.8 MB wheel downloads OK) and scipy
    (step 43-44) but never installs `requests` (no reason to — it used urllib).
    Solution is functionally complete; the ONLY failure is the verifier's
    missing `requests`.
  - task_000028 / task_000958 / task_001857 / task_002063 result.json:
    identical `import requests` collection abort; each description asks for an
    HTTP API / server on a listen surface.
- Why Control not Instruction: the agent cannot know the verifier imports
  `requests` — the test file does not exist during the agent phase (TB2 sandbox
  topology), so no prompt rule could tell it to pre-install a dependency it has
  no observable reason to install. The fix must fire mechanically outside the
  agent's knowledge. It is not Configuration because no existing knob controls
  container-side dependency staging, and not Action because the agent already
  has Bash — the missing step is a guaranteed, agent-independent guard.
- Retroactive check (A-corrective): yes — task_000106's solution passed its own
  urllib tests and the initial pytest; with `requests` importable, collection
  succeeds and the four `test_final_state` assertions run against a correct
  API. The four sibling tasks share the identical collection-abort mechanism,
  so the same guard unblocks collection for each.
- expected_global_gain: unblocks a 5-task HTTP-service cluster (spanning
  multiple domains) whose verifier pytest is guaranteed reward=0 for a purely
  infrastructural reason (missing test dependency), independent of solution
  quality.
- regression_risk: very low. Guard is `|| true`-terminated and runs the
  original command unchanged after `;`; no-op when `requests` already imports;
  fires at most once per task, only on armed network/service tasks, so
  non-service passing clusters are untouched. If offline, `pip install` fails
  silently → task behaves exactly as today (status quo, no new failure).
- cost_shift: negligible. One fast `python3 -c 'import requests'` probe (plus at
  most one quiet `pip install requests`) prepended to a single Bash call on
  armed tasks only; no extra model turns, no extra tokens in message history.
- rollback_trigger: revert if any previously-passing network/service task
  regresses to F attributable to the prepended guard, or if replay fails on the
  guard.

### Why this differs from R1's accepted h_verifier_dep_requests_v1

R1's `h_verifier_dep_requests_v1` was accepted (not reverted), but the
`current_config` for this proposal is R0, which does **not** contain the
ensurer — so the assigned focus task_000106 is still broken under the config I
am evolving. This candidate re-establishes the mechanism AND broadens the
arming predicate: R1 required BOTH a service keyword AND a listen surface
(logical AND); this version arms on EITHER (logical OR) and adds route/host
patterns (`/api`, `/author`, `127.0.0.1`, PUT/DELETE routes, `serve`/`serving`).
The AND requirement risks missing service tasks that name an endpoint without a
canonical "server" keyword (or vice versa); since the guard is a harmless no-op
when `requests` is already present and `|| true`-terminated when offline,
arming slightly more broadly is strictly safer than arming too narrowly and
leaving a collection-abort task uncovered.
