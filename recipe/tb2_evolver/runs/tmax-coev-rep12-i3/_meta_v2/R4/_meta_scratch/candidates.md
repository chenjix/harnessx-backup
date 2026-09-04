# R4 Candidates

## Candidate C-005 — Budget-aware verifier dep guard

**Three-axis tag**: lens=failure-cluster / lever=Processor / intent=close-structural-zero

**Signal (verified from trajectories)**
Three tasks — `task_000796`, `task_000910`, `task_002108` — end with
`exit_reason=budget_exceeded` (all 80 steps consumed) and `reward=0`. Their
hidden verifier `test_final_state.py` begins with `import requests`; on the
image `requests` is not preinstalled, so pytest aborts at **collection** time
with `ModuleNotFoundError: No module named 'requests'` → structural 0
regardless of final container state.

The repo already ships `VerifierDepGuardProcessor`, which installs `requests`
only on **exit intent** (model ends turn with no tool call). But these three
tasks never reach exit intent — they hit the step budget mid-work — so the
existing guard **never fires**. Confirmed against `messages.json`: no exit-intent
turn appears; the run terminates on budget.

**Body evidence**
- `result.json` for all three: `status`/`exit_reason` = budget_exceeded, reward 0.
- `messages.json`: assistant turns run to the step ceiling; `length_nudge`
  fires repeatedly (task_002108: 12 no-tool turns) but the run never emits a
  clean end_turn. So the existing exit-only guard's trigger condition is never met.
- `test_final_state.py` (verifier) imports `requests`; container lacks it →
  collection error is deterministic.

**Intervention**
New `BudgetAwareVerifierDepGuardProcessor` (order 96, own singleton group, runs
after the exit-intent guard at 95). It fires the SAME idempotent dep-ensure Bash
call on the first no-tool-call turn once `step_id >= max_steps - budget_margin`
(margin=6 → fires from step 74 with the observed max_steps=80), OR on exit
intent, whichever comes first. `max_steps` is read from `StepStartEvent.task`;
falls back to 80. Fires at most once per task. Only injects when the model
produced no tool call of its own that turn, so it never clobbers a real action.
The Bash command is `import`-guarded per module and `|| true`-wrapped: a pure
no-op when the module already imports (the common case).

**Retroactive check (variant: would-fire-and-help)**
On the 3 budget_exceeded+requests tasks: at step ≥74 the model produces
narration-only (no tool call) turns repeatedly, so the guard WOULD fire well
before step 80, injecting the `pip install requests` Bash call, which executes
within budget. The verifier would then find `requests` importable → no
collection error. Whether the task's *content* then passes is a separate
correctness question (these tasks may still fail on value/state), but the
structural 0 floor is removed and they get a fair scored run.

**Why Processor, not Template/Configuration**
- Template: the agent CANNOT know the hidden verifier imports `requests` — this
  is a property of the checker, not the task. Embedding "install requests" in
  the prompt would be task-specific injection and wouldn't generalize.
- Configuration: no knob installs verifier deps.
- Processor is the only lever that can deterministically inject a mechanical
  Bash call the model cannot narrate past.

**Pareto statement**
- `expected_global_gain`: removes a deterministic structural-zero floor for the
  budget-bound subset of verifier-dep-missing tasks (3 observed; generalizes to
  any future task where the verifier imports requests/pyyaml AND the agent
  exhausts budget). Complements, not replaces, the exit-intent guard.
- `regression_risk`: LOW. Fires at most once, only on a no-tool-call turn, only
  in the last ~6 steps or at exit intent. On tasks where deps already present
  it's a no-op Bash call (a few hundred ms, one step). On passing tasks that
  reach exit intent, behavior is unchanged vs. the existing exit-intent guard
  (that one still fires first at order 95; this one is idempotent). Worst case:
  one extra Bash step consumed near budget on a task that was already going to
  fail on budget anyway.
- `cost_shift`: negligible. One extra short Bash call on at most the subset of
  runs that approach budget without exit intent; no-op otherwise.

**predicted_affected**: task_000796, task_000910, task_002108 (structural floor
removed; content-correctness unchanged).

**Rollback trigger**: if R5 shows any previously-passing task regressing to
budget_exceeded or a new error attributable to the injected Bash step, revert
this processor (keep the exit-intent guard).
