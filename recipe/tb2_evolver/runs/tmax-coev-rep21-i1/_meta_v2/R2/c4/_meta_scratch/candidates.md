# Candidates — R2 c4

## Assigned focus: `task_000140_01c78b42`

**Verdict: no harness fix possible — explicit no-op.**

### Diagnosis

`task_000140_01c78b42.result.json` in the trajectory set shows:

```
"status": "error",
"elapsed_s": 0.7,
"agent": null,
"final_pytest": null,
"error": "RuntimeError: docker run failed for task_000140_01c78b42:
  docker: Error response from daemon: Conflict. The container name
  \"/tmax-task00014001c78b42-1788159878\" is already in use by
  container \"afdc0f1e...\". You have to remove (or rename) that
  container to be able to reuse that name."
```

There is **no `messages.json`** for this task — the agent phase never
executed. The container failed to *start* (`elapsed_s: 0.7`) because a
stale container with the same name was left behind by a prior run and
not removed before re-run.

This is an **orchestration / infrastructure defect** in
`recipe/tmax_eval/docker_env.py::start_container` (read-only), which
runs *before* any `HarnessConfig` processor, tool, or system prompt is
loaded. The evolvable surface (processor pipeline, tool registry,
system prompt) has no hook that fires before container creation and no
mechanism to clean up a leftover Docker container name.

### Not a one-off

Across the 50-task set, **19 tasks fail with the identical
container-name-conflict error** (all `status: error`, `elapsed_s ≈ 0.7`,
`agent: null`): task_000028, 000140, 000264, 000329, 000338, 000505,
000536, 000748, 000760, 000863, 000912, 000956, 001088, 001264, 001653,
001697, 001701, 001706, 001761. This confirms a systemic infra issue
(stale-container cleanup in the runner), not a task-specific agent gap.

### Why no config edit

Any processor/prompt change would (a) fail to touch the actual failure
(which precedes harness init) and (b) risk regressing the 30 tasks that
currently run (`status: ok`). Fabricating a "fix" for a pre-harness
docker error would be a local pretend-win with pure downside. The
disciplined move is a byte-for-byte no-op copy of `current_config` and
a `NEEDS_FROM_HUMAN` note flagging the runner bug.

No `## Candidate C-NNN` is proposed because no config change is shipped.
