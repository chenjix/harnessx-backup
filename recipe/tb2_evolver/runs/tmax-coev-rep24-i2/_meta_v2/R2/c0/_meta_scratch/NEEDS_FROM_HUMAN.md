# Needs from human — infra bug outside evolvable scope

## Symptom

Assigned focus `task_000010_644ab1c2` (and 7 sibling tasks) failed with
`status: error`, `elapsed_s: 0.1`, `reward: 0`. The run loop never started;
no agent step, no processor, no verifier phase.

Error (identical across all 8):

```
RuntimeError: docker run failed for task_000010_644ab1c2:
docker: Error response from daemon: Conflict. The container name
"/tmax-task000010644ab1c2-1788418459" is already in use by container
"ee1e8516e57b...". You have to remove (or rename) that container to be
able to reuse that name.
```

All 8 affected tasks (same error, all `elapsed_s ~0.1`):
`task_000010_644ab1c2`, `task_000015_89886d8d`, `task_000118_3043e92d`,
`task_000140_01c78b42`, `task_000505_50b5162d`, `task_000933_1f27096a`,
`task_001781_529727cf`, `task_001937_ac874115`.

## Root cause

`recipe/tmax_eval/docker_env.py::start_container` (line ~105):

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

Container name is keyed on `int(time.time())` — **second resolution**. When
`run_eval.py`'s `--system-error-retries` loop (line ~414) re-submits a task
within the same wall-clock second as a prior attempt, or when a previous
container holding that name was not `docker rm -f`'d, `docker run --name`
collides and raises `Conflict`. There is no pre-run `docker rm -f <name>`
and no per-attempt name uniqueness (no PID / UUID / nanosecond suffix).

## Why the meta-agent cannot fix this

The evolvable surface (processors, tools, templates, config knobs) executes
**inside** the run loop. `start_container` runs **before** the run loop
begins — the container does not yet exist, so no `MultiHookProcessor` hook
(`on_before_model`, `on_after_model`, tool hooks) has fired. `elapsed_s: 0.1`
confirms the loop never started. This is a benchmark-infrastructure race
condition, not a harness-mechanism deficiency.

`recipe/tmax_eval/docker_env.py` and `run_eval.py` are outside the
meta-agent's writable scope (`output_dir/` + `memo_path` only).

## Suggested human fix (in read-only infra)

In `docker_env.py::start_container`, make the name collision-proof and/or
idempotent. Any one of:

1. Add uniqueness: `name = f"tmax-{tid[:16]}-{os.getpid()}-{uuid.uuid4().hex[:8]}"[:63]`
   (nanosecond `time.time_ns()` also works).
2. Pre-clear: `_run(["docker", "rm", "-f", name])` before `docker run`.
3. Pass `--rm` semantics + retry-with-fresh-name on `Conflict`.

Until then these 8 tasks are unwinnable by any harness change and should be
treated as infra flakes (retried by the orchestrator), not scored as agent
failures.
