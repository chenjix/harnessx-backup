# Candidates — R4 c3

## Candidate C-001 — VerifierDepEnsurer (ensure `requests` for HTTP-service verifiers)

- **Three-axis tag**: lens=verifier-phase-failure / lever=control / intent=corrective
- **Assigned focus**: `task_000028_7fe033ac` (system_administration)

### Signal

`task_000028_7fe033ac` fails reward=0 despite `initial_pytest.passed=true` and
`exit_reason=done` at 26 steps. The failure is entirely in the verifier phase:
the injected `test_final_state.py` begins `import requests`, the base image lacks
`requests`, so pytest aborts at **collection**
(`ModuleNotFoundError: No module named 'requests'` →
`Interrupted: 1 error during collection`) and every assertion errors at once
regardless of solution correctness.

### Verified body evidence

- `task_000028_7fe033ac.result.json`: `initial_pytest.passed=true`;
  `final_pytest.passed=false, rc=2`; output_tail is the `import requests`
  collection abort (verified in file).
- Task is an Nginx reverse proxy + C++ HTTP socket backend on 127.0.0.1:8080 —
  a canonical HTTP-service task the verifier drives over HTTP.
- Transcript: the only "requests" token in `task_000028.messages.json` is the
  English word in the prompt ("routes requests to…"); the agent never
  `pip install`ed the Python `requests` module (`grep -c "pip install requests" = 0`).
- **Cluster**: `grep -l "No module named 'requests'" *.result.json` returns **6**
  tasks — task_000028 (system_administration), task_000106 (data_querying),
  task_000939 (software_engineering), task_000958 (data_querying),
  task_001857 (debugging), task_002063 (data_processing). All HTTP-service
  tasks, all identical collection abort, none installed requests. Spans ≥3
  distinct domains → generalizable class, not a single-task patch.
- pip works in-run: `grep -l "Successfully installed" *.messages.json` includes
  task_000106/task_000378/task_000790 — installing the dep is viable.

### Arming verification (measured against all 50 trajectories)

Regex arms on SERVICE-surface **AND** LISTEN-surface. Verified:
- All 6 collection-abort tasks arm (`ARMED=True` for each).
- Only **1 passing task arms** (task_001382_6c9d34ea) — and the guard there is
  behavior-preserving (short-circuits if requests present, else silent
  `pip install … || true` then runs the original command unchanged after `;`).
- Other armed-failing tasks (task_000011/000378/000683/001035/001694/001937)
  fail for unrelated reasons; the guard is a pure no-op for them.

### Retroactive check (variant: "would the mechanism have flipped the signal?")

If the guard had been present during the run, `python3 -c 'import requests'`
would fail, `pip install -q requests` would succeed (pip proven working
in-run), and `test_final_state.py` would collect. Since `initial_pytest`
passed and the agent built a functionally complete service, at least the
collection abort — the *sole* observed blocker on this cluster — is removed.
Answer: **yes** for the collection-abort class (residual assertion failures, if
any, reclassify as capability gaps to log, not a regression).

### Why control (mechanism) not instruction/configuration

The agent cannot see the verifier's test file during the agent phase (TB2
sandbox topology), so no system-prompt instruction ("install requests") could
be justified without leaking verifier internals, and it would misfire on the
~44 non-service tasks. No configuration knob toggles a missing dependency. The
only correct surface is a runtime interception that ensures the dependency
exists — a Control-lever `MultiHookProcessor`.

### Why this lineage needs it now

The `current_config` I evolve from (R1/config.yaml) is R0-lineage and has **no**
VerifierDepEnsurer registered (verified — no such `_target_` in the file). The
mechanism was accepted in sibling R1/R3 lineages but never landed here, so the
focus task and its 5 siblings are still guaranteed reward=0 under this config.

### Pareto statement

- `expected_global_gain`: unblocks a 6-task HTTP-service cluster (≥3 domains)
  whose verifier aborts at collection on a missing dep — currently guaranteed
  reward=0 for a purely infrastructural reason independent of solution quality.
  Generalizes to any unseen HTTP-service task with the same verifier pattern.
- `regression_risk`: very low. Guard is idempotent, silent, `|| true`-terminated;
  no-op when requests already imports; fires ≤1×/task, only on armed
  service+listen tasks. Exactly 1 passing task arms (task_001382) and the guard
  is behavior-preserving there. Offline image → pip fails silently → status quo.
  Never mutates message history (contract-clean).
- `cost_shift`: negligible. One fast import probe (+ at most one quiet pip
  install of a small pure-Python wheel) prepended to a single Bash call on armed
  tasks only; no extra model turns.
- `rollback_trigger`: revert if any previously-passing service task (esp.
  task_001382_6c9d34ea) regresses to F attributable to the guard, or if
  synthetic replay fails on the processor.
