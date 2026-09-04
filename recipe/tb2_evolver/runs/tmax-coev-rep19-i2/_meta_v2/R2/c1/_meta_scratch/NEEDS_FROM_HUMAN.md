# Out-of-scope infrastructure fix needed

## Assigned focus is a runner-infra failure, not a harness deficiency

`task_000028_7fe033ac` (and 9 others this round) failed with
`status: error`, `elapsed_s: 0.1`, reward 0 — **before the agent loop
ever started**. No system prompt, processor, or tool executed.

### Root cause (identical across all 10 errored tasks)

```
RuntimeError: docker run failed for <task>: docker: Error response from
daemon: Conflict. The container name "/tmax-task...-1788106887" is already
in use by container "84aa...". You have to remove (or rename) that
container to be able to reuse that name.
```

All 10 collisions carry the SAME timestamp (`1788106887`, one at
`...888`). In `recipe/tmax_eval/docker_env.py::start_container`:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

The container name is keyed on `int(time.time())` (1-second resolution)
plus a truncated task_id. When a batch (or a retry) launches multiple
trials of the same task within the same wall-clock second, the generated
names collide and `docker run --name` fails hard.

### Errored tasks this round (all same cause)
- task_000010_644ab1c2
- task_000028_7fe033ac  (this proposal's assigned focus)
- task_000118_3043e92d
- task_000140_01c78b42
- task_000505_50b5162d
- task_000748_c9807703
- task_000933_1f27096a
- task_001032_1adaccb9
- task_001781_529727cf
- task_001937_ac874115  (was an R1 predicted_affected task — its R1
  attribution is now muddied by an infra error, NOT a harness regression)

### Why the meta-agent cannot fix this
`recipe/tmax_eval/docker_env.py` and `recipe/tmax_eval/run_eval.py` are
**read-only** (SOUL invariant #2). The failure occurs in the runner
before the HarnessConfig-controlled agent phase, so no processor / tool /
template / config knob can intercept it. The TB2 playbook confirms
`config.yaml` "controls the processor pipeline and system prompt, not the
benchmark infrastructure."

### Suggested runner fix (for the human, outside this scope)
In `docker_env.py::start_container`, make the container name
collision-proof, e.g.:
- append a random suffix / `uuid4().hex[:8]` (or `os.getpid()` +
  monotonic counter) instead of relying on `int(time.time())`, and/or
- before `docker run`, `docker rm -f <name>` any pre-existing container
  with that name (idempotent cleanup), and/or
- retry once with a fresh name on a "name is already in use" conflict.

Any of these makes the 10 errored tasks re-runnable. Until then these are
phantom failures that will keep dragging the measured score and polluting
lever attribution regardless of what the harness config does.

## Meta-agent action this round
No harness lever applies. Shipping an explicit **no-op** (R1 config copied
byte-for-byte) rather than drifting onto another proposal's territory or
fabricating a change with no supporting trajectory signal.
