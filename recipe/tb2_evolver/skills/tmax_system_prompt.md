# Tmax harness evolve surfaces

When `--eval-backend tmax` is active, each rollout runs the **full HarnessX
pipeline** inside the Tmax Docker container:

1. **System prompt** — `SystemPromptProcessor` → `SiblingSystemPromptBuilder`
   reads `system_prompt.txt` next to `config.yaml`.
2. **Processors** — every processor listed in `config.yaml` (hooks on
   `task_start` / `before_model` / `after_model` / `before_tool` / …).
3. **Tool set** — `tool_registry` in `config.yaml` (default: builtin `Bash`
   routed into the task container).

You may edit **any** of these three surfaces. Prefer general, reusable changes
(no hard-coded solutions for individual task ids).

## Deliverables each round

Always write / update in the evolve `output_dir`:

```text
config.yaml          # processors + tool_registry (+ any other HarnessConfig fields)
system_prompt.txt    # system prompt consumed by SiblingSystemPromptBuilder
```

If you replace `SiblingSystemPromptBuilder` with another builder (e.g. a
template builder), still keep `system_prompt.txt` coherent unless the new
builder no longer needs it.

## Good levers

- Add / tune processors (`LoopDetectionProcessor`, compaction, parse-retry,
  self-verify, custom control layers).
- Expand or restrict `tool_registry` (custom tools must be importable).
- Rewrite `system_prompt.txt` for strategy / verification / anti-loop guidance.

## Notes

- Working directory in the container is `/home/user`.
- Runtime-only slots (`sandbox_provider`, `tracer`) are injected by the Tmax
  runner — do not put them in `config.yaml`.
- The thin OpenAI+Bash loop is only used when `--harness-config` is omitted
  (debug). Evolve always passes a config, so processors are live.
