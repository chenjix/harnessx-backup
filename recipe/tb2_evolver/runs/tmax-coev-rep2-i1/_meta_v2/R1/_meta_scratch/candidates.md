# R1 Candidates — tmax-coev-rep2-i1

## Candidate C-001 — De-prime the max_tokens repetition loop

**Three-axis tag**: lens=trajectory-cluster / lever=Processor (author) / intent=fix-recurring-harness-deficiency

**Schema**: replace stock `LengthTruncationRecoveryProcessor`
(singleton group `tmax_length_recovery`, `_order=5`) with authored
`LengthLoopDeprimeProcessor` at same slot. Params
`repeat_threshold=2, head_chars=1200, tail_chars=600, deprime_after=2`.

**Signal / evidence**:
- R0 pass rate 29/50. Cluster `budget_exceeded` at the 80-step cap:
  tasks 010, 118, 264, 958, 1321, 1701, 1818 (7 tasks, all ~70-82 msgs).
- JSONL traces confirm the stock `LengthTruncationRecoveryProcessor`
  DOES fire (corrective nudge + truncation marker present 33-39x per
  looping task) — yet the model keeps emitting the identical narration.
- Root cause: each truncated no-tool narration turn is persisted in
  full; by step 50 the same narration paragraph appears ~6x in context
  (verified in task 264 step-50 context), re-priming the exact same
  runaway generation. The nudge alone does not break the self-priming.

**Mechanism**: on repeated consecutive length-truncations
(`>= deprime_after`), drop the runaway narration to a compact stub so
history stops re-showing the loop text, and escalate the user nudge to
a hard "single Bash command, no prose" directive. First truncation
retains the stock head+tail collapse (early reasoning preserved).

**Retroactive check (variant: would-this-have-helped)**:
- 264 (recursive CTE) & 958 (sqlite syntax / server not listening):
  model's narration already NAMED the real bug ("near backups: syntax
  error"). Breaking the loop + forcing one concrete command gives the
  model fresh, de-primed context to convert insight → action. Plausible
  flip.
- 010 (operator.py stdlib shadow), 1321, 1701: mixed — loop-break
  reclaims budget but underlying error may be a capability gap. Lower
  confidence.

**Tasks affected (predicted_affected)**: 264, 958, 1818 (primary);
118, 1321, 1701 (secondary/budget-reclaim). Rule: only tasks that
exhibit ≥2 consecutive `finish_reason=length` no-tool turns are touched
at all — the deprime branch is gated on that exact signal.

**Regression risk**: LOW. The processor only activates on the
repetition signature (consecutive length-truncations with no tool
call). Passing long-horizon tasks (msg counts 48-98: 001536, 001652,
001498, 001031, 001673, 001089 …) do NOT exhibit this signature — they
make tool calls each turn and finish_reason is `stop`/`tool_calls`, not
`length`. So they are never mutated. This is precisely why message-count
threshold tuning was REJECTED (passing tasks reach 98 msgs; the
distinguishing signal is truncation-recurrence, not length).

**Cost shift**: neutral-to-negative (good). De-priming shrinks persisted
context on looping tasks, reducing per-call token cost inside loops; if
loops break earlier, fewer steps consumed.

**Rollback trigger**: if R2 shows net pass-rate drop OR any previously
passing long-horizon task (001536/001652/001498/001031) regresses,
revert to stock `LengthTruncationRecoveryProcessor`.

## Rejected alternatives
- Lower `CompactionProcessor.message_threshold` 100→45: REJECTED —
  10 passing tasks have 40-98 msgs; would summarize/evict their needed
  context → likely regression. Message count does not separate healthy
  long work from the loop.
- History pruning in `on_before_model`: REJECTED — contract forbids
  `len_delta < 0`.
- Stronger nudge only (no de-prime): low value — the existing nudge
  already fires and fails; the persisted repeated narration is the
  actual re-priming driver.
- "done-but-failed" cluster (13 tasks: wrong centroid math, grid=60
  vs 50, bypass detection, ModuleNotFoundError requests): mostly model
  capability/correctness gaps — no harness fix. Self-verify checklist
  already covers file-existence; it cannot catch "your math is wrong."
