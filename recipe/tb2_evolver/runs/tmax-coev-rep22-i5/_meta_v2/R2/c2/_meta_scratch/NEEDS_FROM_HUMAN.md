# Needs from human — infrastructure fix outside evolvable surface

## Assigned focus: `task_000028_7fe033ac` failed — NOT a harness-capability gap

`result.json` for `task_000028_7fe033ac`:

```
"status": "error",
"reward": 0,
"elapsed_s": 0.1,
"error": "RuntimeError: docker run failed ... docker: Error response from daemon:
          Conflict. The container name \"/tmax-task0000287fe033ac-1788321907\"
          is already in use by container \"3443490e...\". You have to remove
          (or rename) that container to be able to reuse that name."
"traceback": ".../recipe/tmax_eval/docker_env.py, line 124, in start_container"
```

The container failed to **start**. `elapsed_s=0.1`, there is **no `.messages.json`
episode log** for this task — the agent run loop never began. This is not a model
capability gap and not a harness (in-loop) deficiency; it is an infrastructure
flake in the eval driver.

## Root cause

`recipe/tmax_eval/docker_env.py::start_container` (line 105) builds the container
name from:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

`int(time.time())` is **whole-second** resolution. When the orchestrator launches
multiple tasks within the same wall-clock second (parallel batch eval), the
`task_id[:20]` prefix collides for tasks sharing that prefix, and the second
`docker run --name <name>` is rejected by the daemon with a name Conflict.

## Same flake hit 5 tasks this round (all `status=error`, `elapsed_s=0.1`)

- task_000015_89886d8d
- task_000028_7fe033ac  (my assigned focus)
- task_000140_01c78b42
- task_001653_c4cafa73
- task_001781_529727cf

None of these has an episode/messages log — the agent never ran on any of them.

## Why NO `config.yaml` change can fix this

The `HarnessConfig` surface (processors, tool_registry, system prompt / templates)
runs **inside** the agent phase. This failure occurs **before** that phase — during
`docker_env.start_container`, in the recipe driver. `recipe/**` is read-only for the
meta-agent. There is no processor hook, tool, template, or knob that executes prior
to container start, so the evolvable surface cannot intercept, retry, or rename.

## Requested human fix (in read-only `recipe/tmax_eval/docker_env.py`)

Make the container name collision-proof, e.g.:

```python
import uuid
name = f"tmax-{task_id.replace('_', '')[:16]}-{uuid.uuid4().hex[:12]}"[:63]
```

(sub-second uniqueness). Alternatively add a pre-run `docker rm -f <name>` /
retry-with-new-name loop in `start_container`, or pass `--rm` + a UUID suffix.

## This round's action

Explicit no-op: `config.yaml` copied byte-for-byte from R1. The assigned focus is
unsupported as a harness-capability gap; the smallest defensible action is to not
touch the (healthy, mature) processor pipeline over an infra flake, and escalate the
real fix here.
