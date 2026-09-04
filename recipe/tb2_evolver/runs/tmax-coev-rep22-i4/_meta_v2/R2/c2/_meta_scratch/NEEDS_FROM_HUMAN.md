# NEEDS FROM HUMAN — infrastructure fix outside HarnessConfig scope

## Summary

The assigned focus task `task_000028_7fe033ac` failed this round for a reason
that **cannot be fixed by any HarnessConfig surface** (processors / tools /
templates / system prompt). It is a docker orchestration bug in the eval runner.

## Evidence

`task_000028_7fe033ac.result.json`:
```
"reward": 0,
"status": "error",
"elapsed_s": 0.1,
"error": "RuntimeError: docker run failed for task_000028_7fe033ac: docker:
  Error response from daemon: Conflict. The container name
  \"/tmax-task0000287fe033ac-1788298078\" is already in use by container ...
  You have to remove (or rename) that container to be able to reuse that name."
"traceback": ... recipe/tmax_eval/docker_env.py, line 124, start_container ...
```

- `elapsed_s = 0.1` and there is **no `.messages.json`** and an empty task dir
  (only `result.json`) — the agent never launched. This is not a model or
  harness-capability failure; the container failed to start.

- **This is not isolated**: 9 of the 50 tasks in this trajectory set failed
  with the identical `Conflict` container-name error at `docker run` time:
  `task_000015, task_000028, task_000140, task_000264, task_000396,
   task_000505, task_000748, task_001032, task_001090`.
  All 9: `status=error`, `elapsed_s≈0.1`, reward 0. That is ~18% of the round
  being zeroed by infrastructure, silently attributed to the harness/model.

## Root cause

`recipe/tmax_eval/docker_env.py::start_container`:
```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
docker run -d --name <name> ...
```
The container name uses `int(time.time())` (1-second resolution). When two
tasks start within the same wall-clock second — or when a prior run's
container with the same name was not removed (no pre-run `docker rm -f` and
`docker run` is not retried on name conflict) — the name collides and
`docker run` hard-fails. No retry, no unique suffix, no cleanup-then-retry.

## Why the meta-agent cannot fix this

`recipe/tmax_eval/docker_env.py` is **read-only** — outside `output_dir` and
outside every surface the meta-agent may edit (processors / tool_registry /
system prompt). A `MultiHookProcessor` runs *inside the agent loop*, which
only exists *after* the container starts; it can never intercept a container
that failed to launch. There is no HarnessConfig knob for container naming or
docker retry.

## Suggested fix (human, in the read-only recipe layer)

In `recipe/tmax_eval/docker_env.py::start_container`, one of:
1. Add a process-unique suffix to the container name (e.g. append
   `uuid.uuid4().hex[:8]` or `os.getpid()` in addition to the timestamp), and/or
2. Best-effort `docker rm -f <name>` before `docker run`, and/or
3. Retry `docker run` with a fresh name on a `Conflict` error.
Any of these removes the collision class and would recover all 9 zeroed tasks.

## Meta-agent decision this round

**Explicit no-op.** The current R1 config is copied byte-for-byte to
`output_dir/config.yaml`. Shipping a processor/tool/template change here would
be drifting off-focus onto an unrelated capability gap (SOUL.md forbids
task-specific injection and the brief forbids drifting onto another proposal's
territory). The R1 `HttpVerifierDepProcessor` diagnosis for this task is
**superseded**: in *this* trajectory set the task never reached the verifier —
it never started. No harness edit is defensible on this evidence.
