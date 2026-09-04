entry = r'''

## Round 7 - collapse max_tokens truncation loop

<!-- journal:frontmatter
round: 7
timestamp: 2026-04-27T00:00:00Z
hypothesis_id: h_truncation_loop_compactor_v1
levers: [control]
predicted_affected: [task_000998_4d9c7852, task_001214_f44c0aa2, task_001028_5bc8bc70, task_000908_170e5e4e, task_001267_0acfd3a0, task_000710_fa628bd4, task_000032_3fb303f6, task_000796_828a72cf, task_000437_8450eef7]
cited_candidates: [C-007]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Recovers dozens of wasted steps across a 9-task truncation-loop cluster (5 domains) that die at budget_exceeded; generalizes to any future task falling into the max_tokens loop (structural trigger, no literals)."
regression_risk: "Fires only on 3+ consecutive near-identical truncated (200+ char, no-tool-call) assistant turns - absent from all passing tasks (passive-nudge count 0). Never blocks/drops/fabricates a tool call; preserves the first narration turn. Long passers (task_000097 nudge=1, task_000628 nudge=0) below the min_run=3 trigger."
cost_shift: "Net down - collapsing the repetition wall shrinks assembled prompts and shortens loops so tasks stop burning the full 80-step budget."
rollback_trigger: "If R8 shows any previously-passing task regressing T->F with a harness collapse note disrupting legitimate work, or pass_rate drops vs R3 incumbent mean, drop the TruncationLoopCompactor registration and keep the R3 pipeline."
-->

### Why

Dominant harness-shaped failure in the r6 draw (11/50): a self-reinforcing
max_tokens verbosity loop. The eval model runs under a hard max_tokens=4096
output cap (set outside config, unchangeable here). On hard tasks it emits
~4096 tokens of prose with NO tool call (finish_reason=length); the run loop
appends a passive "Your previous response was cut off by the token limit.
Please continue" nudge; the model, now staring at a growing wall of its own
near-identical truncated narration, reproduces that wall 8-23x until
budget_exceeded. The pre-existing LengthTruncationRecoveryProcessor fires but
its on_before_model edit is ephemeral (never lands in state.raw_messages), so
the duplicate wall keeps growing and keeps priming the loop.

### Changes

- processors/truncation_loop_compactor.py - new MultiHookProcessor
  TruncationLoopCompactor (on_step_start, order 20, after compaction).
  Collapses 3+ consecutive near-identical truncated assistant turns (200+
  chars, no tool_calls) + interleaved passive nudges into one trimmed turn plus
  one "emit ONE short Bash command" directive. Run loop persists step_start
  structural edits, so the wall is durably removed.
- config.yaml - copied R3 incumbent byte-for-byte, appended the new processor
  registration after PostCompactionRefreshProcessor. Also copied R3
  system_prompt.txt sidecar so SiblingSystemPromptBuilder resolves it beside
  the new config.

### Evidence

- task_000998_4d9c7852 steps 2-34: assistant content truncated to ~1964
  chars every turn (finish_reason=length), prefix "The user is right - I've
  been stuck in a loop. The files are..." repeated x10, each followed by the
  passive nudge; exit budget_exceeded.
- task_001214_f44c0aa2: prefix "...Let me take a..." x11, passive-nudge count
  21, budget_exceeded.
- task_000908_170e5e4e: prefix "...Let me just w..." x13, passive-nudge count
  18, budget_exceeded.
- All 8 short passing tasks and the 2 long passers have passive-nudge count 0-1
  (below min_run=3), so the trigger is provably absent from the passing set.

### Uncertainty

Blocking the loop is necessary-not-always-sufficient: some of these 9 tasks
also carry a content-correctness gap, so freed steps may still end wrong. The
guaranteed win is converting budget-burned-in-a-loop into a fair scored attempt
at lower cost. Distinct from R2 (identical-tool-call breaker, still live) and
the reverted R5 (tool-block) - this touches only truncated narration turns,
never tool calls. Rollback if any passer regresses with the collapse note in
its trace.
'''
path = "/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep12-i3/learnings.md"
with open(path, "a") as f:
    f.write(entry)
print("APPENDED", len(entry), "chars")
