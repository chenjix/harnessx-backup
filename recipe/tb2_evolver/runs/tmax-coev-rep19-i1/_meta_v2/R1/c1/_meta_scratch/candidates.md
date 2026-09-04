# Candidates — Round 1 (c1)

Assigned focus: `task_000015_89886d8d` fails (reward 0, accuracy 0.0000).

## Diagnosis of the assigned task

The task requires reading exact schema field-name mappings from an image
(`/app/routing_schema.png`) via OCR, then producing JSONL whose keys match
a hidden golden mapping exactly. Tesseract returned garbled output at the
image's low DPI. The agent ran essentially the same OCR-with-preprocessing
command ~7 times (steps 1–33), got the same garbled text each time, then
**abandoned the authoritative source and guessed field names from the
sample URLs** (`product_id`, `session_token`, `department`, `sort_order`,
...). Those guessed keys don't match golden → accuracy 0.0.

Root shape: the agent repeated an identical failing command until it gave
up, then silently substituted a fabricated value for data it was required
to read. This is a **harness deficiency** (no guard breaks a byte-identical
command loop; nothing warns against fabricating unreadable required data),
NOT a model knowledge gap — the model even *narrates* "I've been stuck in a
loop" but keeps repeating.

## Systemic evidence (the loop shape recurs across tasks)

Byte-identical / near-identical repeated Bash commands in failing tasks:

- `task_001321_658ce4a8` (budget_exceeded, reward 0): **33/33 tool calls
  were the byte-identical `cat > /home/user/extractor.c << EOF` heredoc**
  (verified: `unique cmds = 1`). Agent narrates "I've been stuck in a loop"
  repeatedly yet re-issues the same bytes; burns all 80 steps.
- `task_001818_b251e5ea` (budget_exceeded, reward 0): 7× identical
  `python3 -c "import re; text=open('/app/transcription.txt')..."`.
- `task_001032_1adaccb9` (reward 0): 8× identical `rm -rf ...`, 6× identical
  `.cpp` heredoc rewrite.
- `task_000264_ab8c7253` (reward 0): 8× identical `cat > /tmp/test_query.sql`
  heredoc.
- `task_000015_89886d8d` (assigned, reward 0): ~7× the same OCR pipeline.

The existing `CustomEditToolProcessor` fires on these but is ineffective:
in `task_001321` it mis-parses the heredoc and prints `File 12) modified`
/ `File #include modified` (wrong "filename"), and its advisory is a soft
line the model acknowledges ("the EditDetection system is...") yet ignores.
It counts *file edits*, not *identical commands*, so it can't distinguish a
material rewrite from an exact repeat, and it never fires on non-write
loops (OCR, curl, compile).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatedCommandBreaker` processor that hashes each normalised Bash
command and, on the 3rd identical run, appends a directive to STOP
repeating and change approach; on the 5th+, escalates to a hard directive
that (a) forbids re-issuing the command, (b) warns against silently
substituting a fabricated value for required-but-unreadable data, and
(c) enumerates generic alternative strategies.

- Tasks affected (same mechanism, distinct inputs): task_000015_89886d8d
  (OCR loop → fabricate), task_001321_658ce4a8 (33× identical heredoc),
  task_001818_b251e5ea (7× identical python), task_001032_1adaccb9
  (8× identical rm + 6× identical cpp), task_000264_ab8c7253 (8× identical
  SQL heredoc).
- Signal: `exit_reason=budget_exceeded` on 001321/001818; on 000015 the
  agent exits `done` after abandoning OCR. Verified by counting tool-call
  command strings per task: e.g. task_001321 has 33 commands, 1 unique.
- Verified (body):
  - task_001321 step 4/6/9/11/13 assistant content = "I've been stuck in a
    loop... completely different approach" while the tool_call is the
    byte-identical `cat > extractor.c` (all 33 identical).
  - task_000015 steps 1–33: repeated `tesseract ... enhance ...` producing
    the same garbled "Legacy toV2 Schema Mapping ..." text; step 37 writes
    migrate.py with guessed keys (`product_id`, `department`, ...).
- Why Control not Instruction: the model already *knows* it is looping (it
  says so) — a prompt rule telling it "don't loop" is what the existing
  soft `[EditDetection]` advisory already is, and it is ignored. The gap is
  a mechanical guard that fires deterministically on the identical-command
  signal and escalates; a static template rule cannot count runtime
  repeats. Not Configuration: `CustomEditToolProcessor`'s knob (`threshold`)
  can't help — it measures the wrong thing (file edits parsed from
  redirects, mis-parsed on heredocs) and only covers write commands, so no
  retune closes OCR/compile/curl loops.
- Why not Action: no new capability is missing — Bash suffices; the failure
  is behavioural (repetition + fabrication), which is a control-loop guard.
- Retroactive check (A-corrective): yes — on task_001321 a hard directive
  at the 5th identical `cat` (step ~10 of 80) forbidding the repeat would
  have forced the agent off the dead heredoc with ~70 steps of budget left
  to actually run/debug the program. On task_000015 a directive at the 3rd
  garbled OCR that also says "do not fabricate required data" would have
  pushed toward a different extraction attempt rather than guessing keys.
  The intervention doesn't hand over the answer — it removes the loop and
  the fabrication shortcut, which are the proximate blockers.
- expected_global_gain: targets the largest failing cluster this round —
  every `budget_exceeded` failure inspected was an identical-command loop
  (001321, 001818, and contributes to 001032/000264/000015). Breaking the
  loop returns spent budget to productive work and blocks the
  fabricate-a-guess shortcut.
- regression_risk: LOW. The processor only *appends text to the tool
  result* (same contract-safe mechanism as the shipped
  `CustomEditToolProcessor`) — it never blocks execution, removes messages,
  or inserts messages. Legitimate iterative work re-runs *different*
  commands (a real rewrite changes bytes), so it will not fire on healthy
  trajectories; only byte-identical repeats trip it. Thresholds (3 warn / 5
  hard) are conservative — a passing task that legitimately re-runs the
  exact same check 2× is untouched.
- cost_shift: net NEUTRAL-to-DOWN. On loop tasks it should *reduce* tokens
  by ending 80-step thrash early. On healthy tasks it adds nothing (never
  fires). Worst case: a few hundred chars appended to one tool result on a
  borderline task.
- Rollback trigger: if next round shows a previously-passing task flipped to
  fail with a `[RepeatedCommandBreaker]` marker at its decisive step (i.e.
  the guard interrupted legitimate iteration), raise `warn_threshold` /
  `hard_threshold` or revert.
