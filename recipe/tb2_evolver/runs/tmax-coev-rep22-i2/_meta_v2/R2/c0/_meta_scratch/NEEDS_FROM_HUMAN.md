# Needs from human — infra flake outside HarnessConfig write-scope

## Summary

My assigned focus task, **`task_000010_644ab1c2`**, did not fail for any
reason a `HarnessConfig` (processors / tools / system prompt) can address.
It failed at **container startup**, before the run loop ever booted:

```
RuntimeError: docker run failed for task_000010_644ab1c2:
docker: Error response from daemon: Conflict. The container name
"/tmax-task000010644ab1c2-1788232928" is already in use by container
"d45671ef6c9f...".
```

`result.json`: `status=error`, `elapsed_s=0.1`, `reward=0`. No
`messages.json` was produced — the agent never received a single turn.

## This is not local to one task

Across this round's 50-task trajectory set, **13 tasks (26%) died with the
identical `docker run failed ... container name already in use` error**:

```
task_000010_644ab1c2  task_000015_89886d8d  task_000028_7fe033ac
task_000140_01c78b42  task_000264_ab8c7253  task_000396_e56917e2
task_000505_50b5162d  task_000748_c9807703  task_000933_1f27096a
task_001032_1adaccb9  task_001090_c61c71f2  task_001781_529727cf
task_001937_ac874115
```

Real breakdown of the 50-task set: **20 passed / 17 genuinely ran and
scored reward 0 / 13 pure infra-error (never ran)**. The reported
`pass_rate=0.40` is depressed by these 13 phantom failures. Note that
`task_000015_89886d8d` and `task_000505_50b5162d` — both cited as OCR
targets in R2's `h_ocr_low_dpi_guard_v1` — are among the infra-error 13
this round, so that hypothesis's attribution cannot be judged from this
trajectory set.

## Root cause (read-only recipe code)

`recipe/tmax_eval/docker_env.py::start_container` builds the container
name as:

```python
name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
```

The name is `tmax-` + first 20 chars of the underscore-stripped task_id +
a **1-second-resolution** unix timestamp. Two collision modes:

1. **Truncation collision** — only the first 20 chars of the task_id are
   kept, so any two task_ids sharing that 20-char prefix collide if they
   launch in the same wall-clock second.
2. **Leaked-container collision** — a container from a prior run (same
   task, same second bucket after a fast retry/restart) was never
   `docker rm`'d, so the name is still taken. `run_eval.py` only calls
   `stop_container` in its own teardown path; a crashed/killed prior
   process leaves the container resident and its name reserved.

`start_container` (line 149 of `run_eval.py`) runs **before** the
`harness_config` is ever loaded (line 171). The MetaAgent's entire write
surface — processors, tool_registry, system prompt — is downstream of the
crash. **No config change can prevent or recover from this.**

## Suggested fixes (all in read-only recipe code — human action required)

Any one of these would eliminate the cluster:

1. **Unique names**: replace `int(time.time())` with
   `uuid.uuid4().hex[:12]` (or append PID + a monotonic counter), and use
   the full task_id hash instead of a 20-char prefix truncation.
2. **Pre-run cleanup**: before `docker run`, do
   `docker rm -f <name>` (ignore-missing), or add `--rm`-style reaping of
   stale `tmax-*` containers at eval start.
3. **Retry-on-conflict**: in `start_container`, if stderr contains
   `is already in use`, `docker rm -f` the named container and retry once.

## Action taken this round

Because there is no HarnessConfig lever for a pre-run-loop docker name
collision, this round ships an **explicit no-op**: `config.yaml` is a
byte-for-byte copy of `current_config` (R1). Canonicalizes clean
(`{"ok": true, "checked_templates": 0}`). See `candidates.md` for the
full "focus unsupported" argument and the journal for the round entry.
