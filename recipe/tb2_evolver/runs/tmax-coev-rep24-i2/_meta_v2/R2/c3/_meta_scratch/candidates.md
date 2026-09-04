# Candidates — R2 / c3

Assigned focus: `task_000111_cbada64a` (scientific_computing) fails.

## Diagnosis of the assigned task

`task_000111_cbada64a`: agent writes a C++ program to do OLS linear
regression + a seeded (`std::mt19937(12345)`) bootstrap 95% CI over squared
residuals, then write `m,c,ci_lower,ci_upper` to `/home/user/result.txt`.
It compiles and runs cleanly, produces `2.5056,1.2262,3.9742,6.2925`, and the
agent exits after only checking file existence + format. Verifier expects
`m ≈ 2.5997`; got `2.5056` → reward 0 (`final_pytest` assertion).

The agent's OLS is textbook and deterministic, so the mismatch is a real
computational bug (data-read / formula / precision), not RNG variance (`m` is
RNG-independent). The decisive harness-relevant fact: **the agent never
independently re-derived the number.** Its self-verify turn (steps 6-8) only
ran `cat`/`ls` and checked the *format*, never recomputed `m` a second way
(e.g. `numpy.polyfit` on the same CSV) which would have surfaced the
discrepancy. The verifier is hidden during the agent phase, so there is no
in-loop correctness signal — the agent's only defence against a subtly-wrong
number is an independent recompute, and nothing in the harness prompts one.

This is not a single-task quirk: it is the dominant shape of the
`scientific_computing` failing cluster (see Tasks affected).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `NumericCrossCheckNudgeProcessor` that, on the first Bash command that
*runs* a numeric/scientific computation (interpreter/compiler/binary run +
numeric vocabulary or a write to a `result`/`output`-style artifact), appends
a one-shot strategy-only nudge to that tool result telling the agent to
recompute the key quantity by an independent method and reconcile any
discrepancy before finishing.

- Tasks affected (corrective, ≥2 distinct, same mechanism):
  - `task_000111_cbada64a` — OLS slope 2.5056 vs expected 2.5997; single
    self-consistent run, no independent recompute.
  - `task_001330_f5aff1f5` — Monte-Carlo mean slope `m=0.048` vs expected
    `0.050`; verifier hint literally says "Ensure you used numpy.random.seed(42)
    and the correct parameters" — a subtle RNG-order/parameter slip the agent
    never cross-checked.
  - (adjacent, same cluster) `task_000011_d089ef35` — computed server response
    values wrong (50.75 vs 93.25); numeric result never re-derived.
- Signal: `domain=scientific_computing`, `reward=0`, `exit_reason=done`,
  short step counts (7 / 14), `final_pytest` assertion on a numeric value that
  is close-but-wrong. Agent's final steps are `ls`/`cat`/format checks only.
- Verified (Read):
  - `task_000111` messages step 5 (`assistant`) writes the OLS+bootstrap C++;
    step 6 runs `g++ -O3 … && ./analyze && cat result.txt` → `2.5056,1.2262,…`;
    steps 7-9 (post self-verify) run only `ls -lh …` and `cat result.txt` — no
    second-method recompute. `result.json.final_pytest`: `Expected m to be
    approx 2.5997, got 2.5056`.
  - `task_001330` messages step 1 restates the seeded Monte-Carlo plan;
    `result.json.final_pytest`: `Expected m=0.050, but found m=0.048. Ensure
    you used numpy.random.seed(42) and the correct parameters.` No independent
    recompute in the trajectory.
- Why Control not Instruction: the fix must fire *only* on numeric-compute
  tasks and *at the moment a numeric answer is produced*, not as a blanket
  system-prompt rule that taxes every task (file-ops, sysadmin, security) with
  irrelevant "recompute your numbers" text and inflates tokens globally. A
  Control hook keyed on a numeric-run signature is the narrowly-scoped lever;
  it also survives future prompt edits (the R1 sibling-prompt constraint keeps
  `system_prompt.txt` byte-stable across builders).
- Why Control not a new keepalive-at-exit hook: the existing
  `CustomSelfVerifyProcessor` already owns the exit-intent turn (converts
  no-tool-call exit into a keepalive). Injecting a second keepalive on the same
  turn risks a +2 message-insertion contract violation. Appending to the
  compute step's tool result (the `CustomEditToolProcessor` pattern) is
  contract-safe and lands the guidance *earlier*, while the agent is still
  iterating, so it has room to act on it.
- Retroactive check (A-corrective): yes (probabilistic). If the nudge had
  fired after `task_000111`'s `./analyze` run, an independent `numpy.polyfit`
  recompute on the same CSV would either (a) expose the agent's slope bug and
  let it fix the C++, flipping the task, or (b) agree with 2.5056 — in which
  case the reference/data is the discrepancy source and the task is genuinely
  unfixable at the harness layer (nudge is a harmless no-op). For
  `task_001330`, a careful re-derivation prompted by the nudge is the standard
  way to catch a seed/parameter-order slip flagged by the verifier hint.
- expected_global_gain: raises the odds on the `scientific_computing` failing
  cluster (≥3 tasks share the "clean-looking but slightly-wrong number, never
  re-derived" shape). Generalizes because the nudge encodes a domain-agnostic
  verification *strategy*, not any task's answer.
- regression_risk: very low. Purely additive to one tool result string, fires
  at most once, and only when a numeric-run signature matches — non-numeric
  clusters (file_operations, sysadmin, security, most software_engineering)
  never see it. Worst case on a numeric task that was already passing: one
  extra recompute step (a few tokens/seconds) that confirms the right answer.
  Rollback trigger: if any currently-passing scientific_computing task
  regresses, or replay/synthetic errors, revert.
- cost_shift: +0 on non-numeric tasks. On numeric tasks, +1 short recompute
  Bash step (seconds, small token cost) when the agent heeds the nudge; the
  nudge text itself is ~1 short paragraph appended once.
