# Candidates — R4

Context: R0–R3 flat at 29–31/50. The **control** lever (loop guards)
has been pulled twice: R1 command-string advisory flipped 0/5; R3
(command,output) hard-block flipped 1/3 and lost 2. Trajectory
re-read confirms the escalation ladder is exhausted — the R3 block
DID fire (34 block injections on task_000264) yet the agent re-requested
the identical blocked command until agent_error, and elsewhere it
side-steps the fingerprint by making cosmetic command variations. Neither
advisory nor hard-block changes this model's behaviour.

The **instruction** lever has NEVER been tried (scoreboard: 0 attempts).
The current system prompt is a bare 5 lines with no strategy guidance.
The tb2-playbook names "explicit plan", "double-confirmation before exit",
"upfront environment survey" and "non-interactive discipline" as the
biggest observed levers on this benchmark — none are in the current prompt.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Replace the bare 5-line system prompt with a general work-discipline
prompt: survey → plan → implement → **verify-against-spec before exit**,
plus an **anti-thrash reset** rule (when the same approach fails
repeatedly, stop and question the core assumption instead of re-running
variations). All general strategy, no task-specific literals.

- Tasks affected (>=2 distinct failing, two mechanisms sharing one root
  = "commits/persists without re-checking the spec"):
  - Premature confident commit: task_001653_c4cafa73 (16 msgs, ~8 bash
    calls, declared "exact format" done on a precise-algorithm task),
    task_000933_1f27096a (20 msgs, fast exit, "all requirements
    fulfilled"). The ~16 fails whose tails read "✅ task complete /
    all requirements met" while reward=0 share this shape.
  - Thrash-without-replanning: task_000264_ab8c7253 (ran one exact SQL
    24× consecutively, narrating "stuck in a loop"), task_001031_a8f0eb37
    (21× same mpi4py signature prefix), task_000958_4bb2b05d,
    task_001818_b251e5ea. All re-run near-identical commands rather than
    questioning the underlying assumption.
- Signal: last-assistant tails across the failing cluster are confident
  completion summaries (`"The task is complete"`, `"All requirements
  have been met"`, `"✅"`) with reward=0; thrash subset narrates
  `"I've been stuck in a loop"` in the body while re-issuing the same
  command. `exit_reason` mostly `ok`/`done` (not budget) — the agent
  *chose* to stop or kept looping, it wasn't cut off.
- Verified (Read, bodies quoted):
  - task_001653 first user msg demands a C ETL program with precise
    bootstrap/rounding; agent made ~8 bash calls then last-assistant:
    "All files are in place. The task is complete ... The output" —
    committed without re-deriving the spec's numeric requirements.
  - task_000933 last-assistant: "The task is complete. All requirements
    have been fulfilled: 1. C++ source ... compiles correctly ..." after
    20 msgs — confident exit, no spec re-check.
  - task_000264 body tail: "I've been stuck in a loop running the same
    query ... Let me think about this more carefully" then re-issues the
    same CTE; measured 24 consecutive byte-identical commands.
  - task_001031 body tail: "I've been stuck in a loop trying the same
    Allgatherv signature ... Let me try a different approach" then
    re-issues a near-identical signature (21× same 60-char prefix).
- Why Instruction not Control: the control lever was already pulled
  twice on exactly this loop cluster (R1, R3) and could not convert —
  a mechanical hook cannot make the model *devise a new hypothesis* or
  *re-check correctness against a spec it must infer*. The gap is
  knowledge of **when to stop, re-read, and pivot**, which is a
  prompt-level discipline, not a missing mechanical hook or tool. A
  Control hook injecting "verify before exit" text is exactly the R1
  advisory shape that the model ignored; keeping the discipline in the
  standing system prompt (always in context, not a one-shot append)
  is the untried variant.
- Why Instruction not Configuration: no existing knob encodes work
  discipline; the prompt is the only surface that carries standing
  strategy.
- Retroactive check (A-corrective): partial-yes. For the thrash subset
  a standing "when an approach fails 2–3×, change the assumption not the
  command" rule addresses the exact narrated failure the loop guards
  could not. For the premature-commit subset, a standing "before you
  stop, re-read every explicit requirement and confirm each named output
  exists and matches format/precision" rule targets the exact decisive
  step (agent stopped early, confidently, without re-checking). Flips are
  probabilistic — the prompt cannot supply domain reasoning — but it
  changes the stop/pivot decision that currently ends these tasks at 0.
- expected_global_gain: the failing cluster spans data_querying,
  scientific_computing, software_engineering, data_science — a
  cross-domain discipline gap. Even a modest lift on the premature-commit
  and thrash subsets (the two largest failing shapes) improves multiple
  domains at once; the guidance generalises to unseen tasks because it
  is about process, not answers.
- regression_risk: a longer prompt could (a) inflate tokens on the 30
  passers, (b) push a currently-fast passer into over-verification. Kept
  low by writing concise general strategy (not verbose), preserving the
  existing "work under /home/user", "non-interactive", "one Bash tool"
  facts verbatim, and NOT adding mandatory-copy code or task literals.
  The passing cluster already surveys+plans (verified: task_000024,
  task_000818, task_001591 open with a task decomposition), so the
  survey/plan rules describe what passers already do — near-zero
  disruption there; the net-new content is the pre-exit verify + pivot
  discipline that only bites when the agent would otherwise stop wrong.
- cost_shift: small increase — a few hundred prompt tokens per task and
  possibly 1–3 extra verification bash calls on tasks that would
  otherwise stop early; offset on the thrash tasks, which currently burn
  70–97 steps and should terminate sooner once the agent pivots.
- rollback_trigger: if R5 pass_rate < R4 (i.e. < R3's 30) AND none of
  task_001653/000933/000264/001031 flip, OR any two currently-passing
  tasks regress with no offsetting flip, revert system_prompt.txt to the
  R3 bare 5-line version.
