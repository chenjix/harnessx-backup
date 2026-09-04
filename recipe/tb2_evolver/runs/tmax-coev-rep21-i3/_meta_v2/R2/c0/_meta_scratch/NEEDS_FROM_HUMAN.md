# Blocker outside HarnessConfig scope — infrastructure, not harness

## Assigned focus is an infra failure, not a harness capability gap

Assigned task `task_000010_644ab1c2` did NOT fail inside the run loop.
Its `result.json` is:

```
"status": "error", "reward": 0, "elapsed_s": 0.9,
"error": "RuntimeError: docker run failed ... docker: Error response from daemon:
  Conflict. The container name \"/tmax-task000010644ab1c2-1788195286\" is already
  in use by container \"a55c...\". You have to remove (or rename) that container
  to be able to reuse that name."
"traceback": ".../recipe/tmax_eval/docker_env.py, line 124, in start_container"
```

The agent never started — the container failed to launch. No `messages.json`
exists for this task. No processor, tool, template, or system-prompt change
can affect a `docker run` name collision that occurs *before* the run loop
boots.

## This is a systemic infra defect this round, not a one-off

Across the round's trajectory set, **21 of 50 tasks are `status:error` with the
byte-identical `name_conflict` shape**, all with `elapsed_s <= 1.1s` (never
entered the run loop). These 21 "failures" are pure infrastructure noise:

- The remaining tasks are 21 `ok/reward=0`, 7 `ok/reward=1`, 1 `agent_error`.
- So the *real* harness-addressable population this round is ~29 tasks; the
  21 infra errors are not attributable to the harness at all.

Note: R1's assigned focus `task_000069_41f1682c` is ALSO one of these 21 infra
errors this round — meaning R1's attribution ("pending") is confounded and its
predicted_affected tasks cannot be scored until the container churn is fixed.

## Requested fix (outside my writable scope)

`recipe/tmax_eval/docker_env.py::start_container` reuses a fixed container name
(`tmax-<taskid>-<...>`) and does not remove a pre-existing container of the same
name before `docker run`. Fix options for the human/runner owner:

1. `docker rm -f <name>` (or `docker run --rm` + pre-flight removal) before
   starting, OR
2. append a unique suffix (PID / random / monotonic counter) to the container
   name per attempt, OR
3. run with `--name` omitted and track by container ID.

Until this is fixed, ~40% of the eval population is unscorable and no
HarnessConfig evolution can move the needle on those tasks.

## Decision this round

Explicit **no-op**: `config.yaml` is a byte-for-byte copy of `current_config`
(`R1/config.yaml`). Canonicalize passes (`{"ok": true, "checked_templates": 0}`).
Making up a processor/template to "address" a Docker name collision would be
drift onto an imaginary capability gap and would only add cost.
