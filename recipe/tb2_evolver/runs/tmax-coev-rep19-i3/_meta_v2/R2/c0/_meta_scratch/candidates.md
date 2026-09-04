# Candidates — R2 / c0

Assigned focus: `task_000010_644ab1c2` fails (`exit_reason=budget_exceeded`,
reward 0, 80/80 steps). Diagnosis below, then the shipped candidate.

## Diagnosis of task_000010_644ab1c2

The task requires writing `/home/user/operator.py`. Running
`python3 /home/user/operator.py` puts `/home/user` at `sys.path[0]`, so the
script's own name `operator.py` **shadows the stdlib `operator` module**;
`collections` (imported transitively by almost every stdlib module) does
`from operator import eq`, which resolves to the shadowing file and crashes
the interpreter (`AttributeError: module 'importlib' has no attribute 'util'`,
`Could not import runpy module`). The verifier hits the same crash.

The model discovered a working `importlib.util` invocation from `python3 -c`,
but could not find the correct in-file fix (e.g. strip `sys.path[0]` before
any stdlib import). Whether that specific fix is a **capability gap** is
noted in the memo — no harness change can conjure the exact Python trick.

**BUT** the trajectory's dominant, generalizable harness deficiency is a
*repetition attractor loop*: from ~step 40 onward the model's assistant
content collapses to the same "Actually, let me try using importlib.util..."
narration, re-emitted every turn until the 80-step cap. 30 of the assistant
turns carry the truncation marker; 20 of those ALSO attach a (repeated no-op)
`ls` command. The existing `LengthTruncationRecoveryProcessor` fails to break
it (see C-001).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Supersede `LengthTruncationRecoveryProcessor` with
`DegenerateRepetitionRecoveryProcessor`: trigger recovery on *degeneracy*
(length-truncated no-tool-call turn OR content dominated by self-repetition),
collapse runaway content even when a tool call is attached, keep a decaying
(not hard-reset) escalation counter, and add a terminal "commit best artifact
and stop" nudge.

- Tasks affected (same mechanism, `budget_exceeded` + heavy truncation loop):
  task_000010_644ab1c2, task_000028_7fe033ac, task_000747_424c178b,
  task_001717_a9c46d8d (also lighter: task_000297_01ba10b6,
  task_001857_24daeef3, task_001898_471c0535).
- Signal: `exit_reason=budget_exceeded`, `steps=80/80`; messages contain many
  `[response truncated by harness]` markers AND many
  `Please continue from where you left off` run-loop nudges
  (task_000010: 53 truncation markers / 14 continue nudges;
  task_000747: 15/13; task_000028: 11/13; task_001717: 7/7).
- Verified (Read of task_000010 messages.json):
  - lines 79-96: model emits ~2 KB of repeated "Let me try using
    importlib.util..." then a tool call; the OLD processor's
    `finish_reason=="length" and not event.tool_calls` guard is FALSE here
    (tool call present) → no collapse, counter reset.
  - Python script confirms 20 of 30 truncated assistant turns in task_000010
    carry a tool call, so the old processor treated them as healthy and never
    escalated to its `_NUDGE_REPEAT`.
  - lines 454-540: `[RepeatedCommandBreaker]` counts the same `ls` command up
    to 18× while the narration keeps regenerating — the run reaches 80/80.
- Why Control not Configuration: the old processor's *trigger predicate* is
  wrong (it ignores tool-call-bearing degenerate turns and hard-resets on any
  interleaved turn), not merely mis-tuned. No kwarg on the existing class
  changes the predicate; it needs new detection logic (self-repetition ratio)
  and a decaying counter — a code change, i.e. a Control-lever processor.
- Why Control not Instruction: the model *already receives* an escalating
  "stop repeating" nudge (both the run-loop's passive continue and the
  RepeatedCommandBreaker directive) and ignores it, because the poisoned
  repetitive history keeps re-priming the attractor. A prompt rule cannot
  evict that history; a mechanical `on_after_model` collapse can.
- Retroactive check (A-corrective): PARTIAL.
  - For the *cost/budget* claim: YES — had the runaway content been collapsed
    on every degenerate turn (tool call or not), the transcript would not have
    accumulated the attractor, escalation would have reached the terminal
    "commit best artifact and stop" nudge well before step 80, and the run
    would have committed a best-effort artifact and freed budget across the
    whole `budget_exceeded` loop cluster.
  - For flipping task_000010 to PASS specifically: UNCERTAIN — the underlying
    `operator.py`-shadowing fix is a Python-knowledge gap; breaking the loop
    gives the model more real attempts but does not guarantee it finds the
    fix. Logged as a capability gap in the memo. The candidate is justified on
    the cluster-wide budget-recovery gain, not on a single-task flip.
- expected_global_gain: Reclaims 30-40 wasted steps on the >=4-task
  degenerate-loop `budget_exceeded` cluster, converting dead loop turns into
  either a real fix attempt or a committed best-effort artifact; generalizes
  to any repetition attractor on any task class.
- regression_risk: The self-repetition heuristic could false-positive on a
  legitimately repetitive turn (e.g. a long enumerated list). Bounded: it only
  collapses content > (head+tail+marker) chars AND requires >=55% duplicate
  sentence-fragments over >=900 chars — a genuine loop, not normal prose. The
  collapse keeps head 1200 + tail 600 chars, so a real answer's structure
  survives. Terminal nudge only fires after 5 degenerate turns, so healthy
  runs never see it.
- cost_shift: Down — degenerate loop turns are collapsed (fewer input tokens
  re-fed) and the run is pushed to commit-and-stop instead of grinding to the
  80-step cap; no change on runs that never degenerate.
