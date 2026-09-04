# Candidates — R2 / c1

Assigned focus: `task_000011_d089ef35` (scientific_computing) fails.

## Diagnosis

The task is a fully-specified computation: the C mesh server must extract a
2x2 quadrant, nearest-neighbor refine to 4x4, and return MSE vs a hardcoded
reference. All inputs, the formula (`Sum((refined_i - ref_i)^2)/16`), the
reference vectors, and the exact output format are given in the prompt. I
recomputed the four expected MSEs from the spec independently:
quadrant 0=8.25, 1=17.25, 2=68.25, 3=93.25 — exactly the grader's expected
values. The agent's server returned 8.25, 12.75, 38.25, 50.75: correct only
for the trivial quadrant 0, wrong for 1/2/3 (a bug in its C
extraction/refine/MSE loop).

Crucially, this is NOT a math/capability gap. In-trajectory the agent
computed quadrant 1 by hand and summed to 276 → 17.25 (the correct value),
but its *server* returned 12.75. The agent explicitly flagged the
contradiction — "But the server returned 12.75. Let me check if there's a
bug..." — twice, then abandoned the investigation and exited declaring
success. It treated the running (buggy) program as ground truth over its own
correct derivation. The one-shot `CustomSelfVerifyProcessor` fired but the
agent satisfied it superficially: it re-read the task, re-listed files, and
re-tested ONLY quadrant 0 (the passing case), never reconciling the
discrepancy it had already surfaced.

The same mechanism recurs on `task_000587_9862bb19` (data_science / C++
recommender debug): at step 41 the agent hand-derived Item 104 Bayesian mean
= 2.0 but its program output 2.2500 — "wait, the output shows 2.2500. Let
m[e]..." — and then across steps 43/45/49/51 declared the task complete
without ever resolving the mismatch.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add a verification-discipline rule to the system prompt: when a task fully
specifies a computation, independently derive the expected result by hand for
at least one non-trivial case and compare it to the program's output; if the
independent derivation disagrees with the program, treat the program as buggy
and fix it before finishing — never exit on a mismatch already noticed.

- Tasks affected: task_000011_d089ef35, task_000587_9862bb19
- Signal: `exit_reason=done` with `reward=0`; final_pytest shows correct
  trivial case but wrong non-trivial cases; trajectory body shows the agent's
  own hand-computation contradicting its program output, flagged then
  abandoned.
- Verified (Read):
  - task_000011 messages step 17/23: "MSE = 286/16 = 17.875 ... But the
    server returned 12.75. Let me check if there's a bug..." (its 276-sum
    actually gives the correct 17.25); step 33-37: responded to the
    self-verify checklist by re-reading the task and re-testing only quadrant
    0, then exited. Server outputs 8.25/12.75/38.25/50.75 vs required
    8.25/17.25/68.25/93.25.
  - task_000587 step 41: "Item 104 ... = 8/4 = 2.0... wait, the output shows
    2.2500. Let m[e]" — contradiction surfaced; steps 43/45/49/51 declare
    "task complete" with no reconciliation.
- Why Instruction not Control: a mechanical processor cannot detect this — it
  would have to parse free-form prose reasoning to know a numeric
  contradiction was surfaced, and the discrepancy values are task-specific.
  The existing `CustomSelfVerifyProcessor` already injects a generic
  mechanical checklist and the agent ignored its intent (re-tested only the
  trivial case). The missing thing is a *policy the agent applies*: "your
  independent check beats your program's output when they conflict." That is
  knowledge/discipline, not a missing hook — Instruction, not Control.
- Why Instruction not Configuration: no existing processor knob encodes a
  recompute-and-reconcile discipline; there is nothing to tune.
- Retroactive check (A-corrective): yes (plausible). In both tasks the
  agent's independent derivation was already correct (17.25; 2.0) and it had
  already surfaced the contradiction. A rule elevating the manual derivation
  over the program's output would have blocked exit and forced a debug pass on
  the specified non-trivial cases — the correct target value was in hand in
  both. Not guaranteed (depends on the agent then locating the C/C++ bug), but
  the decisive error was exiting on a known mismatch, which the rule directly
  targets.
- expected_global_gain: closes the "correct-on-trivial-case, wrong-on-real-
  case, self-detected-but-unreconciled" failure shape across
  fully-specified-computation tasks (scientific_computing / data_science /
  debugging clusters — ~10 failing tasks touch precise numeric outputs).
- regression_risk: low. The rule is additive guidance that only bites when a
  task specifies a computation; it does not change tool behavior or the
  pipeline. Minor risk of a few extra Bash steps (hand-check + a debug
  iteration) on tasks that were already passing, but those already end after
  the self-verify checklist. No path to corrupting a passing task's outputs.
- cost_shift: +1-3 short Bash steps on computation tasks where the agent now
  runs an independent check / one debug iteration; negligible on tasks with
  no specified numeric output. Net small token increase, concentrated on tasks
  currently scoring 0.
