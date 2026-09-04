# Candidates — R1 / c0

Focus: `task_000010_644ab1c2` (assigned). Diagnosis: `exit_reason=budget_exceeded`
at 80 steps. The task needs a k8s operator script; the agent got trapped in an
`operator.py`-shadows-stdlib circular-import loop and spent ~20 steps renaming
files back and forth. The *specific* circular-import signature is idiosyncratic
(1/50 tasks). But the assigned failure is one instance of a **systemic, recurring
loop pathology** across the whole `budget_exceeded` cluster: the model repeats a
byte-identical Bash command many times, verbally acknowledging it is stuck, while
the existing loop-breaker only emits advisory *text* that the model ignores.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Upgrade `RepeatedCommandBreaker` from advisory-text-only to a mechanical **hard
block**: once a normalised Bash command has been requested `block_threshold`
(=7) times, refuse to execute it (`approved=False`) and inject a synthetic
pivot directive instead of running it again.

- Tasks affected (same mechanism, distinct inputs):
  - `task_000028_7fe033ac` — same `pkill … server` restart command re-issued
    12+ times *after* the hard warning; `budget_exceeded`, reward 0.
  - `task_000506_c13429e7` — same `wfg_analyzer.py` test command run 21 times;
    `budget_exceeded`, reward 0.
  - `task_001207_44e97fe1` — same `sanitizer.py` verification command repeated to
    budget exhaustion; `budget_exceeded`, reward 0.
  - `task_000010_644ab1c2` (assigned) — rename/rewrite loop on the shadowing
    `operator.py`; ~20 steps burned re-issuing near-identical edit/run commands,
    `budget_exceeded`.
- Signal: `exit_reason=budget_exceeded` (8/50 tasks, 7 failing). Bodies show the
  existing breaker's `[RepeatedCommandBreaker] STOP …` text firing at counts of
  8, 9, 10, 12, 17, 18, 19, 20, 21 — proving the advisory is emitted and then
  ignored on the very next turn.
- Verified (Read of message logs):
  - task_000028 tail: assistant re-issues the identical `pkill -9 server …`
    command at breaker counts 8→12, each preceded by "I've been stuck in a loop.
    Let me take a concrete action" then the same bytes.
  - task_000506 tail: identical `echo -e "…" | python3 … wfg_analyzer.py 1 3`
    at counts 17→21, narrating "I've been stuck in a loop" each time.
  - task_001207 tail: identical `python3 /home/user/sanitizer.py "a/b/c/…"` run
    repeatedly, "The script is complete and working correctly" then re-run.
  - task_000010 steps 30–56: rename cycle `operator.py`→`k8s_operator.py`→… ;
    breaker fired at count 3 (step 42) but only as text; agent kept flailing.
- Why Control not Instruction: the model already *reads and verbally agrees with*
  the advisory instruction ("I've been stuck in a loop") and then repeats anyway
  — a stronger prompt rule is the same class of signal it already ignores.
  The gap is mechanical enforcement, which only a processor that intercepts the
  call (`on_before_tool` → `approved=False`/`synthetic_result`) can provide. This
  is the exact `BgInstallGuard` interception contract, contract-safe.
- Why Control not Configuration: no existing knob turns the soft breaker into a
  blocking one — the block path (`on_before_tool` interception) does not exist in
  the r0 processor at all; it is new mechanism, not a re-tuning.
- Retroactive check (A-corrective): yes. In all four cited tasks the wasted steps
  were spent re-running an identical command; blocking it at count 7 reclaims
  10–15+ steps of the 80-step budget and forces a different action at the exact
  point the model was looping — including task_000010, where a forced pivot off
  the rename loop leaves budget to actually run the operator against the API.
- expected_global_gain: reclaims budget on the `budget_exceeded` cluster (7
  failing tasks) by cutting the tail of identical repeats; generalises because
  the block is command-agnostic (keyed on a normalised hash).
- regression_risk: low. Legitimate iteration changes the command bytes and never
  reaches an identical count of 7; block_threshold=7 sits above the existing
  hard_threshold=5 so the model always gets two escalating text warnings first.
  Worst case a genuinely-needed idempotent re-run (rare) is blocked once — the
  synthetic directive explicitly allows inspecting state with a *different*
  command, so recovery is one step.
- cost_shift: net negative (cheaper). Blocked repeats stop consuming model+tool
  turns; the added synthetic result is a few hundred tokens, far less than the
  10–20 wasted identical turns it replaces.
- Rollback trigger: if the next round shows any previously-passing task flipping
  to fail with a `BLOCKED — this command was NOT executed` synthetic result at
  its decisive step, raise `block_threshold` or revert.
