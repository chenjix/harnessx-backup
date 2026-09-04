# Candidates — R5

## Candidate C-005
[lens: failure | lever: control | intent: corrective]

Replace the R4 byte-identical assistant-turn terminator with a similarity-based
(Jaccard >= 0.85 on the normalized token set) near-duplicate terminator that
warns at 3 and force-stops at 5 consecutive near-identical turns.

- Tasks affected (failing cluster, same mechanism — drifting narrative loop):
  - crash: task_000032_3fb303f6 (exit_reason=error), task_001201_1340f4e2 (exit_reason=error)
  - budget/loop: task_001028_5bc8bc70, task_001465_aa3ed3f8, task_001044_45c70cf1
- Signal: `exit_reason=error` (agent_error) on 000032/001201 and
  `exit_reason=budget_exceeded` at steps=80 on the loopers; the R4
  `[LoopTerminator]` banner appears **0 times** in all of them (grepped the
  messages.json). The R4 exact-match fingerprint never fires because the loops
  drift.
- Verified (Read, R4 messages.json):
  - task_000032_3fb303f6: 39 assistant turns; the "I've been stuck in a loop
    trying to ... operator.py / password ... take a fundamentally different
    approach" turn recurs ~a dozen times but each differs by a few words.
    Measured max **byte-identical** consecutive run = 4 (below R4 threshold 6),
    but max **similarity>=0.85** run = 4. Run then crashed to
    `exit_reason=error` at step 62 after 828s.
  - task_001201_1340f4e2: 44 assistant turns; repeated "I've been stuck in a
    loop trying to fix the same issue with st.builds ..." interleaved with
    length-recovery "continue from where you left off" injections. byte run = 4,
    **similarity run = 10**. Crashed to `exit_reason=error` at step 44.
  - task_001028_5bc8bc70: byte run = 4, **similarity run = 17**;
    task_001465_aa3ed3f8: similarity run = 6; task_001044_45c70cf1: similarity
    run = 5 — all budget_exceeded at steps=80.
  - Passing-set safety (all 10 R4 passers, measured): max similarity>=0.85 run =
    2 (task_000899), the other nine = 1. terminate_threshold=5 leaves the entire
    passing set untouched with a wide margin.
- Why Control not Instruction: the R2 system prompt already tells the model to
  stop looping and bank partial work; four rounds of trajectories show it
  ignores that instruction and the R1 soft "BLOCKED" redirect (re-emitting the
  near-identical turn 4-17 more times). Instruction + soft redirect are
  proven-saturated on this cluster; the missing capability is a *mechanical hard
  stop*, and the loop text-shape detection must fire uniformly across every task
  — a shape only an `on_before_model` processor can express.
- Why fuzzy Control not re-shipping the R4 exact Control: R4's mechanism was
  right in spirit but its byte-identical fingerprint is too brittle for the
  drifting loops (fired 0 times). The evidence-backed fix is to relax the
  match to token-set similarity; identical turns still score 1.0, so the fuzzy
  matcher subsumes the exact one — hence R5 replaces rather than stacks.
- Retroactive check (A-corrective): partial-yes. On the two `exit_reason=error`
  tasks the forced clean stop is a strict robustness win — it removes the crash
  (which also protects the post-flight replay gate), guarantees the verifier
  scores whatever partial work exists, and reclaims 600-800s of wall-clock.
  Whether each budget looper then *passes* still depends on model capability
  (most fail on a wrong computed value, a capability wall), so the honest gain
  is robustness + large cost/step recovery, with pass-rate upside concentrated
  on the crash cluster. This is deliberately NOT claimed as a broad pass-rate
  flip: R0-R4 confirm the dominant done-but-wrong cluster is capability-bound.
- expected_global_gain: Eliminates the `exit_reason=error` crash cluster (2
  tasks) by converting agent_error into clean done, and recovers wasted
  budget/wall-clock on the drifting-loop cluster (>=5 tasks) so exit-intent
  processors (verifier-dep guard) can fire. Generalizes to any future task that
  falls into a *near*-identical (not byte-identical) assistant-turn loop — the
  exact shape R1 (Bash-input) and R4 (byte-identical) structurally cannot cover.
- regression_risk: Near-zero on pass-rate. Every R4 passing task has a max
  similarity>=0.85 consecutive run of at most 2; terminate_threshold=5 (warn at
  3) cannot be tripped by any observed passing trajectory. Residual risk: a
  legitimate task that must poll one near-identical action >=5 times could be
  stopped early — mitigated by the high threshold, the min_tokens=5 guard
  (short polling acks are ignored), and the fact that no passing trajectory
  approaches the threshold. The warn nudge at 3 gives the model two turns to
  self-correct before termination.
- cost_shift: Net decrease. Truncates 20-50 wasted step-generations (full model
  calls, several at the 4096-token cap) across the loop cluster including the
  828s/1555s/964s runaways. The synthetic stop turn replaces a model call rather
  than adding one; no new model calls. The warn path adds at most one small user
  message per looping task.
- rollback_trigger: Revert if R6 pass_rate < 0.20 OR any previously-passing task
  regresses with a `[LoopTerminator]` banner implicated in its final steps
  (threshold too low / disrupted a legitimate near-identical repeat).
