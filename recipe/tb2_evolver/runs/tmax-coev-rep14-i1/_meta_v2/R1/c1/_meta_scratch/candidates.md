# Candidates — Round 1 (focus: task_000015_89886d8d)

## Assigned-focus diagnosis

`task_000015_89886d8d` (software_engineering): the task requires OCR of
`/app/routing_schema.png` to extract URL→JSON routing rules. The agent's OCR
came back garbled (visible only as the pre-compaction summary), so it
*inferred* the schema from `sample_urls.txt` and produced a parser missing the
`department` key that the hidden verifier expects (`KeyError: 'department'`).

That root cause is a **model capability gap** (robust OCR of a low-resolution
schema image) — NOT a harness deficiency. Per SOUL rules, embedding the OCR
recipe or the field mapping into the prompt would be task-specific memorisation
and would not generalise. So the pass-flip for task_15 is out of scope for a
harness fix.

BUT the trajectory exposes a genuine, **recurring harness deficiency** on the
way to that failure, and that is what this round ships.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a cumulative length-truncation terminator: after N total
`finish_reason=length`+no-tool-call turns in a task, raise `LoopDetectedError`
to abort the runaway loop and recover the best output, instead of nudging
indefinitely.

- Tasks affected (same mechanism, distinct inputs):
  - task_000015_89886d8d — 15 length-truncation nudges, `finished=no_tool_calls`, elapsed **1022s**
  - task_001032_1adaccb9 — 16 nudges, `finished=no_tool_calls`, elapsed **968s**
  - task_000958_4bb2b05d — 8 nudges, `finished=budget_exceeded`, elapsed **760s**
  - (contrast, PASSING) task_001498_df8254c9 — 5 nudges, then recovered, reward=1
- Signal: `user` messages equal to *"Your previous response was cut off by the
  token limit. Please continue from where you left off."* recur ≥8× on the
  failing tasks; median task elapsed is ~150s but these run 760–1022s.
  `LengthTruncationRecoveryProcessor` uses a *consecutive* counter that resets
  on any tool call, so it never fires on this interspersed thrash pattern.
- Verified (Read of messages.json):
  - task_15 msgs 71,73,75,77 are near-identical no-tool-call narration
    ("The user is right - I've been stuck in a loop... Wait, I think I see the
    issue now...") each preceded by the passive continue nudge (msgs 72,74,76);
    task ends `no_tool_calls` after ~500s of tail loop.
  - task_1032 msgs 46,48,55,59 repeat "The user is right - I've been stuck in a
    loop..." / "I see the issue now. The tar file has some unusual entries..."
    — identical shape, different task input.
  - task_1498 (passing) hit the same truncation 5× early then recovered — sets
    the safe lower bound for the cap.
- Why Control not Configuration: the existing recovery processor's knob
  (`repeat_threshold`) counts *consecutive* truncations and resets on any tool
  call, so no re-parameterisation of it catches the interspersed relapse
  pattern seen here. A new hook tracking the *cumulative* count is required —
  that's mechanical loop-guard logic that must fire uniformly across tasks,
  i.e. Control, not a knob tweak.
- Why Control not Instruction: the model already receives an escalating
  corrective nudge ("issue ONE Bash command", "STOP... you are repeating
  yourself") and ignores it — a stronger prompt rule demonstrably does not
  break the loop. The fix has to be a mechanical terminator, not more prose.
- Retroactive check (A-corrective): PARTIAL/honest. This does NOT flip
  task_15/1032/958 to *pass* — those hit real reasoning/OCR walls. It DOES
  convert ~500s of wasted tail-loop compute per task into an early clean
  `loop_detected` exit with best-output recovery. The corrective target here is
  the **wasted-budget failure mode**, not the task correctness. Cap=10 is set
  2× above the passing-recovery observation (5) so no recovering task is cut.
- expected_global_gain: reclaims 300–500s of dead wall-clock on each looping
  task (≥3 observed this round) and frees per-run budget/step headroom for the
  rest of the batch; generalises to any future task that trips the same
  narration→truncation→nudge cycle.
- regression_risk: LOW. Only fires after 10 cumulative truncations — a task
  that recovers (task_1498 at 5) keeps its full runway with 2× margin. Worst
  case a borderline task that would have recovered at the 11th+ truncation is
  cut, but no such case exists in the batch; the cap is deliberately generous.
- cost_shift: NET DOWN. Each aborted loop saves the tokens/time of ~5-11
  additional 4096-token truncated generations. No added cost on the ~46 tasks
  that never trip the guard (the counter simply stays at 0).
