# Needs from human — infra fix outside meta-agent write scope

## Docker container-name collision (23/50 tasks in this round errored)

`recipe/tmax_eval/docker_env.py::start_container` (line ~105) names the
container:

    name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]

In this trajectory set, 23 of 50 tasks (including the assigned focus
`task_000028_7fe033ac`) failed at container launch with:

    docker: Error response from daemon: Conflict. The container name
    "/tmax-task<id>-1788094866" is already in use by container "<hash>".

All 23 share the identical `-1788094866` timestamp suffix, so `time.time()`
is either frozen/mocked or many launches collided within one second while
stale containers from a prior interrupted run still held the names.

This is not fixable through `config.yaml` (processors / tool_registry /
system_prompt) — the collision happens before any agent step at
`elapsed_s≈0.1–0.2`.

### Suggested fixes (require editing docker_env.py, outside meta scope)
1. Use a collision-proof suffix: `uuid.uuid4().hex[:12]` instead of
   `int(time.time())`.
2. Before `docker run`, reap any stale name:
   `docker rm -f <name>` (ignore errors) — or `docker run --rm` semantics
   with a unique name.
3. Ensure orphaned containers from interrupted runs are cleaned up between
   evolve rounds.

Until this lands, ~46% of the task set produces null trajectories and the
evolve loop cannot get signal on those tasks.
