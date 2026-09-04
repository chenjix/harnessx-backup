# Candidates — R1 (tmax-coev-rep12-i3)

Baseline: 11/50 pass (22%). Model: qwen35-9b-tmax-coev-r12, true context
window = 65536 tokens; harness_runner reserves max_tokens=4096 output →
effective input ceiling ≈ 61440 tokens.

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Lower `CompactionProcessor.token_threshold` from 140000 to 40000 so compaction
actually fires before the model's 65536-token context window overflows.

- Tasks affected: task_001044_45c70cf1, task_000444_250abeda
  (both `status=agent_error`, `exit_reason=error`). Also protects any
  long-running task from the same overflow (regression-prevention for the
  budget_exceeded long-context cluster: task_001028_5bc8bc70 peaked ~31.8k
  rough tokens and was climbing).
- Signal: `result.json.agent.exit_reason == "error"` on both crash tasks; the
  episode trace `task_end` event carries
  `error: "BadRequestError: ... maximum context length is 65536 tokens.
  However, you requested 4096 output tokens and your prompt contains at least
  61441 input tokens ... total of at least 65537 tokens"`. The pipeline's
  `CompactionProcessor.token_threshold` was 140000 — unreachable on a 64k
  model, so compaction never triggered.
- Verified (Read):
  - task_001044_45c70cf1 trace `task_end` step 43:
    `exit_reason=error`, error = 400 BadRequestError, "maximum context length
    is 65536 tokens ... prompt contains at least 61441 input tokens". The
    step_start `token_count` (rough cl100k) series climbed
    40435 → 43408 → 46381 → 49354 and the next request (~52k rough) hit
    61441 real input → hard 400. Overhead (real − rough) ≈ 9-10k = system
    prompt + Bash tool schema + Qwen-vs-cl100k tokenizer skew.
  - task_000444_250abeda trace `task_end`: same
    `maximum context length is 65536 tokens` 400 error; peak rough token_count
    20696 before crash — the messages themselves were smaller but the request
    still overflowed once overhead + a large tool result was added; degenerate
    verbatim-repeat loop inflated history (assistant repeated the identical
    "Let me try a different approach…" paragraph across steps).
- Why Configuration not Control/Instruction: the correct mechanism
  (`CompactionProcessor`) is already wired into the pipeline and reads
  `rough_token_count`; it simply never fires because its threshold is
  mis-calibrated for a 128k+ model. The fix is a single knob value, not a new
  hook or prompt rule. A new processor would duplicate machinery that already
  exists; a prompt rule cannot prevent an API-level 400.
- Retroactive check (A-corrective): yes — if compaction had fired at 40000
  rough tokens (~50k real, well under the 61440 ceiling), the request would
  never have reached 65537 total tokens, so neither task would have crashed
  with `exit_reason=error`. They might still fail on correctness (both are hard
  scientific_computing reverse-engineering tasks), but converting a hard harness
  crash into a normal run gives the agent its remaining steps and removes a
  0-reward guaranteed by infrastructure, not capability.
- expected_global_gain: eliminates the 2 `agent_error` crashes (structural
  0-rewards) and prevents the same overflow on the long-context cluster
  (task_001028 was the closest live risk). Fixing a harness-caused hard failure
  is strictly Pareto-positive.
- regression_risk: near-zero. The 11 passing tasks all peaked far below 40000
  rough tokens (largest passing peak ~13k), so they never trigger compaction —
  behaviour is byte-identical for them. Only tasks whose history exceeds ~40k
  rough tokens (already failing / at risk) get compacted. Compaction summarises
  older turns via the existing `summarize` sub-harness and keeps the last 6
  messages intact, so recent working context is preserved.
- cost_shift: slightly lower on long tasks (compaction shrinks the re-sent
  prompt each step, reducing per-call input tokens after the first compaction);
  neutral on short tasks (never fires). Net expected cost movement: flat to
  slightly down.
