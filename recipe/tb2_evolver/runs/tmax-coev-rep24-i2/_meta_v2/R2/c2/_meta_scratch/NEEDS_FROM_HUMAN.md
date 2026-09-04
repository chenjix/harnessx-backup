# Out-of-scope infrastructure bug — cannot be fixed via HarnessConfig

## Assigned focus: task_000015_89886d8d (status=error, reward=0)

### Root cause (verified)

`result.json` for this task:
```
"status": "error", "elapsed_s": 0.1, "agent": null, "final_pytest": null,
"error": "RuntimeError: docker run failed for task_000015_89886d8d:
  docker: Error response from daemon: Conflict. The container name
  \"/tmax-task00001589886d8d-1788418459\" is already in use by container
  \"79465c...\". You have to remove (or rename) that container..."
```

The task **never entered the agent run loop** (`agent: null`, `final_pytest: null`,
`elapsed_s: 0.1`, zero messages). The crash occurs at
`recipe/tmax_eval/run_eval.py:150` → `docker_env.start_container(...)`, i.e.
*before* any processor / tool / system-prompt surface is instantiated.

### Why this is a Docker container-name collision

In `recipe/tmax_eval/docker_env.py::start_container` (line 105):
```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```
The name uniqueness relies on:
- `task_id.replace('_','')[:20]` — truncated task id (collides across tasks
  whose first 20 non-underscore chars match), AND
- `int(time.time())` — **only 1-second resolution**.

`docker run --name <name>` fails with a `Conflict` when a container of that
name already exists — which happens if (a) two tasks are dispatched within the
same wall-clock second, or (b) a prior run's container was not reaped
(`stop_container` uses `docker rm -f`, but a killed/crashed driver can leave
orphans). All **8 of 50** `status=error` tasks in this trajectory set share the
identical error string:

```
task_000010_644ab1c2  task_000015_89886d8d  task_000118_3043e92d
task_000140_01c78b42  task_000505_50b5162d  task_000933_1f27096a
task_001781_529727cf  task_001937_ac874115
```

### Why no HarnessConfig change can fix it

Per tb2-playbook: `config.yaml` controls "the processor pipeline and system
prompt, **not the benchmark infrastructure**." The container is created by the
outer eval driver before the harness loop starts. No `MultiHookProcessor`,
`@tool`, or Jinja template runs early enough to intercept `docker run`. The
naming logic lives in `recipe/tmax_eval/docker_env.py`, which is read-only for
this agent.

### Suggested infrastructure fix (for the human — NOT shippable from here)

In `recipe/tmax_eval/docker_env.py::start_container`, make the container name
collision-proof and/or clean up any pre-existing container of that name:

```python
import uuid
name = f"tmax-{task_id.replace('_','')[:20]}-{uuid.uuid4().hex[:12]}"[:63]
# and/or, before docker run:
_run(["docker", "rm", "-f", name], timeout=30)  # best-effort reap
```

Using a UUID (or PID+monotonic counter) instead of `int(time.time())` removes
the 1-second-resolution collision window entirely. Adding a best-effort
`docker rm -f <name>` before `docker run` reaps orphans from crashed prior
runs. Either change is a one-liner in infra code outside this agent's write
scope.

### Decision for this round

Explicit **no-op**: `output_dir/config.yaml` is a byte-for-byte copy of
`current_config`. There is no defensible HarnessConfig edit that touches this
failure, and drifting onto another proposal's cluster (e.g. the HTTP-`requests`
cluster already owned by R1) would violate the batch-diversity constraint.
