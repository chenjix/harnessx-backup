# NEEDS_FROM_HUMAN — infrastructure failure outside HarnessConfig surface

## Summary

My assigned focus, `task_000069_41f1682c`, did **not** fail due to any
harness capability gap. It failed with a **Docker container-name
collision** *before the agent ever started*:

```
status: "error", reward: 0, elapsed_s: 0.5
RuntimeError: docker run failed for task_000069_41f1682c:
docker: Error response from daemon: Conflict. The container name
"/tmax-task00006941f1682c-1788195287" is already in use by container
"30030a6f775b6e1...". You have to remove (or rename) that container
to be able to reuse that name.
```

- No `task_000069_41f1682c.messages.json` exists (no agent phase ran).
- Traceback originates in `recipe/tmax_eval/docker_env.py:124`
  (`start_container`), called from `recipe/tmax_eval/run_eval.py:149`.

## Scope of the problem (not just my task)

This is a **cluster of 21/50 tasks** in this trajectory set, all with
`status="error"`, `elapsed_s < 1.2s`, identical container-name-conflict
`RuntimeError`. Result-status distribution for the round:

| status        | reward | count |
|---------------|:------:|------:|
| error (docker)|   0    |   21  |
| ok            |   0    |   21  |
| ok            |   1    |    7  |
| agent_error   |   0    |    1  |

The 21 docker-error tasks are counted as reward=0 failures but the agent
never executed — they are **infra flakes**, not model/harness failures.
They depress the measured pass-rate and pollute the failure signal that
downstream evolve rounds diagnose against (see e.g. R1's journal, whose
diagnosis of task_000069 was drawn from a *different* prior run's
trajectory where the agent actually ran).

## Why the meta-agent cannot fix this

The failure is in the eval **runner**, not the `HarnessConfig`:
- It occurs in `recipe/tmax_eval/docker_env.py` / `run_eval.py`, which are
  **read-only** for the meta-agent (hard invariant #2).
- It happens **before** any processor, system prompt, or tool in the
  pipeline is instantiated. No config-surface lever (processors,
  tool_registry, system prompt) can influence Docker container startup.

## Suggested human-side fix (in the read-only runner, not shippable by me)

In `docker_env.py:start_container`, make container creation
collision-safe before `docker run`, e.g. one of:
1. `docker rm -f <name>` (best-effort) immediately before `docker run`,
   or use `--replace` semantics, or
2. append a per-attempt unique suffix (uuid4) to the container name so a
   leftover container from a crashed/parallel run cannot collide, or
3. retry `start_container` once after `docker rm -f` on `Conflict`.

Until fixed, tasks landing on a stale container name will keep erroring
at ~0.5s regardless of any harness evolution.

## This round's decision

Explicit **no-op**: `config.yaml` copied byte-for-byte from
`current_config` (md5 `209c681e37801ed32d453b9be51ebb04`, verified
`cmp`-identical; `canonicalize` → `{"ok": true}`). Making any
config edit here would be (a) ineffective against a pre-agent Docker
error and (b) drift onto other batch proposals' focuses (the 21 genuine
`ok`/reward=0 agent failures), risking regressions on the 7 passing tasks
for zero expected gain on my assigned task.
