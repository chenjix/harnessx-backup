# Candidates — Round 1 (proposal c1)

Assigned focus: `task_000015_89886d8d` (fails, `exit_reason=budget_exceeded`).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a warn-only `OutputStallRecoveryProcessor` that detects consecutive
byte-identical tool results and injects an escalating "same output, no new
information — change approach or finish" note into the tool result.

- Tasks affected (>=2 distinct, same mechanism): task_000015_89886d8d
  (assigned), task_000010_644ab1c2, task_000505_50b5162d, task_000684_1a33ef37,
  task_000958_4bb2b05d, task_001031_a8f0eb37, task_001090_c61c71f2,
  task_001498_df8254c9, task_001652_86e1d185, task_001673_86224c91,
  task_001818_b251e5ea, task_000578_cebe85a5, task_000536_9c16e8ef,
  task_001089_220cc46b, task_000760_76ba653c, task_000818_315382d9,
  task_000933_1f27096a, task_001321_658ce4a8, task_001701_95e3bbcb,
  task_000024_a0664029.
- Signal: `exit_reason=budget_exceeded` at `steps=80` dominates R0 (~35/50
  tasks). Consecutive-identical-tool-result run length (`max_identical_result_run`)
  is >= 7 on ~20 of them and hits the full visible window (33) on ~14. The
  assigned task task_000015 has `max_identical_result_run=33`.
- Verified (Read):
  - task_000015_89886d8d messages.json — every assistant turn is
    `Bash: tesseract /app/routing_schema.png /raw --psm 6 ...` and every tool
    result is byte-identical: `"Tesseract Open Source OCR Engine v4.1.1 with
    Leptonica\nWarning: Invalid resolution 0 dpi. Using 70 instead.\n"`,
    repeated ~33× until budget_exceeded. `migrate.py` / `test_parser.py` were
    never written → final_pytest fails "Migration script not found".
  - task_000024_a0664029 — input command *varies* (input max_run=27) but the
    result stream is identical (`max_identical_result_run=27`); pure
    input-fingerprint detection would miss the variant-command case, output
    detection catches it.
  - task_001818_b251e5ea messages.json — repeated `cargo build --release`
    returning the same error (result run=33).
  - task_000956_7e92337f messages.json — repeated identical `python3 -c
    "import Levenshtein..."` probe (input run=34) with identical output.
- Why Control not Instruction: the agent already *knows* it should "try a
  different approach" — its narration literally says so every turn ("Let me
  try with a different approach") while emitting the identical command. A
  prompt rule cannot fix a runtime perception failure (it cannot see that its
  new output equals its last output); a mechanical `on_after_tool` hook that
  compares result hashes and surfaces the fact IN the tool result is the only
  thing that puts the "no new information" signal where the model reads it on
  its next turn.
- Why Control (warn-only) not Configuration (enable the built-in
  `LoopDetectionProcessor`): that built-in fingerprints tool *inputs* and
  *raises* `LoopDetectedError` to terminate. (1) It misses the variant-command
  / identical-output cases (task_000024, and any task where the agent tweaks
  flags but the error is unchanged). (2) Termination only frees budget; it does
  not give a recovery path. (3) A hard raise risks regressing the one passing
  task with a long identical run — task_000344_e265c898 (reward=1,
  `max_identical_result_run=7`), a read-only reasoning spin *after* the
  reward-earning work already landed — which a warn-only hook leaves passing.
  Output-side + warn-only is a genuinely different, non-colliding mechanism.
- Retroactive check (A-corrective): yes — in task_000015 the first identical
  repeat would trip the warn at run 3 and the hard note at run 6, both ~74
  steps before budget exhaustion, telling the agent its OCR command yields no
  new text and to change approach (e.g. render/resize the image, or hand-transcribe
  from a different tool) and to write the two required scripts. Even if the OCR
  itself still fails, the freed budget + explicit redirect gives the agent the
  room to at least write skeleton `migrate.py`/`test_parser.py` at the required
  paths that the verifier checks for existence.
- expected_global_gain: attacks the dominant `budget_exceeded` cluster
  (~20 tasks with identical-output stalls) by converting silent churn into an
  actionable in-context signal; plausibly flips the subset whose stall was the
  *only* blocker.
- regression_risk: a passing task that legitimately repeats a command with
  identical output >=3× would receive an (ignorable) advisory note appended to
  its tool result — no behavioural forcing, no termination. Only observed such
  case is task_000344 (run=7) which stays passing since we never raise. Note
  text adds a few hundred chars to at most a handful of results.
- cost_shift: net negative (cheaper). Stalled tasks that recover terminate far
  below 80 steps; the appended note is tiny relative to a full 80-step loop.
