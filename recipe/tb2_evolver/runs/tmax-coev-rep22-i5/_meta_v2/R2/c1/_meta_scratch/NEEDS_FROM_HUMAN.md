# Needs from human — infra bug outside HarnessConfig scope

## Assigned focus: task_000015_89886d8d (and 4 siblings) — docker container-name collision

**Not fixable via HarnessConfig.** The failure is pre-loop infrastructure.

### Evidence
`task_000015_89886d8d.result.json`:
- `status: "error"`, `reward: 0`, `elapsed_s: 0.1`
- No `.messages.json` trajectory exists — the agent never ran a single step.
- `error`: `docker run failed ... Conflict. The container name "/tmax-task00001589886d8d-1788321907" is already in use by container "f61b2..."`
- `traceback` points at `recipe/tmax_eval/run_eval.py:150` →
  `recipe/tmax_eval/docker_env.py:124` (`start_container`).

Same root cause hit **5 tasks** in this round (all `status=error`,
`elapsed_s=0.1`, identical "already in use by container" message):
- task_000015_89886d8d
- task_000028_7fe033ac
- task_000140_01c78b42
- task_001653_c4cafa73
- task_001781_529727cf

### Root cause
`docker_env.py::start_container` builds the container name as:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

The suffix has **1-second resolution**. Two tasks launched within the same
wall-clock second — or a stale container left over from a prior/aborted run —
produce a colliding `--name`, so `docker run` fails with a name conflict before
the harness loop starts.

### Why the meta-agent cannot fix this
The editable HarnessConfig surface (`processors`, `tool_registry`,
`system_prompt.txt`, templates) runs **inside** the agent loop. Container
creation/cleanup happens in `recipe/tmax_eval/docker_env.py`, which is
read-only and outside `output_dir`. No processor/tool/prompt can intercept a
docker-daemon name conflict that occurs at `elapsed_s=0.1` with zero agent
turns.

### Suggested fix (for the recipe owner, not the harness config)
In `recipe/tmax_eval/docker_env.py::start_container`, make the name collision-proof
and/or clean up stale containers:
- Use a high-entropy suffix instead of `int(time.time())`, e.g.
  `uuid.uuid4().hex[:12]` or `time.time_ns()`.
- Best-effort `docker rm -f <name>` before `docker run`, or add
  `--rm`-style pre-cleanup / retry-on-conflict in `start_container`.
- Ensure `stop_container` always runs on the previous task before the next
  `start_container` (finally-block cleanup) to avoid stale leftovers.

This round ships an **explicit no-op** (config copied byte-for-byte from R1)
because there is no defensible HarnessConfig edit for a pre-loop infra fault.
