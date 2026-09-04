# Candidates — R3

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add two general strategies to the sidecar system prompt: (1) decompose-first
for multi-step tasks (minimal end-to-end + every deliverable on disk before
perfecting any single stage), and (2) a loop-escape protocol (after 2
failures, name the root cause and switch to a structurally different
mechanism, not a tweaked flag).

- Tasks affected (corrective, failing cluster, >=2 distinct):
  - budget_exceeded / thrash: task_000028_7fe033ac, task_000300_7d6b511c,
    task_000010_644ab1c2, task_000585_049544a3
  - (secondary) done-but-never-committed-deliverable path: task_000010_644ab1c2
    (deliverable /home/user/operator.py never written despite 33 distinct steps)
- Signal: `exit_reason=budget_exceeded` on 7/27 R2 failures (down from R1's 11
  after the loop-breaker, so the *byte-identical* loop shape was addressed but
  the *semantic* thrash remains). In the bodies, the agent emits "I'm stuck in
  a loop, let me try a different approach" 16x (028) / 17x (300) yet keeps
  issuing cosmetic variants of the same failing command. Max consecutive
  byte-identical calls dropped to 1-5 (028=5, 300=4), so R2's Strategy-1 warn
  (`warn_threshold=3`) rarely fires; the loop is now argument-varying.
- Verified (Read of messages.json):
  - task_000028_7fe033ac assistant turns 4-6: "I'm stuck in a loop. Let me try
    a different approach to get the frame count from ffprobe." (repeated
    verbatim while re-running near-identical ffprobe commands) — 16 loop/stuck
    mentions across the run; budget_exceeded at 80 steps.
  - task_000300_7d6b511c assistant turns 4-6: "I keep getting stuck in a loop
    running the same cmake command … I need to stop the loop and take a
    fundamentally different approach" — 17 loop/stuck mentions; budget_exceeded.
  - task_000010_644ab1c2: 33 distinct Bash commands spent debugging the mock-API
    port-forward; final_pytest: "Operator script /home/user/operator.py does not
    exist" — the required deliverable was never committed while a hard sub-goal
    consumed the whole budget.
- Why Instruction not Control: the R2 LoopDetectionProcessor (Control) already
  injects "you are stuck in a loop, try something fundamentally different", and
  the agent *already says exactly that to itself* verbatim — the mechanical
  awareness signal is present and being ignored. A Control hook can only
  re-assert the symptom; it cannot supply the *how* (name the root cause,
  enumerate structurally different mechanisms, decompose sub-goals) as
  agent-authored reasoning. The gap is strategy/knowledge, not a missing
  mechanism → Instruction. Configuration (e.g. lowering `warn_threshold`) was
  rejected because the loops are now argument-varying, so exact-match tuning
  can't catch them, and Strategy-2 name-only would fire on nearly every Bash
  call (already disabled for that reason in R2).
- Retroactive check (A-corrective): partial-yes — for the thrash cluster, if
  the agent had (a) decomposed and committed the easy deliverables early and
  (b) abandoned the failing mechanism after 2 tries instead of 16, it would
  have freed budget to reach the required outputs. Not every budget task will
  flip (some are genuinely hard), but the shared blocker is over-investment in
  one failing mechanism, which this strategy directly counters.
- expected_global_gain: Flip a subset of the 7 budget_exceeded thrash tasks by
  converting reactive same-mechanism retries into early decomposition + a
  genuine strategy switch, and by committing deliverables before a hard
  sub-goal exhausts the budget. Generalizes across domains (k8s operator, cmake
  build, ffprobe pipeline, service daemon) because the pattern is
  mechanism-fixation, not domain-specific.
- regression_risk: Prompt now longer; a task that was passing via a single
  clean approach might read the "decompose first" guidance and over-engineer a
  simple task, adding a few steps. Mitigated: guidance is explicitly gated on
  "more than a couple of commands / multiple files / service+client"; simple
  tasks are told nothing new. The strong R1 survey/restate/functional-verify
  content is preserved byte-for-byte, so the done-cluster near-miss behavior is
  unchanged.
- cost_shift: Near-neutral to slightly negative on the thrash cluster
  (abandoning a failing mechanism after 2 tries instead of ~16 cuts wasted
  steps); +small prompt tokens per task. Net expected positive if it flips >=2
  budget tasks.
