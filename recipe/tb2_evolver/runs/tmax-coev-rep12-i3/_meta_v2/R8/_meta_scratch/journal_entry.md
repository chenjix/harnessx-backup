
## Round 8 — durably break identical-command loops

<!-- journal:frontmatter
round: 8
timestamp: 2026-04-27T16:00:00Z
hypothesis_id: h_identical_command_loop_compactor_v1
levers: [control]
predicted_affected: [task_000730_265f23f6, task_001028_5bc8bc70]
cited_candidates: [C-008]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Recovers ~20-30 wasted steps each on the identical-Bash-command loop subcluster (task_000730 x31, task_001028 x19) that die at budget_exceeded, converting a guaranteed budget-loss 0 into a fair scored attempt; generalizes to any future task that falls into a byte-identical Bash loop (structural trigger, no literals)."
regression_risk: "Near-zero: fires only at 10-plus consecutive identical non-empty single-Bash commands; the passing set's max identical run is 7 (task_001537, a truncation-interleaved iterative PASS), so the collapse provably cannot fire on any currently-passing R7 trajectory. Never blocks/drops/fabricates a tool call (commands already ran); contract-clean net message reduction ending on one user directive."
cost_shift: "Net down: collapsing the repetition wall shrinks the assembled prompt and the directive breaks the loop early, so looping tasks stop burning the full 80-step budget."
rollback_trigger: "If R9 shows any previously-passing task regressing T->F with a harness collapse note ('your previous N turns issued the SAME command') in its trace disrupting legitimate iteration, or pass_rate drops vs the R7 incumbent mean, drop the IdenticalCommandLoopCompactor registration and keep the R7 pipeline."
-->

### Why

R7 = 13/50. No exit_reason=error remain (R1 fix holds). The budget_exceeded
family is still the largest failure bucket. Within it, a subcluster burns the
whole 80-step budget on ONE byte-identical single Bash command repeated turn
after turn, getting the same (empty/failing) result each time. This is distinct
from the finish_reason=length narration loop that the R7 TruncationLoopCompactor
handles (those turns carry NO tool call); here every turn emits a well-formed
Bash tool call. The R2 RepeatCommandBreaker (registered, order 6) is a proven
SILENT NO-OP — its on_before_model redirect never lands in state.raw_messages,
so the nudge evaporates each step and the model never sees it (grep of "loop
detected by the harness" across all 50 R7 traces = 0 hits). The reverted R5
blocker persisted but BLOCKED execution and disrupted legitimate iteration.

### Changes

- `processors/identical_command_loop_compactor.py` — new MultiHookProcessor
  IdenticalCommandLoopCompactor (on_step_start, order 21, right after the R7
  TruncationLoopCompactor at 20). Collapses a maximal run of min_run-plus
  consecutive assistant turns whose single Bash tool call is byte-identical
  (with their interleaved tool-result messages) down to the FIRST turn + its
  result, and appends ONE corrective directive user message. Runs at
  on_step_start so the edit changes history_hash and the run loop auto-generates
  a SegmentBoundary that writes the trimmed window durably into
  state.raw_messages/state.messages (runloop.py ~345-368) — the same durable
  path the R7 compactor uses. NEVER blocks/drops/fabricates a tool call.
- `config.yaml` — copied R7 byte-for-byte; registered the new processor after
  TruncationLoopCompactor with min_run=10 (safely above the passing set's max
  identical run of 7). system_prompt.txt copied byte-for-byte from R7.

### Evidence

- `_meta_scratch/dupscan.py` over all 50 R7 traces (max consecutive identical
  non-empty single-Bash run): task_000730_265f23f6 = 31, task_001028_5bc8bc70
  = 19 (both budget_exceeded, reward 0); highest PASSING task = task_001537
  = 7 (truncation-interleaved iterative disasm, PASSES); all other passers 3 or below.
- `task_000730_265f23f6`: `tesseract /app/bug_report.png stdout 2>&1` issued
  31 consecutive identical times; exit budget_exceeded.
- `task_001028_5bc8bc70`: `strings /home/user/log_analyzer` issued 19
  consecutive identical times; last assistant even narrates "I've been stuck in
  a loop repeatedly running the same strings command"; exit budget_exceeded.
- No "loop detected by the harness" redirect appears in either trace, proving
  the R2 breaker is inert on exactly the tasks it was meant to fix.
- runloop.py 345-368: on_step_start message mutation triggers auto
  SegmentBoundary which rewrites state.raw_messages/state.messages (durable).

### Uncertainty

Breaking the loop is necessary-not-always-sufficient: the freed steps may still
end in a content-correctness gap (both tasks are hard reverse-engineering / OCR
problems). The guaranteed win is converting budget-burned-in-a-loop into a fair
scored attempt at lower cost. Distinct mechanism and distinct hypothesis id from
the reverted R5 blocker (that intercepted on_before_tool with approved=False and
blocked execution at threshold 4; this trims already-produced history without
blocking, at a passing-set-safe threshold 10). If any passer regresses with the
collapse note in its trace, revert per the rollback trigger.

Skipped this round (model capability / correctness gaps, no harness fix): the
~20 tasks that exit done/no_tool_calls having self-verified as complete (the
CustomSelfVerifyProcessor checklist already fires once) but whose hidden verifier
finds wrong values / missing files (e.g. task_000763 PIN 2778 vs 2394;
task_000567 PC1_Sum 6.95 vs 7.77). Embedding the correct answers would be
task-specific injection, not a harness mechanism. The verifier-requests
ImportError floor (task_000796/task_000910/task_002108, budget_exceeded) was
addressed by R4's accepted dep guard but dropped in the R5-to-R3 revert; not
re-added this round because the dep guard alone did not flip them in R4 (the
services are also functionally wrong), so it is pure insurance with no expected
flip — deferred to keep this round's attribution clean.
