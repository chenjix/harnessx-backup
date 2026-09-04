# Out-of-scope fix needed: Docker container-name collision in eval runner

## Summary
My assigned focus `task_000010_644ab1c2` did NOT fail due to any agent
behavior or harness-config deficiency. It failed at **container startup**
(`elapsed_s: 0.1`, `status: error`), before the agent phase ever began.
There is no episode/message log — only `result.json` with a `docker run`
error.

## Root cause (evidence)
`result.json` error:
```
RuntimeError: docker run failed for task_000010_644ab1c2:
docker: Error response from daemon: Conflict. The container name
"/tmax-task000010644ab1c2-1788106887" is already in use by container
"946d73c09cee...". You have to remove (or rename) that container to
be able to reuse that name.
```

This is NOT isolated. **All 10 `status: error` tasks in this round
(task_000010, 000028, 000118, 000140, 000505, 000748, 000933, 001032,
001781, 001937) died identically at 0.1s** with the same container-name
Conflict, and nearly identical timestamps (`1788106887`/`...888`).

The container name is constructed in `recipe/tmax_eval/docker_env.py:105`:
```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```
The `int(time.time())` suffix has **1-second resolution**. When multiple
tasks launch concurrently within the same second (parallel workers), and/or
a prior run's container was not torn down, the names collide and
`docker run --name <name>` fails with a Conflict.

## Why this cannot be fixed via HarnessConfig
`config.yaml` only evolves the in-container **processor pipeline and system
prompt** (confirmed by tb2-playbook). Those hooks fire *inside* the agent
loop, which never starts for these tasks. No processor/tool/template can
intercept a host-side `docker run` failure. This is a **runner
infrastructure bug**, not a harness deficiency and not a model capability gap.

## Requested fix (files outside my writable scope — read-only)
In `recipe/tmax_eval/docker_env.py::start_container`, make the container
name collision-proof and/or pre-clean stale containers, e.g.:
- Append a high-entropy suffix instead of second-resolution time:
  `name = f"tmax-{task_id.replace('_','')[:20]}-{uuid.uuid4().hex[:12]}"[:63]`
- OR proactively `docker rm -f <name>` (ignore-missing) before `docker run`.
- OR add `--rm` semantics + a retry-with-fresh-name on Conflict.

Until then, these 10 tasks are un-scoreable regardless of agent quality and
will keep showing up as `status: error` noise in every round.

## My action this round
Explicit **no-op**: `config.yaml` is a byte-for-byte copy of `current_config`
(R1/config.yaml). Inventing a config change to "address" a host-side Docker
collision would add pure regression risk with zero mechanistic path to the
failure. Smallest defensible edit = no edit.
