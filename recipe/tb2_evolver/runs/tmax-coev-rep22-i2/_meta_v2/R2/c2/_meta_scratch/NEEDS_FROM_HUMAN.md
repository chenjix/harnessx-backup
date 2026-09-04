# Needs from human — R2/c2

## Assigned focus is an infrastructure failure, not a harness gap

`task_000028_7fe033ac` did not fail inside the agent loop. Its `result.json`:

```
"status": "error",
"elapsed_s": 0.1,
"error": "RuntimeError: docker run failed ... docker: Error response from daemon:
          Conflict. The container name \"/tmax-task0000287fe033ac-1788232928\" is
          already in use by container \"fa97a2da6187...\". You have to remove
          (or rename) that container to be able to reuse that name."
```

There is **no trajectory** (only `result.json`, no `messages.json`): the agent
loop never executed a single step. The container failed to start.

## Scope: this is systemic, not a one-off

26 of 137 tasks in this round (~19%) errored with the **identical** docker
container-name conflict, all with `elapsed_s ~= 0.1` and no trajectory:

```
task_000010_644ab1c2  task_000015_89886d8d  task_000028_7fe033ac
task_000140_01c78b42  task_000264_ab8c7253  task_000396_e56917e2
task_000505_50b5162d  task_000748_c9807703  task_000933_1f27096a
task_001032_1adaccb9  task_001090_c61c71f2  task_001781_529727cf
task_001937_ac874115  (each appears once per shard; 26 result rows total)
```

Note these include R1's targets (`task_000010`, `task_001090`) and R2's OCR
targets (`task_000015`, `task_000505`) — prior interventions could not even be
evaluated this round because the containers never started.

## Root cause (read-only code, outside meta-agent write scope)

`recipe/tmax_eval/docker_env.py::start_container`:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
proc = _run(["docker","run","-d","--name",name, ...])
```

The container name is `task_id[:20] + second-granularity timestamp`. On retry of
the same task within the same wall-clock second, or when a prior container was
not reaped (`stop_container` failed / was skipped), the name collides and
`docker run` returns a `Conflict`. `start_container` does not `docker rm -f` the
stale name or retry with a fresh suffix.

## Why the meta-agent cannot fix this

`run_eval.run_one` calls `docker_env.start_container()` **before**
`harness_runner.run_harness_agent()`. The `HarnessConfig` I control (processors,
`tool_registry`, system prompt) is only loaded inside `run_harness_agent`, which
runs after the container is up. No processor / tool / template hook fires before
`docker run`, so a name conflict is structurally unreachable by any artifact in
my write scope (`output_dir/` + `memo_path`).

## Suggested fix (for a human, in read-only `recipe/tmax_eval/docker_env.py`)

Any one of:
1. On `Conflict` from `docker run`, `docker rm -f <name>` then retry once.
2. Use a collision-proof suffix: `f"tmax-{tid[:16]}-{uuid.uuid4().hex[:12]}"`
   (or append `os.getpid()` + a monotonic counter) instead of `int(time.time())`.
3. Reap leftover `tmax-*` containers (`docker ps -aq --filter name=tmax-`) before
   each run / at eval startup.

## This round's decision

Explicit **no-op**: `config.yaml` copied byte-for-byte from `current_config`.
The evidence does not support any harness-config change; inventing a processor
for a pre-loop docker failure would be a fabricated gap and would not touch the
failure path.
