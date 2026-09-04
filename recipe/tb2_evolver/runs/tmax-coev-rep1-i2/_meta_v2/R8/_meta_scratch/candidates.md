# R8 Candidates

## Candidate C-001 — calibrate compaction threshold to the model's real 65536 context

- **Three-axis tag**: lens=cost/robustness · lever=configuration · intent=fix-crash-cluster
- **Change**: `CompactionProcessor.token_threshold` 140000 → 22000 (single knob). All
  other processors, kwargs, and the sibling `system_prompt.txt` are byte-identical to R7.

### Signal
R7 = 35/50 (0.70). One failure, `task_001032_1adaccb9`, has
`status=agent_error` / `exit_reason=error` — the fatal crash shape the R6
rollback trigger explicitly named as a re-ship condition ("if a NEW cluster of
exit_reason=error crashes appears, act on it") and the exact condition the
post-flight replay gate hard-fails on.

### Verified body evidence
- `task_001032_1adaccb9/oh_runs/.../_trace.jsonl` final event:
  `task_end ... exit_reason='error', error='BadRequestError: Error code: 400 -
  This model's maximum context length is 65536 tokens. However, you requested
  4096 output tokens and your prompt contains at least 61441 input tokens, for a
  total of at least 65537 tokens.'` The provider rejected the request; the run
  crashed rather than compacting.
- The model's **real** max context is **65536**, output reserve **4096**, so the
  real input budget is **61440**. Harness `rough_token_count` (cl100k_base) at
  the crashing step reported only **28689** — a ~2.14× undercount for this
  token-dense content (hex dumps + C code). `CompactionProcessor` fires on
  `rough_token_count > token_threshold`; with `token_threshold=140000` it can
  **never** trigger before the real 65536 wall on this model.
- Growth rate on 001032: ~800–1000 rough tok/step, monotonic (steps 31→45:
  16871→28689). Crash reached at rough≈28.7k. `task_000028_7fe033ac` climbed to
  rough 22421 at its final step (budget_exceeded @80) — the second task that
  approaches the wall.
- Peak rough token_count across all 50 tasks (measured from traces): only
  001032 (28689) and 000028 (22421) exceed 22000; the next-highest passing tasks
  sit at 19829 (001652, pass) and 19044 (001701, pass), staying below threshold.

### Sizing / why 22000
Real/rough ratio ≈ 2.14, real crash budget = 61440. Threshold 22000 rough ≈
47080 real, leaving ~14360 real (~6 steps at this growth rate) of headroom
before the 400 — enough for the compaction summary + retention window (6 msgs) +
one more turn to fit. Fires on the two tasks in the crash zone (001032, 000028),
leaves the ~19–20k passing tasks (001652, 001701) untouched.

### Retroactive check (variant: "would the fix have changed the trace?")
YES for 001032: at step ~38 (rough 22420) compaction would have summarised the
older half of the 91-message history, dropping rough well below 22000 and
keeping every subsequent request under the 61440 real wall — the run continues
instead of 400-crashing at step 45. The underlying tar-header parsing bug may
still fail the task, but the run no longer dies with `exit_reason=error` (which
also fails the post-flight replay gate). PARTIAL for 000028: it reaches 22421
only on its final budget_exceeded step, so compaction fires once at the end;
mainly protects it from a future crash, not a guaranteed flip.

### Why configuration, not control/instruction
The mechanism is a mis-calibrated existing knob, not a missing behavior. The
CompactionProcessor already implements exactly the right remedy (summarise +
evict when context grows); it simply never runs because its trigger is pinned to
a 128k-window assumption while this model's real ceiling is 65536. No new
processor, tool, or prompt guidance can add context-window headroom the way
lowering the trigger does — this is a pure single-knob configuration fix. A
control-lever processor would duplicate CompactionProcessor's logic; an
instruction paragraph ("keep your context short") cannot make a 9B model
self-limit token growth it does not track.

### Pareto framing
- `expected_global_gain`: Eliminates the `exit_reason=error` 400-crash class for
  any task whose context grows past ~28.7k rough tokens (~61.4k real). Currently
  1 hard crash (001032) + 1 task at the edge (000028); generalizes to every
  long-context task on this model, and removes a post-flight replay-gate failure
  risk. Compaction firing on long runs also frees the model from re-reading a
  huge accumulated history each step.
- `regression_risk`: Low. Only 2/50 tasks currently exceed rough 22000, both
  failing; the highest passing tasks sit ~2–3k below the new threshold, so no
  currently-passing task triggers compaction. Residual risk: if a borderline
  passing task grows past 22000 in a future run, its earliest history is
  summarised — but CompactionProcessor preserves the first message (task
  description, `preserve_first_message: true`) and a 6-message retention window,
  and PostCompactionRefreshProcessor is already in the pipeline to re-anchor.
- `cost_shift`: Net reduction on long runs — summarising the older half of a
  growing transcript cuts per-step input tokens on the most expensive tasks
  (001032 was at cumulative_cost $4.42 / 1.27M tokens when it crashed). Flat on
  the 48 short tasks that never reach the threshold.
- `rollback_trigger`: If R9 pass_rate < 34/50 AND either (a) a previously-passing
  task regresses because compaction summarised load-bearing context, or (b)
  001032 still exits with `error`/unchanged, revert `token_threshold` to 140000.
