# NEEDS FROM HUMAN — benchmark-infrastructure bug (not HarnessConfig-fixable)

## Summary

My assigned focus task **`task_000015_89886d8d`** did not fail for a model or
harness-pipeline reason in this trajectory set. It failed at **container launch**,
before any HarnessConfig processor / prompt / tool ever ran:

```
status: error
elapsed_s: 0.1
RuntimeError: docker run failed for task_000015_89886d8d:
  docker: Error response from daemon: Conflict. The container name
  "/tmax-task00001589886d8d-1788232928" is already in use by container
  "e2551f4cdd8a1193fbc813ef4ca71b4eb13e35a94ef011dcb7acf90e502fc8f2".
  You have to remove (or rename) that container to be able to reuse that name.
```

This is **not** unique to my task. **13 of 50 tasks (26%)** in
`tmax-coev-rep22-i2-r0-fe-c4-traj` died with the identical error and
`elapsed_s ≈ 0.1`:

```
task_000010_644ab1c2  task_000015_89886d8d  task_000028_7fe033ac
task_000140_01c78b42  task_000264_ab8c7253  task_000396_e56917e2
task_000505_50b5162d  task_000748_c9807703  task_000933_1f27096a
task_001032_1adaccb9  task_001090_c61c71f2  task_001781_529727cf
task_001937_ac874115
```

All score `reward=0`, dragging the measured pass count down purely from an
orchestration collision, not from agent behavior.

## Root cause (in READ-ONLY code — outside my writable scope)

`recipe/tmax_eval/docker_env.py::start_container` (line ~105):

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

Two structural problems:

1. **Second-granularity timestamp + `[:20]` truncation.** With `-c4` concurrency
   and fast image cache hits, multiple tasks launch within the same wall-clock
   second. The disambiguator is only `int(time.time())` (whole seconds), so it
   does not disambiguate within a second. The `[:20]` truncation of the task id
   further raises collision odds across similar-prefixed ids.
2. **No pre-run cleanup of a stale container of the same name.** `docker run
   --name <name>` fails hard if a container by that name already exists (e.g. a
   leftover from a crashed/killed prior run or repeat). `start_container` does
   not `docker rm -f <name>` first, and there is no retry.

`run_one` in `recipe/tmax_eval/run_eval.py` calls `start_container` at line ~149,
**before** `harness_runner.run_harness_agent` at line ~175. The HarnessConfig
(processors, tool_registry, system prompt) is only loaded inside
`run_harness_agent`. The `RuntimeError` is raised before any of that, so:

**No `MultiHookProcessor`, tool, or template can intercept, retry, or recover
from this failure.** It is categorically outside the evolvable
`HarnessConfig` surface (confirmed by tb2-playbook: "config controls the
processor pipeline and system prompt, not the benchmark infrastructure").

## Suggested fix (human must apply — read-only for the meta-agent)

In `docker_env.py::start_container`, before `docker run`:
- Make the name unique per launch: replace `int(time.time())` with e.g.
  `f"{int(time.time()*1000)}-{os.getpid()}-{uuid4().hex[:8]}"`, and/or
- Pre-clean: `docker rm -f <name>` (ignore rc) before `docker run`, and/or
- On the specific `Conflict ... already in use` stderr, `docker rm -f` the
  named container and retry `docker run` once.

Any one of these removes the 13/50 pre-flight failures. This should be handled
at the eval-orchestration layer, not the harness.

## Impact on this batch

Sibling proposals in R2 were also assigned focus tasks that are in this
docker-error set (`c0 → task_000010_644ab1c2`, `c2 → task_000028_7fe033ac`,
and mine `c1 → task_000015_89886d8d` are all pre-flight docker-conflict deaths).
None of these are HarnessConfig-fixable. If the batch's measured deltas look
noisy or negative on these tasks, the cause is this collision, not the configs.
