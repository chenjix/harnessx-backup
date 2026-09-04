# NEEDS_FROM_HUMAN — R2/c2

## Assigned focus: `task_000028_7fe033ac` — NOT a harness deficiency

The assigned failing task did **not** fail due to any model or harness
capability gap. `result.json` shows:

```
status: "error"
elapsed_s: 0.8
agent: null
final_pytest: null
error: RuntimeError: docker run failed for task_000028_7fe033ac:
  docker: Error response from daemon: Conflict. The container name
  "/tmax-task0000287fe033ac-1788159877" is already in use by container
  "d047407...". You have to remove (or rename) that container to be able
  to reuse that name.
```

The agent phase **never started** — the container failed to launch at 0.8s.
No processor, tool, or system-prompt change in `config.yaml` can affect a
run that dies before the agent loop boots. The `HarnessConfig` surface only
governs the in-agent phase.

## This is systemic, not a one-off

**19 of 50 tasks (38%) in this round errored with the identical Docker
container-name conflict:**

```
task_000028_7fe033ac  task_000912_770802f8  task_001697_af4b85fb
task_001706_24462a09  task_001653_c4cafa73  task_000329_a3ac56b0
task_000338_27d6a1be  task_000863_7acceb19  task_001761_f620d44d
task_001701_95e3bbcb  task_000760_76ba653c  task_001088_6f566806
task_000140_01c78b42  task_000505_50b5162d  task_001264_9f4ca84a
task_000536_9c16e8ef  task_000956_7e92337f  task_000264_ab8c7253
task_000748_c9807703
```

Note: `task_000140_01c78b42` (R1's predicted-affected task) is among them —
its R1 attribution is therefore unreliable; it never actually ran the agent
this round.

## Root cause (read-only orchestration code)

`recipe/tmax_eval/docker_env.py::start_container` builds the container name as:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

The name collides when a prior evaluation attempt for the same task-id left a
container behind (not `docker rm`'d after an earlier crash/interrupt), or when
two attempts land in the same integer second. `docker run --name` then fails
with the observed `Conflict` error and the whole task is scored `reward=0`.

This lives in `recipe/tmax_eval/docker_env.py` and `recipe/tmax_eval/run_eval.py`,
which are **read-only** (SOUL invariant #2: `recipe/**` is not editable by the
meta-agent). It is outside the evolvable `config.yaml` surface entirely.

## Suggested human-side fixes (out of my scope)

1. **Pre-run cleanup**: in `start_container`, `docker rm -f <name>` (ignore
   errors) before `docker run`, or add `--rm` semantics + a retry with a
   fresh suffix on `Conflict`.
2. **Stronger uniqueness**: append a random token (`uuid4().hex[:8]`) instead
   of / in addition to `int(time.time())` so same-second retries never collide.
3. **Between-run sweep**: `docker ps -aq --filter name=tmax-` and remove
   leftovers before the batch starts.
4. **Retry-on-error**: treat `status=error` with a docker Conflict as a
   transient infra failure and re-attempt the task, rather than scoring it 0.

Until one of these lands, ~38% of this benchmark's "failures" are infra
noise, not solvable by any config evolution — and they corrupt the
attribution signal for every accepted/reverted hypothesis.

## Decision this round

**Explicit no-op.** `config.yaml` + `system_prompt.txt` copied byte-for-byte
from R1 (`config.yaml` md5 `f3266f41...`, `system_prompt.txt` md5 `914fb4a8...`).
Making up a config change to "address" an infra flake would be
evidence-free churn and risk regressing the 30 tasks that did run.
