# Candidates — R1/c3

Assigned focus: `task_000028_7fe033ac` fails.

## Diagnosis (verified against trajectory bodies)

`task_000028_7fe033ac` (system_administration): the agent solved the task
**correctly**. It fixed `nginx.conf`, rewrote `server.cpp` to shell out to
`ffprobe`, compiled, ran the backend in the background, fixed the
socket-permission 502, created `logrotate.conf`, and confirmed
`curl`-equivalent (`/dev/tcp`) returned `HTTP/1.1 200 OK` with body `150`.
`initial_pytest.passed = true`, all requirements met.

The task still scored `reward=0`. Root cause is in `final_pytest`:

```
/tmp/test_final_state.py:6: in <module>
    import requests
E   ModuleNotFoundError: No module named 'requests'
!!! Interrupted: 1 error during collection !!!
```

The **verifier's own test module** imports `requests` to make HTTP calls
against the agent's service, and `requests` is not installed in the
container's Python. pytest fails at *collection* — so a fully-correct
solution scores 0 regardless of quality.

### This is a generalizable cluster, not a one-off

`grep "No module named 'requests'"` across all r0 result.json files hits
**7 tasks**, spanning domains system_administration, data_querying,
data_processing, software_engineering, and debugging:

- task_000028_7fe033ac, task_000106_23215092, task_000809_760d7fa0,
  task_000939_1592be48, task_000958_4bb2b05d, task_001857_24daeef3,
  task_002063_8c8adcfe

All fail identically at pytest collection because `test_final_state.py`
does `import requests`.

### Why this is a harness deficiency (not a model capability gap)

- The verifier phase runs pytest against the container's *final state*;
  its test files are injected after the agent exits (per tb2-playbook).
  The agent cannot read them during execution.
- The agent's own verification used `/dev/tcp`, `curl`, `wget` — it never
  needs `requests`, so it has no local signal that `requests` is missing.
- **`pip install` works in this environment.** Verified in
  task_000106_23215092: the agent successfully ran
  `pip3 install numpy` and `pip3 install scipy` (wheels downloaded from
  PyPI at 50-75 MB/s). So the tb2-playbook "internet blocked" prior does
  NOT hold for tmax — dependency provisioning is achievable.
- Therefore the failure is fixable by a **harness-level strategy nudge**:
  when a task produces an artifact/service that an external automated
  verifier will probe, the agent should make the runtime environment
  robust for that verification — including ensuring the common Python
  client library used by such verifiers (`requests`) is importable.

---

## Candidate C-001 — augment system prompt with "verifier-environment robustness" strategy

- **lens / lever / intent**: environment-completeness lens / system-prompt
  lever (SiblingSystemPromptBuilder sidecar) / close a cross-domain
  verifier-collection-failure cluster.
- **Schema / mechanism**: `SiblingSystemPromptBuilder` reads
  `system_prompt.txt` next to the active config YAML (verified in
  `recipe/tmax_eval/prompt_builder.py::_resolve_prompt_path`). Ship an
  augmented `output_dir/system_prompt.txt` sidecar. Config otherwise
  byte-identical to R0.
- **Signal**: 7 tasks scoring 0 solely due to `import requests` failing at
  verifier pytest-collection; agent solutions were otherwise correct.
- **Verified body evidence**: task_000028 trajectory shows a correct
  solution + `final_pytest` ImportError; task_000106 shows `pip install`
  succeeding against PyPI in-container.
- **Retroactive check (would-this-have-helped)**: had the agent, at task
  completion, ensured `requests` was importable in the system Python
  (`python3 -c "import requests" || pip install requests`), the verifier's
  `test_final_state.py` would have collected and run its HTTP assertions
  against the already-working service → likely pass. Same mechanism flips
  the other 6 collection-failure tasks.
- **Generalization test**: the guidance is phrased as a class-level
  strategy ("ensure the environment an external verifier will use is
  complete; standard client libraries such as `requests` should be
  importable"). It contains no task IDs, no socket paths, no frame
  counts, no per-task constants. It helps any unseen task where an
  external verifier probes a produced service/artifact via Python.
- **Why system-prompt lever, not a processor**: a processor cannot know
  what the verifier's injected test file imports (files absent during the
  agent phase), so it cannot reliably decide when/what to install. A
  general strategy nudge lets the model apply judgment per task. A
  blanket "always pip install requests" processor would waste time/tokens
  on tasks with no verifier HTTP probing and risks mutating unrelated
  runs. The prompt keeps the decision with the model where the context
  lives.

### Pareto statement

- **expected_global_gain**: the `import requests` collection-failure
  cluster (7 tasks in r0) plus any future service/HTTP tasks whose
  verifier uses `requests`. These are pure infrastructure-completeness
  losses on otherwise-correct solutions — high-value to recover.
- **regression_risk**: low. The addition is advisory strategy text, not a
  mandate to install on every task. Worst case: a few extra Bash calls
  (`python3 -c "import requests"`) on tasks that don't need it, costing a
  small number of tokens. No processor/tool code paths change, so no
  crash surface added; replay risk is minimal (config binds identically
  to R0, only the sidecar text differs).
- **cost_shift**: slightly positive token cost (a short verification/
  install step on service-type tasks); negligible on non-service tasks
  where the model won't trigger it. Net expected value strongly positive
  given 7 recoverable zeros.
- **rollback trigger**: if next round shows the cluster unchanged AND a
  net pass-rate drop vs R0, revert the sidecar to the R0 prompt.
