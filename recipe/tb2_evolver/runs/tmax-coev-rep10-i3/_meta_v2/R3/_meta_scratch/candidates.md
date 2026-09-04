# R3 Candidates

## Candidate C-001 — VerifierDepPrimerProcessor (SHIPPED)

**Three-axis tag:** lens=environment-fidelity / lever=Processor (new) / intent=unblock-false-zero-cluster

### Hypothesis
A cluster of HTTP/service tasks scores 0 not because the agent's solution is
wrong, but because the *post-agent verifier's* pytest module does
`import requests`, and `requests` is not installed in the task container.
pytest returns rc=2 (`ModuleNotFoundError: No module named 'requests'`) at
collection time → automatic reward 0, regardless of correctness.

### Evidence
- rc=2 `ModuleNotFoundError: No module named 'requests'` observed in R2
  trajectories for tasks: 000020, 000028, 000661, 000689, 000958, 002160
  (+ 000010 circular-import variant). All are HTTP/service tasks.
- In every case the agent built the server in C/Go/Node or Python stdlib and
  probed with `curl` — never had a reason to install the Python client, so
  the missing dep is purely a verifier-side environment gap.
- pip is reachable in these containers (flask/certifi/charset_normalizer
  installed successfully in *passing* tasks) → `pip install requests` will
  succeed.

### Mechanism (schema)
New `MultiHookProcessor` `VerifierDepPrimerProcessor`:
- `on_task_start`: reset `_primed=False`.
- `on_after_model`: on the **first** model turn only, append a single real
  Bash `ToolCall` (`python3 -m pip install --quiet ... requests || true`) to
  `event.tool_calls`. Idempotent, quiet, non-fatal (`|| true`), touches no
  task output path. Persists in the container for the verifier phase.
- `on_task_end`: reset.

Runloop-verified: `on_after_model` output replaces `model_event` (runloop
L438), is recorded as the assistant message (L479), and each tool_call —
including the appended primer — is executed (L497+) with id-matched results.
Provider-safe: N tool_calls → N tool_results, ids matched.

### Retroactive check
- **Direct-replay variant:** the six rc=2 tasks would have had `requests`
  importable at verify time → collection succeeds → reward reflects actual
  solution correctness instead of a forced 0.
- **Would-not-help variant:** tasks failing for genuine logic reasons
  (false-completion clusters in data_processing / scientific_computing) are
  untouched — this only removes the artificial dep barrier.

### Tasks affected (intent=unblock cluster)
predicted_affected: 000020, 000028, 000661, 000689, 000958, 002160 (000010 possible).

### Pareto assessment
- **Global gain:** up to 6 forced-zeros can flip to their true reward.
- **Regression risk:** LOW. The install is idempotent + non-fatal, appended
  (never replaces) the model's first action, runs once per task. Worst case
  on the 12 passing tasks is a ~<1s "already satisfied" no-op tool result.
- **Cost shift:** +1 tool call/task, trivial tokens; negligible.
- **Rollback trigger:** if global pass-rate drops vs R0 (15/50) or any smoke
  task errors on the injected turn, revert to R0.

### Validation
canonicalize ✓ · dry_fire ✓ (0 bugs) · contract ✓ (0 violations) ·
literals ✓ (0 findings). system_prompt.txt copied byte-for-byte for
SiblingSystemPromptBuilder resolution.
