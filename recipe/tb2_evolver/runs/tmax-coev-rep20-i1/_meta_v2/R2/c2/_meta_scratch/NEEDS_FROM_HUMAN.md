# Needs from human — R2/c2

## Blocking infrastructure failure, NOT a harness-config gap

The assigned focus task `task_000024_a0664029` and **22 other tasks
(23/50 total)** in this trajectory batch failed *before the agent loop
ever started*, with:

```
status: error
elapsed_s: ~0.1
error: RuntimeError: docker run failed for <task>:
  docker: Error response from daemon: Conflict. The container name
  "/tmax-task<...>-<epoch>" is already in use by container "<id>".
  You have to remove (or rename) that container to be able to reuse
  that name.
```

Traceback origin: `recipe/tmax_eval/docker_env.py:124` in
`start_container`.

### Root cause

`start_container` builds the container name as:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

Two structural problems:
1. **Second-granularity timestamp** — parallel/retried launches of the
   same task within the same wall-clock second collide.
2. **20-char task-id truncation** — different task_ids sharing a
   20-char prefix would also collide (secondary risk).
   Combined with leftover containers from a crashed/killed prior run
   that were never `docker rm`-ed, the `docker run --name` conflicts.

### Why the meta-agent cannot fix this

- The failure is in `recipe/**`, which is **read-only** per SOUL.md
  hard invariant #2. I may only write `HarnessConfig` (processors /
  tools / templates / system prompt) + the journal.
- The failure happens at `elapsed_s ~0.1`, **before any container
  exists**, so **no HarnessConfig processor, tool, template, or
  system-prompt hook runs** — every lever I control binds *after* the
  container is up. There is no harness-mechanism surface exposed by
  this failure.

This is neither a harness deficiency I can patch nor a model
capability gap — it is an orchestration/infra flake.

### Suggested human fix (in read-only `recipe/tmax_eval/docker_env.py`)

- Make the container name collision-proof: add a random suffix
  (`uuid4().hex[:8]`) instead of / in addition to `int(time.time())`,
  and/or use full task_id hashing rather than a 20-char prefix.
- Pre-flight cleanup: `docker rm -f <name>` (ignore-missing) before
  `docker run`, or add `--rm` semantics + name reuse guard.
- Ensure `stop_container` runs in a `finally` even when the run loop
  raises, so names are freed on crash.

Until this is fixed, ~46% of this batch will keep failing at t=0 and
those rows are **uninterpretable** as harness signal — they should be
excluded from pass-rate attribution or re-run.
