# c3 Diagnosis — assigned focus task_000028_7fe033ac

## Decision: EXPLICIT NO-OP (config copied byte-for-byte from R1)

## Why the assigned focus yields no harness fix

`task_000028_7fe033ac` `result.json`:
- `status = "error"`, `reward = 0`, `elapsed_s = 0.2`
- `error = "RuntimeError: docker run failed ... Conflict. The container
  name \"/tmax-task0000287fe033ac-1788094866\" is already in use ..."`
- Traceback originates in `recipe/tmax_eval/docker_env.py:124`
  (`start_container`) → `run_eval.py:149`.

The task **never started**: the failure is at container-launch, at 0.2s,
before any agent step, before any LLM call, before any processor in the
pipeline ever runs. No system-prompt, processor, or tool change can prevent
a Docker container-name collision — that logic lives in `docker_env.py`,
which is **read-only** for the meta-agent (only processors / tool_registry /
system_prompt are evolvable per the tb2 playbook).

## Scope of the flake (not just my task)

23 of 50 tasks in this trajectory set share the identical failure signature:
`status=error`, `elapsed_s≈0.1–0.2`, `docker: ... container name
"/tmax-task<id>-1788094866" is already in use`. All 23 share the **same
timestamp suffix `-1788094866`** — `docker_env.py:105` names containers
`f"tmax-{task_id...}-{int(time.time())}"`. A frozen/mocked clock (or a
prior interrupted run leaving orphaned containers with the same names)
causes the collision. This is a harness-runner / orchestration infra bug,
not a model or in-loop harness deficiency.

Fixing it would require editing `docker_env.py` (e.g. name with a uuid4
suffix, or `docker rm -f` the stale name before `docker run`). That file
is outside my write scope. Logged for the human below.

## Why NOT drift onto other proposals' territory

The genuinely-executed reward=0 tasks (e.g. task_000740 659s, task_001498
600s, task_000933 572s — likely budget/loop/output-stall clusters) are the
territory of the R1 `control` proposals (cyclic-loop hard-stop / output-stall
recovery, both `control` lever, one accepted / one pending). The brief tells
me to stay on my assigned focus and make the smallest defensible edit when
that focus is unsupported. My focus is unsupported for a *harness* change,
so I ship the no-op rather than duplicating another cell's control-lever bet.

## Needs from human

The 23/50 `docker name conflict` errors are an infrastructure issue in
`recipe/tmax_eval/docker_env.py`. Recommend: unique container names
(uuid4 suffix instead of `int(time.time())`) and/or a pre-run
`docker rm -f <name>` reap of stale containers. This is required for the
benchmark to produce meaningful trajectories on those 23 tasks. See
NEEDS_FROM_HUMAN.md.
