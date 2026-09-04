# Needs from human — infrastructure fix outside HarnessConfig scope

## Assigned focus is an eval-runner bug, not a harness deficiency

`task_000106_23215092` (my assigned focus) failed with:

```
"status": "error",
"reward": 0,
"elapsed_s": 0.6,
"error": "RuntimeError: docker run failed for task_000106_23215092:
  docker: Error response from daemon: Conflict. The container name
  \"/tmax-task00010623215092-1788195286\" is already in use by container
  \"6fef56b0...\". You have to remove (or rename) that container..."
```

The trajectory directory contains **only** `result.json` — no `agent/`,
no episode JSONL, no tool calls, no system prompt exchange. The container
never started, so the task agent never ran. This is impossible for any
`HarnessConfig` change (processors / system prompt / tool registry) to
affect: the entire evolvable surface runs *inside* the container, which
here never came up.

## Scope of the problem

**21 of 129 tasks (16%) in this round failed identically** — all with
`status: error`, `elapsed_s ~0.5–0.9s`, and the same
"container name already in use" Docker conflict. Examples:
`task_000010_644ab1c2`, `task_000069_41f1682c`, `task_000106_23215092`,
`task_000111_cbada64a`, `task_000118_3043e92d`, ... (21 total via
`grep -l 'already in use by container' <traj>/task_*/result.json`).

Note: `task_000069_41f1682c` was a *predicted_affected* target of R1's
`StrictSelfVerifyProcessor` bet. In this round it did not run at all due
to the same container conflict — so R1's attribution for that task is
confounded by infra, not by the processor change.

## Root cause (in read-only `recipe/tmax_eval/docker_env.py`)

`start_container()` names the container:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

The uniqueness suffix is `int(time.time())` — **1-second resolution**.
When the same task is (re)started within the same wall-clock second as a
prior container that was not yet `docker rm`'d — e.g. a retry after a
crash, or two workers colliding — the name collides and `docker run`
hard-fails before the agent boots.

## Requested fix (human — outside my write scope of output_dir/ + memo)

Make the container name collision-proof and/or clean up stale names.
Any of:

1. Add a high-entropy suffix:
   `name = f"tmax-{task_id.replace('_','')[:16]}-{uuid.uuid4().hex[:12]}"[:63]`
   (drop the timestamp, or keep it plus the uuid).
2. Pre-remove any existing container with the target name before
   `docker run` (`docker rm -f <name>` guarded by name existence), or
   pass `--rm` semantics / retry-with-new-name on the specific
   "name in use" daemon error.
3. Ensure `stop_container()` runs in a `finally` on every path
   (including the error path) so names are always freed.

I did **not** change `config.yaml` this round (explicit byte-for-byte
no-op) because there is no harness lever that can influence a task whose
container never starts. Chasing this in the processor pipeline would be
pure noise and risk regressing the 108 tasks that *do* run.
