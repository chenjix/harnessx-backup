ENTRY = r'''

## Round 5 — fuzzy near-identical turn terminator

<!-- journal:frontmatter
round: 5
timestamp: 2026-06-05T00:00:00Z
hypothesis_id: h_fuzzy_loop_terminator_v1
levers: [control]
predicted_affected: [task_000032_3fb303f6, task_001201_1340f4e2, task_001028_5bc8bc70, task_001465_aa3ed3f8, task_001044_45c70cf1]
cited_candidates: [C-005]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Eliminate the exit_reason=error crash cluster (task_000032, task_001201) by converting agent_error into a clean done that guarantees verifier scoring and protects the post-flight replay gate, and reclaim 20-50 wasted step-generations / 600-1600s wall-clock across the drifting-loop cluster (>=5 tasks) so exit-intent processors (verifier-dep guard) fire. Generalizes to any task falling into a NEAR-identical (not byte-identical) assistant-turn loop — the shape R1 (Bash-input) and R4 (byte-identical) structurally cannot cover."
regression_risk: "Near-zero on pass-rate. Every R4 passing task has a max consecutive similarity>=0.85 assistant-turn run of at most 2; terminate_threshold=5 (warn at 3) cannot be tripped by any observed passing trajectory. Residual: a legitimate task that must poll a near-identical action >=5 times could be stopped early — mitigated by the high threshold, min_tokens=5 (short acks ignored), and the two-turn warn window before termination."
cost_shift: "Net decrease. Truncates 20-50 wasted step-generations (several at the 4096-token cap) across the loop cluster including the 828s/1555s/964s runaways. The synthetic stop turn replaces a model call rather than adding one; no new model calls. Warn path adds at most one small user message per looping task."
rollback_trigger: "Revert if R6 pass_rate < 0.20 OR any previously-passing task regresses with a [LoopTerminator] banner implicated in its final steps (threshold too low / disrupted a legitimate near-identical repeat)."
-->

### Why

R0-R4 are all flat at pass_rate 0.20 (10/50). The dominant remaining
harness-level waste is a degenerate assistant-turn loop; the R4
DegenerateTurnTerminatorProcessor was built to convert it into a clean exit but
measurement on the R4 trajectories shows it NEVER FIRED on its target tasks
(grep [LoopTerminator] = 0 in both exit_reason=error crashes and in the
budget_exceeded loopers). The cause: R4's fingerprint requires byte-identical
turns, but the real loops DRIFT — each regenerated "I've been stuck in a loop
... take a fundamentally different approach" turn differs by a few words or a
different 4096-token truncation point. The max byte-identical consecutive
assistant run tops out at 4, below R4's terminate_threshold of 6. A similarity
(Jaccard on normalized token set) counter at 0.85 separates loopers from the
passing set even more sharply: FAILING loopers reach runs of 4/5/6/10/17 while
the entire R4 passing set has a max run of 2. The done-but-wrong-value cluster
that makes up most of the remaining failures is a model-capability wall (wrong
computed numbers/hashes/MSE across ~28 tasks) and is explicitly NOT targeted
this round — no harness fix for those; they need model capability.

### Changes

- processors/fuzzy_loop_terminator.py — new FuzzyLoopTerminatorProcessor
  (MultiHookProcessor, on_before_model): counts consecutive assistant turns
  whose normalized token-set Jaccard similarity is at least 0.85 (byte-identical
  scores 1.0, so it subsumes the R4 exact matcher); warns at 3 (one
  contract-safe user message), force-terminates at 5 via skip_model +
  synthetic_output which the run loop turns into finish_reason=stop ->
  exit_reason=done. Strategy-only, no task literals.
- config.yaml — REPLACED the R4 exact-match DegenerateTurnTerminatorProcessor
  with the fuzzy terminator (warn 3 / terminate 5 / similarity 0.85). Rest of
  the R4 pipeline (R1 BashLoopBreaker, R3 ProactiveVerifierDepGuard, etc.) and
  sibling system_prompt.txt copied byte-for-byte.

### Evidence

See _meta_scratch/candidates.md C-005 for the measured byte-run vs
similarity-run tables. Key: task_000032 byte_run=4 / sim_run=4 (crashed
exit_reason=error, 828s); task_001201 byte_run=4 / sim_run=10 (crashed
exit_reason=error); task_001028 byte_run=4 / sim_run=17; task_001465 sim_run=6;
task_001044 sim_run=5 (all budget_exceeded, steps=80). Passing set: max
sim_run=2 (task_000899), nine of ten equal 1. [LoopTerminator] banner count = 0
in all target messages.json (R4 exact matcher never fired).

### Uncertainty

Partial-yes retroactive check. On the two exit_reason=error tasks the forced
clean stop is a strict robustness win (removes the crash, protects the replay
gate, banks partial work, saves wall-clock). On the budget loopers it does not
by itself add a missing deliverable — most fail on a wrong computed value, a
capability wall — so the honest gain there is cost/step recovery plus a clean
exit that lets the verifier-dep guard exit-intent path run. If R6 shows the
crash tasks still failing with real per-test assertions (not agent_error) AND no
cost reduction on the loop cluster, loop control is fully saturated for this
benchmark and the lever must move off it entirely (the remaining wall is pure
model capability).
'''

path = '/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep12-i4/learnings.md'
with open(path, 'a', encoding='utf-8') as f:
    f.write(ENTRY)
print('appended', len(ENTRY), 'chars')
