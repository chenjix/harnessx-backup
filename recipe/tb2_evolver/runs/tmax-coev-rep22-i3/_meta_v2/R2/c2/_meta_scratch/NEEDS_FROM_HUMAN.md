# Needs from human — infrastructure bug outside evolvable surface

## Assigned focus `task_000028_7fe033ac` is an orchestration/infra failure, not a harness gap

`task_000028_7fe033ac.result.json`:
- `status: "error"`, `reward: 0`, `elapsed_s: 0.1`
- error: `RuntimeError: docker run failed ...: docker: Error response from
  daemon: Conflict. The container name "/tmax-task0000287fe033ac-1788258698"
  is already in use by container "eae4ce18...".`
- traceback root: `recipe/tmax_eval/docker_env.py:124` in `start_container`

The agent **never ran** — the failure happens at container startup, 0.1s in,
before the run loop boots. No processor, system prompt, tool, or template can
intercept a `docker run` conflict that occurs before the first agent turn.

### Root cause (in read-only runner code)

`recipe/tmax_eval/docker_env.py:105`:
```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```
Container name uses **second-resolution** `int(time.time())`. When the same
task is retried, or two trials collide within the same wall-clock second, the
generated name duplicates an existing container → `docker run` refuses with a
name conflict → `RuntimeError` before the agent starts.

### Scope of the bug this round

All 8 `status: "error"` tasks in this trajectory set share the *identical*
container-name-conflict signature (elapsed_s ≈ 0.1 each):
`task_000015_89886d8d, task_000028_7fe033ac, task_000140_01c78b42,
task_000505_50b5162d, task_000748_c9807703, task_001032_1adaccb9,
task_001090_c61c71f2, task_001781_529727cf`.

### Why no config-level fix

Per `tb2-playbook` + hard invariant #2:
- Container naming/lifecycle lives in `recipe/tmax_eval/docker_env.py` and
  `harness_runner`, which are **read-only** and injected at runtime — NOT in
  the `config.yaml` evolvable surface (processors / tool_registry / system
  prompt only).
- The failure precedes the run loop, so no `MultiHookProcessor` hook ever
  fires. This is unaddressable from the meta-agent's write scope.

### Suggested human fix (in `docker_env.py`, out of my scope)

Any of:
1. Use a collision-proof suffix instead of second-resolution time, e.g.
   `uuid.uuid4().hex[:12]` or `time.time_ns()`.
2. Add `--rm`-safe pre-cleanup: `docker rm -f <name>` (ignore-missing) before
   `docker run`, or retry `start_container` with a fresh name on the
   "already in use" `stderr`.
3. Pass `--replace`-style idempotency by removing any stale container with the
   deterministic name first.

Shipping explicit **no-op** config this round (byte-for-byte copy of R1) —
drifting onto another proposal's focus would violate the batch-diversity rule,
and there is no defensible smaller edit within scope.
