# Needs from human — infrastructure fix outside HarnessConfig scope

## Assigned focus: `task_000118_3043e92d` — infrastructure error, NOT a harness capability gap

### Diagnosis

`task_000118_3043e92d.result.json`:

```
"status": "error",
"reward": 0,
"elapsed_s": 0.1,
"error": "RuntimeError: docker run failed ...: docker: Error response from daemon:
  Conflict. The container name \"/tmax-task0001183043e92d-1788106888\" is already in
  use by container \"a077fa0d...\". You have to remove (or rename) that container ..."
```

The task failed in **0.1s, before the agent ever ran** — no trajectory, no tool
calls, no system prompt applied. The failure is raised in
`recipe/tmax_eval/docker_env.py::start_container`, called from
`recipe/tmax_eval/run_eval.py::run_one`, i.e. *before* the HarnessConfig
processors/tools/prompt are ever wired in.

### Root cause (systemic — 10/50 tasks, 20% of the batch)

`start_container` builds the container name as:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

`int(time.time())` has **whole-second granularity**, and the task-id prefix is
truncated to 20 chars. When two tasks launch within the same wall-clock second
(concurrent workers, or a retry landing on the same second) the generated names
collide, and `docker run --name` fails with the "container name already in use"
Conflict. The timestamps in the failing tasks confirm this: `-1788106887` and
`-1788106888` (adjacent seconds), and there is no per-container uniqueness beyond
the second.

Affected tasks this round (all identical `status=error`, `elapsed_s=0.1`,
"container name ... already in use"):

- task_000118_3043e92d  (assigned focus)
- task_000010_644ab1c2
- task_000028_7fe033ac
- task_000140_01c78b42
- task_000505_50b5162d
- task_000748_c9807703
- task_000933_1f27096a
- task_001032_1adaccb9
- task_001781_529727cf
- task_001937_ac874115

### Why this cannot be fixed in HarnessConfig

- Container naming/launch happens in `recipe/tmax_eval/docker_env.py` and
  `recipe/tmax_eval/run_eval.py`, which are **read-only** (SOUL invariant #2:
  writes only to `output_dir/` and `memo_path`; `recipe/**` is read-only).
- The HarnessConfig surface (processors, tool_registry, system prompt) is only
  invoked *inside* the run loop, which never starts for these tasks. No
  processor `_hook_`, tool, or prompt edit can run before/around
  `start_container`. There is literally no lever in the config that touches
  Docker container naming.

This is therefore a harness **infrastructure** bug, not a HarnessConfig
capability gap and not a model capability gap. Shipping any config change would
be drift onto invented territory (SOUL: stay on your focus; if the focus is
unsupported by a config-level fix, make the smallest defensible edit — here, a
byte-for-byte no-op).

### Recommended fix (for the human — in `recipe/tmax_eval/docker_env.py`)

Make the container name collision-proof, e.g.:

```python
import uuid
name = f"tmax-{task_id.replace('_', '')[:12]}-{uuid.uuid4().hex[:12]}"[:63]
```

or add a monotonic counter / PID, or use `time.time_ns()` instead of
`int(time.time())`. Optionally also: before `docker run`, `docker rm -f <name>`
any pre-existing container with the same name, and/or retry `start_container`
once on a Conflict error with a freshly-generated name. Any of these removes the
whole-second collision window.

Until this lands upstream, ~20% of tasks in this benchmark will intermittently
score 0 for reasons unrelated to agent capability, adding noise to every evolve
round's attribution.
