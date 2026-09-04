# NEEDS_FROM_HUMAN — R2 (loop3-rep1-i1)

Two findings this round are real but require edits **outside `output_dir/`**
(the harness/agent layer is read-only for the meta-agent), so they are
logged here rather than shipped.

## 1. Latent bug: `timeout_seconds` is dropped on the YAML-loaded config path

`benchmarks/terminal_bench_2/agent.py:189-192`

```python
if self._harness_config_yaml:
    base_config = HarnessConfig.from_yaml_file(self._harness_config_yaml)  # timeout_seconds NOT injected
else:
    base_config = make_tb2_harness_config(timeout_seconds=task_timeout)     # injected here only
```

The agent computes `task_timeout` (lines 172-182) but only passes it to the
**default** builder. When an evolved `config.yaml` is loaded (every evolve
round), the serialized `TaskTimeReminderProcessor` deserializes with
`timeout_seconds=None`, and `EnvironmentContextInjector` likewise gets no
timeout. Effect:

- `TaskTimeReminderProcessor.on_step_start` short-circuits on
  `if not self._timeout` (harness.py:246) → the 70%/90% time-budget nudges
  **never fire** for any evolved config.
- The agent is never told its wall-clock budget in the Environment header.

Suggested fix (human, in `agent.py`): after `from_yaml_file`, re-inject the
resolved timeout into the two processors, e.g. rebind
`TaskTimeReminderProcessor._timeout` / `EnvironmentContextInjector._timeout`
to `task_timeout` via `base_config.copy(...)` or a small post-load pass. This
cannot be done from `config.yaml` because the per-task timeout is a runtime
value not reachable from any processor event (`TaskStartEvent` /
`StepStartEvent.task` carry `max_steps` and `token_budget` but no timeout).

NOTE: this bug did **not** cause any of R2's 6 failures (see finding #2), so
it is not urgent for score — but it means a whole harness mechanism is dead
weight in every evolved run.

## 2. Dominant failure cluster is a stalled inference request (infra, not config)

4 of 6 failures (`cancel-async-tasks`, `custom-memory-heap-crash`,
`largest-eigenval`, `headless-terminal`) died with `exit_reason=interrupted`.
Measured from the episode JSONL: the **last productive tool call happened at
only 20-28% of the wall-clock budget**, then a single
`active_model_provider.complete()` request stalled for **651-1447s** and
consumed the remainder until Harbor's external wall-clock killed the task.

- `cancel-async-tasks`: last tool at 231s / 900s (26%), then 668s stall.
- `largest-eigenval`:   last tool at 249s / 900s (28%), then 651s stall.
- `custom-memory-heap-crash`: last tool at 352s / 1800s (20%), then 1447s stall.

`request_timeout_sec=600` is set (agent kwargs, provider `timeout=600`) but the
stall exceeds 600s — the local vLLM server at `127.0.0.1:8303` is hanging and
the provider read-timeout is not aborting. The run loop
(`harnessx/core/runloop.py:419`) does not wrap `complete()` in
`asyncio.wait_for`, and no processor hook can interrupt an in-flight request
(`on_before_model` fires before, `on_after_model` after — a hung request never
reaches the latter). **Not addressable from `config.yaml`.**

Suggested human fixes (all outside meta-agent scope):
- Wrap `complete()` in `asyncio.wait_for(..., request_timeout_sec)` in the run
  loop so a hung request aborts and the loop can continue/retry, OR
- Lower `request_timeout_sec` and/or add a provider-side connect+read timeout
  that actually fires against the vLLM endpoint, OR
- Investigate why the vLLM server hangs on these particular requests
  (the stalled steps followed large self-authored test scripts / long
  contexts — possible server-side OOM or scheduling stall).

The remaining 2 failures are model-capability gaps (see journal):
`pytorch-model-cli` (wrong prediction on an unseen verifier image) and
`query-optimize` (SQL runtime 0.75x golden — optimization depth).
