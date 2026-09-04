# Needs from human — infra fix outside evolvable scope

## Stale Docker container name conflict blocks 19/50 tasks

The assigned focus task `task_000140_01c78b42` — and 18 other tasks in
the same round — never run the agent. They fail at container startup
with:

```
RuntimeError: docker run failed: docker: Error response from daemon:
Conflict. The container name "/tmax-task...-<ts>" is already in use by
container "<id>". You have to remove (or rename) that container to be
able to reuse that name.
```

`elapsed_s ≈ 0.7`, `agent: null`, no `messages.json` — the agent phase
never begins, so no `HarnessConfig` processor/prompt/tool intervention
can address it.

### Fix location (read-only to meta-agent)

`recipe/tmax_eval/docker_env.py::start_container` — before `docker run`,
remove any pre-existing container with the target name, e.g.:

- `docker rm -f <name>` (or `docker container rm --force`) prior to
  `docker run`, or
- pass `--rm` and ensure prior runs' containers are reaped, or
- add a unique suffix / retry-with-cleanup on the `Conflict` error.

### Affected tasks (all identical error)

task_000028, task_000140, task_000264, task_000329, task_000338,
task_000505, task_000536, task_000748, task_000760, task_000863,
task_000912, task_000956, task_001088, task_001264, task_001653,
task_001697, task_001701, task_001706, task_001761.

Until the runner reaps stale containers, these tasks will keep scoring
0 regardless of any harness-config evolution. This depresses the
apparent pass-rate and pollutes attribution for every evolve round.
