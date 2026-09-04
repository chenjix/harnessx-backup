# NEEDS_FROM_HUMAN — R2/c0

## Assigned focus is an infrastructure race, not a harness-config gap

**Task `task_000010_644ab1c2` failed with `status: error`, `reward: 0`,
`elapsed_s: 0.6`** — the agent session **never started**. The failure is a
Docker container-name collision at `docker run`:

```
RuntimeError: docker run failed for task_000010_644ab1c2: docker: Error
response from daemon: Conflict. The container name
"/tmax-task000010644ab1c2-1788179434" is already in use by container
"cf50b6c272b6..." You have to remove (or rename) that container ...
```

### Root cause (read-only code, outside my write scope)

`recipe/tmax_eval/docker_env.py::start_container` (line 105):

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

The container name is `tmax-<task_id-no-underscores, truncated to 20 chars>-<unix-second>`.
Two failure vectors:
1. **Second-granularity timestamp collision** — two runs of the same task
   (retries, parallel workers) starting within the same integer second get
   identical names.
2. **20-char prefix truncation** — distinct task IDs can collide after the
   `[:20]` truncation of the underscore-stripped id.

A stale/undeleted container from a prior run holding the name also triggers it.

### Why NO HarnessConfig intervention can fix this

Processors, tools, and system-prompt templates all run **inside** the agent
session, which is created by `start_container`. The collision happens
**before** the harness is even instantiated, so nothing on the evolvable
surface (`config.yaml` processor pipeline / tool_registry / system_prompt.txt)
can intercept it. The tb2-playbook confirms: "The harness config controls the
processor pipeline and system prompt, **not the benchmark infrastructure**."

### Scope of the problem

**9 of 50 tasks in this round (18%) failed identically** with
`status: error` + docker-name Conflict, all with `elapsed_s < 1s` (never ran):

- task_000010_644ab1c2, task_000140_01c78b42, task_000264_ab8c7253,
  task_000505_50b5162d, task_000748_c9807703, task_000933_1f27096a,
  task_000958_4bb2b05d, task_001321_658ce4a8, task_001937_ac874115

These are pure infra flakes — the true agent pass-rate is being under-counted
by up to 9 tasks. This dwarfs any single-task harness tweak.

### Suggested fix (requires editing read-only recipe code — human action)

In `recipe/tmax_eval/docker_env.py::start_container`, make the name collision-proof
and/or pre-clean, e.g.:

```python
import uuid
name = f"tmax-{task_id.replace('_','')[:16]}-{uuid.uuid4().hex[:12]}"[:63]
# and/or, before docker run:
_run(["docker", "rm", "-f", name], timeout=30)  # best-effort clear stale name
```

Using a `uuid4` suffix (instead of `int(time.time())`) removes both the
second-granularity race and — with a shorter task-id prefix — the truncation
collision. A best-effort `docker rm -f` on the target name before `run` clears
stale containers from crashed prior runs.

## This round's decision: explicit no-op

Because the assigned failure is unaddressable via the HarnessConfig surface,
R2/c0 ships the R1 config **byte-for-byte** (config.yaml + system_prompt.txt),
per the "smallest defensible edit" rule. No processor/tool/template authored.
