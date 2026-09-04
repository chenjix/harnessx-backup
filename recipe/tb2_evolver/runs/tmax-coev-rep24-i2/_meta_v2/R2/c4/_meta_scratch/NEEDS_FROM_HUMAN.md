# Needs from human — infra bug outside writable scope

## `task_000118_3043e92d` fails with a Docker container-name collision

`result.json`: `status:error`, `elapsed_s:0.1`, no messages produced.

```
RuntimeError: docker run failed for task_000118_3043e92d: docker: Error
response from daemon: Conflict. The container name
"/tmax-task0001183043e92d-1788418459" is already in use by container "895777cc..."
  at recipe/tmax_eval/docker_env.py:124 (start_container)
```

### Root cause

`recipe/tmax_eval/docker_env.py:105`:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

The uniqueness suffix is only 1-second-granular wall-clock time. In a
repeated sweep (`rep24`) two runs of the same `task_id` that start in the
same second collide on the container name → Docker refuses the second
`docker run` → the task hard-errors before the run loop starts.

### Fix (must be done by a human — `recipe/**` is read-only for the meta-agent)

Any of:
1. Add a high-entropy suffix to the name, e.g.
   `f"...-{int(time.time())}-{os.getpid()}-{uuid.uuid4().hex[:6]}"[:63]`.
2. Before `docker run`, best-effort `docker rm -f <name>` to clear a
   stale container of the same name.
3. Use `--rm` semantics + a monotonic counter per process.

### Why the meta-agent cannot fix it

The HarnessConfig surface (processors / tools / system prompt) is only
exercised *inside* the run loop, which never starts here. No processor
hook fires before `docker run`. The bug is pure orchestration
infrastructure in a read-only path.
