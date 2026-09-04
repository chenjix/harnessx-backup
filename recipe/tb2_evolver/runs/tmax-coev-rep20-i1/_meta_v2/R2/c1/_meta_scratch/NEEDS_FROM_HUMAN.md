# NEEDS_FROM_HUMAN — R2/c1

## Assigned focus is NOT fixable by HarnessConfig

**Focus task:** `task_000015_89886d8d` (assigned failing task).

### Diagnosis (verified from result.json, this round's trajectory set)

`task_000015_89886d8d.result.json`:
```
"reward": 0, "status": "error", "elapsed_s": 0.2,
"error": "RuntimeError: docker run failed for task_000015_89886d8d:
   docker: Error response from daemon: Conflict. The container name
   \"/tmax-task00001589886d8d-1788094866\" is already in use by
   container \"021a9985...\". You have to remove (or rename) that
   container to be able to reuse that name."
```

The task **never started the agent loop** (`elapsed_s = 0.2`, no steps,
no messages). The failure is a Docker **container-name collision** in
the orchestration layer, thrown from
`recipe/tmax_eval/docker_env.py::start_container` (called at
`recipe/tmax_eval/run_eval.py::run_one` line 149) — *before* the
`HarnessConfig` (processors / tools / templates / system prompt) is ever
consumed.

### This is systemic, not a one-off

23 of 50 tasks in this trajectory set failed with the identical signature
(`status=error`, `docker run failed ... container name ... already in
use`), all at `elapsed_s ≈ 0.2`:

```
task_000015, task_000684, task_001697, task_000505, task_000863,
task_000028, task_000264, task_001264, task_000760, task_000140,
task_001653, task_001701, task_000912, task_000329, task_001706,
task_000536, task_000206, task_001781, task_001089, task_001761,
task_000338, task_000024, task_000396
```

Note that several of these (task_000028, task_000264, task_000206,
task_000024, task_000536, task_000015) are the very tasks the R1 journal
believed were `budget_exceeded` no-progress loops and targeted with the
`CyclicLoopGuard` / `OutputStallRecoveryProcessor` proposals. In THIS run
those tasks did not run at all — they crashed at container start. The R1
loop-processor evidence must have come from an earlier trajectory set;
against the current infra state those levers cannot fire because the
agent loop never boots.

### Root cause

`docker_env.py::start_container` builds the container name as:
```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```
- `int(time.time())` is **second-resolution**. Two tasks launched in the
  same wall-clock second get the same name suffix (observed: both
  task_000015 and task_000684 got suffix `-1788094866`).
- More importantly, `start_container` does **not** remove a pre-existing
  container of the same name before `docker run --name`. A stale
  container left behind by a killed/crashed prior run (or a same-second
  sibling) makes `docker run` fail with `Conflict`, and there is no
  retry / `docker rm -f` fallback.

### Why no HarnessConfig fix is possible

`HarnessConfig` governs only in-loop agent behavior (processors fire on
step/tool events; tools/templates shape the agent). Container lifecycle
is orchestration code in `recipe/tmax_eval/` which is **read-only** for
this meta-agent (Hard Invariant #2). There is no processor/tool/template
hook that runs before `start_container`. Any config edit would be pure
theater — it cannot touch this code path.

### Requested human fix (in `recipe/tmax_eval/docker_env.py`)

Make `start_container` collision-proof, e.g.:
1. Pre-remove any existing container with the target name:
   `docker rm -f <name>` (ignore errors) before `docker run`, **or**
2. Use a collision-free name (add a monotonic counter / PID / uuid4 hex
   suffix instead of / in addition to second-resolution `time.time()`),
   **and**
3. On `docker run` `Conflict`, retry once after `docker rm -f`.

Until this is fixed, ~46% of the benchmark is a false-negative floor that
no config evolution can lift, and per-task focus assignments that land on
one of the 23 error tasks are un-actionable at the config layer.

### Decision this round

Explicit **no-op**: `config.yaml` copied byte-for-byte from
`current_config` (R1/config.yaml). Canonicalize: `{"ok": true}`.
