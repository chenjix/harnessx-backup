# Candidates — R1 c3

Assigned focus: `task_000028_7fe033ac` (fails, `budget_exceeded`).

## Diagnosis

task_000028 is stuck in a **period-3 tool-call cycle**: the agent
alternates between `rm -f /tmp/video_backend.sock && touch ... && ls`
→ `/app/server > /app/server.log 2>&1 &` → `sleep 1 && cat log`, repeating
this exact 3-command block ~8 times until `budget_exceeded` at 80 steps /
550s. The model emits the *identical* narration ("The socket file is not
being created... let me try a different approach") on every cycle while
doing the same thing. Root logic bug (touch-ing a regular file where a
UNIX socket must be bound) is a model reasoning gap — NOT the harness's
job. But the **unbounded budget burn on an unrecoverable stall IS a
harness deficiency**: no processor hard-stops a *cyclic* loop.

Why existing mechanisms miss it:
- `LoopDetectionProcessor` (raises on exact-consecutive) is **not in the
  pipeline**, and even if added only raises on period-1 identical repeats;
  a period>1 cycle (a,b,c,a,b,c,…) never trips its consecutive-tail count.
- `CustomEditToolProcessor` fired ONE soft text warning ("modified more
  than 7 times") — the model ignored it and kept looping. Soft nudges are
  provably ineffective for this weak model.

## Cluster confirmation (systemic, not idiosyncratic)

~35 of 49 R0 tasks exit `budget_exceeded` at exactly 80 steps. Body-checked
four representatives — all are no-progress repeat loops of varying period:

- `task_000028` — period-3 cycle (rm/touch → run → cat), ~8 reps.
- `task_000206` — period-2 cycle (`grep …|grep Source-IP` / `grep …`),
  alternating then period-1, >10 reps.
- `task_000010` — period-1 exact loop (identical `python3 -c "import
  socket…"` 16x at the tail).
- `task_000264` — period-1 exact loop (identical `sqlite3 … EXPLAIN QUERY
  PLAN …` 16x).
- `task_000536` — cycles through tesseract language variants then settles
  into a period-1 loop.

All burn the full 80-step / 500-780s budget on one task, starving the rest
of the benchmark of wall-clock.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `CyclicLoopGuard` `MultiHookProcessor` that fingerprints each Bash
command (whitespace-normalized), detects a repeating tail cycle of period
1..4, warns once at 3 repetitions, and raises `LoopDetectedError` at 4
repetitions — converting an unrecoverable full-budget stall into a clean
early `exit_reason=loop_detected`.

- Tasks affected: task_000028 (assigned), task_000206, task_000010,
  task_000264, task_000536 — same mechanism (no-progress repeat loop),
  differing only in cycle period. Fixing one fixes all.
- Signal: `exit_reason=budget_exceeded` + `steps=80` on ~35/49 tasks;
  body shows a period-1..4 tool-call block repeated 8-16x at the tail.
- Verified (Read):
  - task_000028 messages steps span: `rm -f …sock && touch …sock && ls`
    / `/app/server > /app/server.log 2>&1 &` / `sleep 1 && cat
    /app/server.log` repeat as a 3-block ~8 times to budget_exceeded.
  - task_000206 last 16 cmds: `grep "session_id=EVIL_ACTOR_007"
    /home/user/data/web.log | grep "Source-IP:"` alternating with the
    un-piped `grep …` — a period-2 cycle then period-1.
  - task_000010 last 16 cmds: identical `python3 -c "import socket…"` 16x.
  - task_000264 last 16 cmds: identical `sqlite3 … EXPLAIN QUERY PLAN …` 16x.
- Why Control not Configuration: the closest existing knob is the
  unused `LoopDetectionProcessor`, whose exact-consecutive Strategy-1
  cannot detect period>1 cycles at all, and whose name-only Strategy-2 is
  warn-only (never raises). Re-parameterising it does not add a hard-stop
  for cyclic loops — a new mechanism is required. A cross-task guard that
  must fire uniformly on every task is a Control shape, not a per-call
  Action tool.
- Why Control not Instruction: the model already receives a soft text
  warning (`CustomEditToolProcessor`) and ignores it verbatim while
  continuing the loop. Another prompt rule the model can ignore does not
  close the budget-burn gap; only a mechanical hard-stop does.
- Retroactive check (A-corrective): yes — task_000028 would have exited
  `loop_detected` after the 4th cycle (~step 12) instead of running to
  step 80, and the same holds for every cited task. This does not make
  those tasks *pass* (the underlying reasoning bug remains), but the
  benchmark-level gain is budget reclaimed: at 80 steps and 500-780s per
  stalled task, an early exit frees a large fraction of wall-clock/step
  budget for tasks the agent can actually solve. For any task where the
  loop was a symptom of an *otherwise recoverable* state, the injected
  warning + forced re-plan gives a real second chance the current
  pipeline never offers.

- expected_global_gain: dominant R0 failure cluster is `budget_exceeded`
  @80 steps (~35/49). Early termination of unrecoverable cyclic stalls
  reclaims wall-clock/step budget for solvable tasks and gives the agent a
  forced-replan warning before the hard stop — plausibly flips the subset
  of stalls that were recoverable, and strictly improves cost on the rest.
- regression_risk: LOW. A legitimate task would have to issue the *same*
  1-4 command block 4 times in a row with no interleaving to trip the
  raise. Whitespace-normalized fingerprints + a 24-slot window +
  raise_reps=4 keep the false-positive surface small; interleaving any
  distinct command breaks the tail run. Worst case is an early exit on a
  task that was going to fail anyway (already reward=0). No
  currently-passing task in R0 exhibits a >=4x repeated cycle (passing
  tasks finish in 9-28 steps with `exit_reason=done`).
- cost_shift: strongly NEGATIVE (cheaper). Stalled tasks currently run 80
  steps; hard-stopping at ~step 12-16 cuts their token/wall-clock cost by
  roughly 4-5x. No cost added to healthy tasks (guard only reads its own
  fingerprint window; no model calls).

Rollback trigger: if R1 pass_rate drops vs R0, or any R0-passing task
(task_000587, task_000748, task_000912, task_000344, task_001781) flips to
`loop_detected`, revert C-001.
