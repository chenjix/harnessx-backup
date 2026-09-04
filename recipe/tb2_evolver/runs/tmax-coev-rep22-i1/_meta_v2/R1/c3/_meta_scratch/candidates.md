# Candidates — Round 1 (c3)

Assigned focus: `task_000118_3043e92d` fails (reward=0, 59 steps,
finished=no_tool_calls). Verifier: peak log-dir size 209,715,200 B
(200 MB) vs threshold 45 MB — the monitor script never truncated.

## Root-cause diagnosis (assigned task)

The graded artifact is the final `deployment_monitor.py`; the verifier
starts the monitor itself (`subprocess.Popen(["python3", monitor_path])`),
so the agent does NOT need to keep a live background process. But the
agent never understood that. From the summary onward, the entire visible
trajectory (~28 tool calls) is a **non-converging diagnostic loop**: the
agent runs `python3 monitor.py &`, immediately runs `ps aux | grep ...`,
sees `[python3] <defunct>` zombies, concludes "the monitor is not showing
up in the process list", and repeats — with slightly-varied Bash args each
time. It re-narrates the *same conclusion in the same words* ~15 times,
twice hitting the output-token limit mid-repetition, and finishes with
`no_tool_calls` having never (a) realised the background job persists for
the verifier, nor (b) run the actual end-to-end deployment test that would
have exposed the script's real logic bug (its `while True:` breaks
immediately when no workers are running yet, during the verifier's initial
0.5 s window). It burned all 59 steps in the loop instead of fixing the
script.

This is a **harness deficiency**, not (only) a model capability gap: the
existing guards do not stop this loop shape.
- `LengthTruncationRecoveryProcessor` only fires on `finish_reason=length`
  (it fired twice here but didn't break the loop).
- `LoopDetectionProcessor` is not even in the R0 config; and even if added,
  its exact strategy needs *consecutive byte-identical* tool inputs (these
  vary) and its name-only strategy is warn-only (never terminates).

So the loop runs unbounded until budget/step exhaustion.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `SemanticRepetitionBreaker` processor that detects near-duplicate
assistant *narration* across recent turns (Jaccard token-overlap on
normalized content), injects a corrective redirect nudge at the warn
threshold, and raises `LoopDetectedError` at the raise threshold so the
run exits cleanly (`exit_reason=loop_detected`, last-good filesystem
recovered) instead of grinding to `budget_exceeded`/`max_steps`.

- Tasks affected (failing, same non-converging-loop mechanism, distinct
  inputs): `task_000118_3043e92d` (assigned; process-monitoring),
  `task_000958_4bb2b05d` (server crash-loop), `task_001032_1adaccb9`
  (tar/hex parsing), `task_001321_658ce4a8` (persistent error re-diagnose),
  `task_000015_89886d8d` (URL-decoder test loop), `task_001031_a8f0eb37`
  (script re-run loop).
- Signal: failing cluster shows `finished ∈ {no_tool_calls,
  budget_exceeded}` at high step counts (57/58/59 and 80) while passing
  tasks almost never exceed ~28 steps; assistant `content` near-duplicate
  counts (window=8) reach 5-14 repeats of the same sentence.
- Verified (Read of message bodies):
  - `task_000118` msgs 55/57/59/61/63/65/67/69 repeat verbatim: "The
    monitor script is not showing up in the process list. Let me try..."
    and "I see there are zombie processes... let me clean them up"; two
    turns truncate at the token limit mid-repetition; ends no_tool_calls.
  - `task_001321` — 14 verbatim repeats of "The error is still appearing.
    This is strange..." plus "I've been stuck in a loop. Let me try a
    fundamentally different approach" (existing nudge fired, ignored).
  - `task_000015` — 6 repeats of "The tests are failing because the URL
    decoder..." + 5 of "The user is telling me to stop the repetition..."
    (nudge fired, agent still could not escape).
  - `task_000958` — 4 repeats of "I've been stuck in a loop. Let me try a
    fundamentally different approach"; `task_001032` — 7 repeats of a
    hex-dump narration; `task_001031` — 5 repeats of "Now let me run the
    script again:".
- Offline replay of the detector over R0 trajectories (window=8, sim=0.80,
  warn=3, raise=4): **5/6 failing loop-tasks hard-raise** (turns 10-29,
  well before exhaustion), **0 passing tasks raise**, and only **1 passing
  task (`task_000578`) gets a single warn** (one nudge, no termination).
  `task_000118` gets 4 warns (repeated early nudges) but no raise — the
  corrective redirect is its intervention.
- Why Control not Instruction: the failure is mechanical (unbounded loop
  consuming budget), not a missing prompt rule — the existing "you are
  stuck in a loop" nudges already fire in-band and are demonstrably ignored
  (`task_001321`, `task_000015`). A per-turn cross-task guard that measures
  progress and can hard-terminate is a shape the system prompt cannot
  express. Why Control not Configuration: `LoopDetectionProcessor` keys on
  tool-arg fingerprints, which vary here; no knob on an existing processor
  catches near-duplicate *narration*, so a new hook is required.
- Retroactive check (A-corrective): partial-yes. For the 5 raise-tasks:
  terminating at turn 10-29 stops budget waste and recovers the last-good
  filesystem — a net-positive fail-fast even where the artifact was already
  wrong (no worse than the current 0), and a real save on any task where a
  correct artifact was already on disk before the loop began. For
  `task_000118`: the early corrective nudge ("background jobs persist for
  the verifier; verify the file and finish OR change the logic, don't keep
  checking process state") directly targets the misconception driving the
  loop, giving the agent budget and a prompt to find/fix the timing bug it
  never reached. The nudge is a redirect, not a solution — no task-specific
  code is injected.
- expected_global_gain: reclaims 40-70 wasted steps across the 6 high-step
  loop failures; plausibly flips 1-3 where the real work was reachable once
  the loop is broken, and prevents whole-round budget starvation from a
  single stuck task.
- regression_risk: LOW. Thresholds are conservative (observed loops repeat
  5-14×; raise at 4 near-dupes in a window of 8 with 0.80 Jaccard). Offline
  replay raised on 0/30 passing tasks and warned on 1. Worst case on a
  false-positive raise: a task that was going to fail anyway exits a few
  turns sooner (same reward 0). The one warned passing task gets a single
  extra user message — negligible.
- cost_shift: NET NEGATIVE (cheaper). Early termination of doomed loops cuts
  the most expensive trajectories (57-80 steps) to ~10-29 steps; the nudge
  adds at most one short message on a handful of tasks.
