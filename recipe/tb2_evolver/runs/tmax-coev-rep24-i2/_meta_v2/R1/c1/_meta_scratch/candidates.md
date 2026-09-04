# Candidates — Round 1 (c1)

## Candidate C-001: Independent-derivation step in self-verify checklist

**Three-axis tag:** lens=verification-discipline / lever=processor (custom) / intent=close-failing-cluster

**Assigned focus:** `task_000011_d089ef35` (scientific_computing) fails.

### Signal (verified from trajectory body)

- `result.json`: `reward=0`, `exit_reason=done`, `finished=no_tool_calls`, 32 steps.
- `final_pytest`: q0=8.25 ✓, q1=17.25 ✓, q2 expected 68.25 got 38.25 ✗, q3 expected 93.25 got 55.25 ✗.
- Root implementation bug (messages.json lines 168–175, 302): agent uses
  `start_row = quadrant / 2` (q2,q3 → row 1) but bottom quadrants start at row 2.
  It correctly fixed the *column* analogue `start_col = (quadrant%2)*2` but never
  applied the same `*2` to the row.
- **Harness-relevant deficiency (lines 388–448):** the agent "verified" by writing a
  Python script that re-implemented the SAME indexing (`orig_row = (i//2)+start_row`
  with the same buggy `start_row`). C server and Python oracle agreed (both wrong),
  so the agent declared success with false confidence and exited. The existing
  `_tb2_self_verify` one-shot fired (line 551) but only prompted a task re-read +
  re-run of the same broken checks — it did not force an independent derivation.

### Why this is a (partial) harness fix, not pure task knowledge

The residual reasoning error (row offset) is a model capability gap — no harness
should inject "multiply row index by 2". BUT the *mechanism* that let a wrong answer
pass as verified is general: the verification oracle mirrored the implementation.
The dominant failure shape this round is `no_tool_calls done → reward 0` (agent
exits believing it succeeded): 30/50 fail, and the majority of those exit `done`.
A checklist step that forces deriving ≥1 expected value BY HAND from the task's own
definitions — independent of the agent's code — is a general strategy that raises the
chance such tasks catch their own bug before exit.

### Intervention

New processor `IndependentSelfVerifyProcessor` (subclass of the stock
`CustomSelfVerifyProcessor`) that keeps identical one-shot firing semantics but
injects a stronger checklist adding step 5: derive expected values by hand from the
spec, for a boundary/edge case, and compare to the running system's actual output;
if they disagree, trust the spec and fix the implementation. No task constants,
IDs, or algorithms embedded. Shares the parent's `_singleton_group` so exactly one
self-verify processor is active.

### Retroactive check (variant: would-it-have-fired)

On task_000011 the self-verify tool fired at the natural exit. With the new text,
step 5 would have instructed the agent to hand-derive q2/q3 from "Bottom-Left =
Rows 2-3" (values 9,10,13,14 → MSE against Ref 2) rather than re-running its own
formula. Hand derivation from the spec yields 68.25 (not 38.25), surfacing the row
bug. Fires exactly where the stock processor fired; strictly more informative text.

### Pareto statement

- `expected_global_gain`: the `no_tool_calls done → fail` cluster (computed-value /
  transformation / service-response tasks across scientific_computing, data_science,
  data_processing, data_querying). Independent-derivation is the canonical antidote
  to "verified against my own reimplementation".
- `regression_risk`: LOW. Same firing gate and singleton group as the stock
  processor; only the injected string changes. Slightly longer prompt → one extra
  hand-derivation could cost a few more steps on already-passing tasks, but does not
  change control flow or block exit. Auto-revert trigger: if global pass-rate drops
  vs R0, revert to stock `CustomSelfVerifyProcessor`.
- `cost_shift`: small positive (a few hundred tokens + possibly 1–2 extra Bash calls
  on tasks where the agent chooses to hand-derive). Bounded by the one-shot gate.

### Why processor lever, not system-prompt / tool

- System prompt: this guidance is only relevant at *exit* time; front-loading it in
  the static prompt dilutes it and it is ignored by exit. The one-shot injection at
  the no-tool-call boundary is where attention is highest.
- Tool: TB2 exposes only `Bash`; cannot add tools (playbook constraint).
- Processor is the exact surface the stock self-verify already uses — minimal,
  reversible, contract-clean.
