# NEEDS FROM HUMAN — R2/c1

## Assigned focus `task_000015_89886d8d` is an infra failure, not a harness gap

`result.json` for the assigned task:

```
status  = "error"
reward  = 0
elapsed_s = 0.1        # agent NEVER ran
error   = RuntimeError: docker run failed for task_000015_89886d8d:
          docker: Error response from daemon: Conflict.
          The container name "/tmax-task00001589886d8d-1788258698"
          is already in use by container "56cb97b..."
```

The agent process was never started — the run loop dies at container
creation, before any processor / tool / template can execute.

## This is systemic — 8 of 50 tasks in the round hit the SAME error

`status` distribution over the 50 trajectories:
`ok=41, agent_error=1, error=8`.

All 8 `error` tasks fail with the identical
`docker: ... Conflict. The container name ... is already in use`
message and `elapsed_s ~0.1`:

- task_000015_89886d8d (assigned)
- task_001090_c61c71f2
- task_001781_529727cf
- task_000748_c9807703
- task_000140_01c78b42
- task_000028_7fe033ac
- task_001032_1adaccb9
- task_000505_50b5162d

That is a **16% infra-loss floor** on every round — 8 guaranteed
reward-0 tasks that have nothing to do with model capability or the
harness config.

## Root cause (outside meta-agent write scope)

`recipe/tmax_eval/docker_env.py::start_container`:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

The container name is derived from the task id truncated to 20 chars
PLUS `int(time.time())` at **1-second resolution**. `run_eval.py` runs
tasks with `ThreadPoolExecutor(max_workers=args.concurrent)`
(`--concurrent` / `TMAX_CONCURRENT`). When two tasks are launched
within the same wall-clock second under concurrency > 1, they generate
the **same container name** and the second `docker run` fails with a
name Conflict. A stale/not-yet-reaped container from a prior run with a
colliding name would also trigger it.

## Why the meta-agent CANNOT fix this

`HarnessConfig` (the only thing I can write) governs the in-container
processor pipeline + system prompt. It is loaded and executed *after*
`start_container` succeeds. The failure is upstream of the run loop, in
`recipe/tmax_eval/` — which is **read-only** for the meta-agent
(write scope is limited to `output_dir/` and the learnings memo). No
processor, tool, template, or prompt change can prevent a container
from failing to start.

## Requested fix (one of)

1. **Collision-proof container name** in `docker_env.py::start_container`
   — append `uuid.uuid4().hex[:8]` (or `os.getpid()` + a monotonic
   counter) instead of / in addition to `int(time.time())`:
   ```python
   import uuid
   name = f"tmax-{task_id.replace('_','')[:16]}-{uuid.uuid4().hex[:10]}"[:63]
   ```
2. **Retry-on-conflict** in `start_container`: on a `Conflict`/name-in-use
   `docker run` failure, `docker rm -f <name>` any stale container and
   regenerate the name once before raising.
3. **Pre-run reap**: `docker rm -f` any dangling `tmax-*` containers at
   the start of `run_eval.py` so a crashed prior run cannot poison names.

Any of these removes the 16% infra-loss floor and unblocks the 8
tasks so their true reward can be measured.

## This round's decision

**Explicit no-op.** `config.yaml` copied byte-for-byte from
`current_config` (R1). There is no valid config-surface intervention
for an upstream container-start failure; inventing a processor here
would be theater that cannot touch the failing code path and would
risk regressing the 41 healthy tasks. Logged and deferred to the human
/ infra owner.
