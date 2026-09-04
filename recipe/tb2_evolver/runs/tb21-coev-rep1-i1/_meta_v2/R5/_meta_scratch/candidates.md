# Candidates — R5

## Candidate C-004
[lens: failure | lever: control | intent: corrective]

Add a `SemanticLoopBreaker` `MultiHookProcessor` that detects repeated
near-identical assistant reasoning turns (a semantic loop the byte-hash loop
detector misses) and injects one bounded redirect that forces the agent to
commit its current best output to the required path and change strategy.

- Tasks affected (>=2 distinct, same mechanism):
  vulnerable-secret, protein-assembly, write-compressor,
  adaptive-rejection-sampler, llm-inference-batching-scheduler,
  sam-cell-seg, train-fasttext (all `passed:false`).
- Signal: max count of an identical normalised assistant-content prefix within a
  single run (aggregated across all sessions of the trial). Clean Pareto
  separation in R4:
  - PASSING: configure-git-webserver=2, git-leak-recovery=2, kv-store-grpc=2
  - FAILING loops: write-compressor=48, vulnerable-secret=41,
    protein-assembly=39, adaptive-rejection-sampler=32,
    llm-inference-batching=10, sam-cell-seg=5, train-fasttext=5,
    overfull-hbox=3.
  Hash/exact-match loop detection misses these because tool-call IDs and the
  exact command bytes differ each iteration while the *reasoning content* is
  verbatim identical.
- Verified (Read, body-quoted):
  - vulnerable-secret session `abdee88b…` steps 25–49: every assistant turn
    begins verbatim `"Let me analyze the disassembly more carefully. I see
    that:\n\n1. At \`401200\`…"` and the paired tool result is the identical
    `objdump .rodata` dump; `/app/results.txt` never written → verifier
    `FileNotFoundError: '/app/results.txt'`. Agent later even narrates
    `"I'm stuck in a loop repeating the same command. Let me take …"` (x41).
  - protein-assembly session `8f31106c…`: `"I'm stuck in a loop trying the
    same commands. Let me …"` (x8) and `"The PDB API is consistently
    returning 404 errors. Let me try …"` (x39); `/app/gblock.txt` never
    written.
  - adaptive-rejection-sampler session `81b2107a…`: `"Let me try a different
    approach. The issue is that the \`sample_envelope\`…"` (x32); no
    `normal_samples.txt` / `exponential_samples.txt` produced.
  - llm-inference-batching-scheduler session `dbe854aa…`: `"I keep making the
    same mistake. Let me take a fundamentally …"` (x10).
  - write-compressor: `"I keep making the same mistake by repeatedly modifying
    the script…"` (x48).
  - Passing controls: configure-git-webserver / git-leak-recovery /
    kv-store-grpc all top out at maxrep=2 — they will not trip the
    threshold=4 detector.
- Why Control not Instruction: the agent already *knows* it is looping (it
  literally says so) — a prompt rule telling it "don't loop" adds no
  information it lacks; the gap is a missing *mechanical* intervention that
  fires from outside the model's own control flow to interrupt the pattern and
  re-anchor on the deliverable. A prompt edit cannot detect the repetition or
  break it mid-run. Why Control not Configuration: no existing knob expresses
  "N repeated reasoning signatures"; the stock loop detector is byte-hash based
  and structurally cannot see semantic repetition, so tuning it does nothing.
- Retroactive check (A-corrective): yes — on vulnerable-secret /
  adaptive-rejection-sampler / protein-assembly the agent had a concrete
  best-effort candidate in hand and simply kept re-deriving it; a redirect at
  the 4th repeat that says "write your current best answer to the required path
  now, then try something genuinely different" gives the file-exists precondition
  a chance to pass and frees ~15-90 wasted steps for a new approach. It is not a
  guaranteed content-correctness flip on the pure-capability tasks (feal-style
  cryptanalysis, PDB behind blocked network), but for the file-never-written
  cluster the required first verifier check (file exists / non-empty) becomes
  reachable.
- expected_global_gain: closes the largest R4 failing cluster (7 tasks stuck in
  repeated-reasoning loops that burn to max_steps without committing the
  deliverable). Generalises because the trigger is content-repetition, not any
  task-specific string — any future task where the model spins will be caught.
- regression_risk: low and bounded. The 3 passing clusters top out at maxrep=2,
  well under threshold=4, so the redirect never fires for them. Worst case an
  agent that legitimately phrases 4 turns similarly gets one extra user message
  (cost only, capped at max_interventions=3 per run). The processor never
  swallows tool calls, never fabricates a keepalive, and never blocks the run
  from ending — it only appends a contract-safe +1 user message when the prior
  role is not `user`.
- cost_shift: neutral-to-down. Looping tasks currently burn to max_steps
  (protein-assembly 118 / vulnerable-secret 120 Bash calls) with zero forward
  progress; a redirect that ends the spin earlier (agent commits a file, then
  SUCCEEDs, or converges faster) reduces wasted steps. Bounded by
  max_interventions=3.
- rollback_trigger: if R6 shows these 7 tasks still `passed:false` AND their
  runs still exhibit maxrep>=4 with no output file, the redirect is being
  ignored (deeper capability gap) — revert the processor. Revert immediately if
  any of configure-git-webserver / git-leak-recovery / kv-store-grpc regresses.

Novelty vs reverted R3 (`h_output_artifact_keepalive_v1`): different mechanism.
R3 fired on a *terminal no-tool-call stall* and fabricated a synthetic keepalive
tool call to prevent the run from ending. This fires *mid-run while the agent is
still emitting tool calls*, keyed on **reasoning-content repetition**, injects
only one bounded user redirect, and never blocks run termination. Different
lens signal (repetition, not stall), different hook trigger, different action.
