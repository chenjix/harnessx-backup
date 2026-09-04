# Assigned focus is an infrastructure failure, not a harness deficiency

## Task: `task_000015_89886d8d`

`result.json`:
```
"reward": 0,
"status": "error",
"elapsed_s": 0.1,
"error": "RuntimeError: docker run failed for task_000015_89886d8d: docker: Error response from daemon: Conflict. The container name \"/tmax-task00001589886d8d-1788298078\" is already in use by container \"...\". You have to remove (or rename) that container to be able to reuse that name."
```

## Diagnosis

The agent container **never started** (`elapsed_s: 0.1`, `status: error`). The
run loop never executed a single step, so the HarnessConfig (system prompt,
processors, tools) was never loaded into a running agent. The failure is a
**Docker container-name collision** raised inside the eval runner, before the
harness is engaged.

Root cause in `recipe/tmax_eval/docker_env.py::start_container`:
```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```
- The container name is `tmax-<task_id_first_20_chars>-<unix_seconds>`.
- Resolution is **1 second** (`int(time.time())`), and the task_id is truncated
  to 20 chars, so uniqueness relies almost entirely on the second-granularity
  timestamp.
- A stale container with the same name (from a prior aborted/parallel run)
  blocks `docker run`. `start_container` does **not** `docker rm -f` a
  pre-existing name before running.

## This is NOT fixable via HarnessConfig

- Failure occurs *before* the agent container starts — no processor,
  tool, or template can intercept it. A `MultiHookProcessor` runs inside the
  agent session, which never begins.
- Per `tb2-playbook`, `config.yaml` controls only the processor pipeline and
  system prompt. It has **zero levers** over Docker container naming, lifecycle,
  or the run loop's `start_container` call.
- `recipe/tmax_eval/docker_env.py` and `run_eval.py` are under `recipe/**`,
  which is **read-only** per Hard Invariant #2.

## Not a one-off — a systemic infra flake in this round

All 9 `status: error` tasks in this trajectory set share the **identical**
collision on the **same timestamp** `1788298078`, each with `elapsed_s: 0.1`:

- task_000015_89886d8d  (assigned focus)
- task_000028_7fe033ac
- task_000140_01c78b42
- task_000264_ab8c7253
- task_000396_e56917e2
- task_000505_50b5162d
- task_000748_c9807703
- task_001032_1adaccb9
- task_001090_c61c71f2

These false zeros also pollute R1's gating attribution: R1 predicted
task_000028 / task_000140 / task_000505 / task_001090 etc. as service/feasibility
tasks, but they errored at container-start and never ran — the R1
`HttpVerifierDepInstallProcessor` change cannot be fairly credited or blamed for
them.

## Requested human fix (eval runner, outside meta-agent scope)

In `recipe/tmax_eval/docker_env.py::start_container`, make the container name
collision-proof and/or self-healing. Suggested minimal patch:
1. Add a random suffix / use `uuid4().hex[:8]` instead of relying on
   second-resolution `int(time.time())`, and/or
2. Before `docker run`, best-effort `docker rm -f <name>` (idempotent) to
   reclaim a stale name, and/or
3. Retry `start_container` once with a fresh name on the `Conflict` daemon error.

Until this is fixed, these 9 tasks will keep registering as hard zeros
regardless of any HarnessConfig evolution, and will contaminate the co-evolution
gating signal.

## Decision this round

**Explicit no-op.** `config.yaml` (and its sibling `system_prompt.txt`) copied
byte-for-byte from `current_config` (R1). md5 verified identical. Canonicalize:
`{"ok": true}`. No config lever can address the assigned focus; the smallest
defensible edit is no edit.
