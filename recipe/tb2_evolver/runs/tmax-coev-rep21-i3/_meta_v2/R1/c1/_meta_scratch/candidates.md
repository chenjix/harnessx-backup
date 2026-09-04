# Candidates — Round 1 (tmax-coev-rep21-i3)

Assigned focus: `task_000015_89886d8d` (software_engineering) fails with
`exit_reason=budget_exceeded`, reward 0. Required deliverables
`/home/user/migrate.py` and `/home/user/test_parser.py` were **never
written** — the agent spent all 80 steps re-issuing one OCR/PIL inspection
command.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatedCommandCircuitBreaker` processor that intercepts a Bash
command issued unchanged `repeat_threshold` (=3) times in a row, refuses to
execute it (`approved=False`), and injects an escalating redirect as its
synthetic result — mechanically breaking identical-command doom loops.

- Tasks affected (>=2 distinct, same mechanism):
  - `task_000015_89886d8d` (assigned; 16 consecutive identical Bash calls)
  - `task_001207_44e97fe1` (29 consecutive identical)
  - `task_001717_a9c46d8d` (26), `task_001832_dd672877` (26),
    `task_000010_644ab1c2` (23), `task_001447_8bde38ef` (21),
    `task_001902_29d93022` (16), `task_001857_24daeef3` (15),
    `task_001547_8cde5da2` (14)
  - **19 of 50 R0 tasks** hit >=4 consecutive identical Bash calls; only 2
    of those 19 passed (and those two repeated a command *after* the answer
    was already produced — see regression_risk).
- Signal: `exit_reason=budget_exceeded` + a long run of byte-identical
  `assistant.tool_calls[].function.arguments`. Measured directly from the
  `.messages.json` logs (script in `_meta_scratch/analyze_loops.py`).
- Verified (Read body, task_000015):
  - Steps repeatedly emit the identical call
    `python3 ... Image.open('/app/routing_schema.png') ... img.save('/tmp/routing_schema.png')`
    and receive the identical tool result `Image size: (800, 400), mode: RGB`.
  - Assistant narration verbatim, every turn: *"I see - the system is
    detecting that I'm repeatedly running the same command. Let me try a
    fundamentally different approach"* — then re-issues the SAME command.
  - The existing soft nudge fired
    (`[EditDetection] File /tmp/read_image.py has been modified more than 7
    times ... step back`) and the agent explicitly acknowledged it
    (*"the system has detected that I'm over-editing ... told me to step
    back"*) yet repeated the identical command on the very next turn.
    → proves a text warning appended to an otherwise-identical tool result
    is insufficient; the observation itself must change.
  - Neither `/home/user/migrate.py` nor `/home/user/test_parser.py` was ever
    created (final `pytest` fails: *"Migration script not found"*).
- Why Control not Instruction: the model already *knows* it is looping — it
  says so on every turn — and the prompt-level guards (EditDetection,
  PostCompaction) were read and ignored. Adding more prompt text patches
  nothing; a *mechanism* that changes the tool observation (blocks the call
  and returns a different, escalating result) is required. Not Configuration,
  because no existing knob detects consecutive-identical *commands* — the
  `CustomEditToolProcessor` keys on per-file edit counts (soft warning only)
  and only for write commands, so a read-only inspection loop like
  task_000015's never trips it into a hard stop.
- Retroactive check (A-corrective): yes — if the identical OCR-inspection
  command had been refused after 3 repeats with a redirect to "produce the
  required output file(s)", the ~13 wasted repeat-turns (and the same pattern
  across the other cited tasks) would have been reclaimed for actually
  writing `migrate.py`/`test_parser.py`. The loop is the proximate blocker:
  the agent had already learned the only fact the command yields (image is
  800x400 RGB) on turn 1.
- expected_global_gain: reclaims the budget wasted by the loop on the ~17
  failing tasks that stall this way, letting them reach the deliverable-
  writing phase. Even a partial conversion of these is a large pass-rate
  move given R0 pass_rate is only 0.16.
- regression_risk: LOW. The two passing tasks with high repeat counts
  (`task_000870_7cbd963f` re-ran the same correct SQL 26×; `task_000316`
  echoed `"Task completed successfully."` 21×) repeated *after* their work
  was done — blocking those repeats reclaims budget and cannot un-write a
  correct output file. The guard only fires on *consecutive identical*
  commands and resets on any materially different command, so legitimate
  interleaved polling of changing state is unaffected. `reset_after_block`
  gives the agent a clean slate after each block so a genuinely-new command
  is never collaterally refused.
- cost_shift: NEGATIVE (cheaper). Refused commands are not executed and the
  loop is cut short, reducing per-task steps/tokens on the ~19 looping tasks.

Lever/why-not summary: Control (new `MultiHookProcessor`), not Instruction
(prompt nudges already ignored) and not Configuration (no existing knob
detects consecutive-identical commands or issues a hard block).
