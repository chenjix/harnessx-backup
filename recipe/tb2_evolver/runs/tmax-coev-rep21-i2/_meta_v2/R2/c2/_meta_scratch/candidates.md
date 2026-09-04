# Candidates — R2 / c2

Assigned focus: `task_000015_89886d8d` (fails, `exit_reason=loop_detected` @ 20 steps).

## Diagnosis (assigned task)

`task_000015` asks the agent to OCR `/app/routing_schema.png` (tesseract),
then write `/home/user/migrate.py` and a hypothesis-based
`/home/user/test_parser.py`. The 4B model gets stuck on the **first** sub-step:
it calls `tesseract <img> -o raw.txt`, but tesseract 4.1.1 takes the output
base as a *positional* argument, not `-o`, so every call fails with the
identical `read_params_file: Can't open ...` error. It never produces text,
never writes the two required files, and the verifier fails on
`os.path.isfile(migrate_script)` / `test_parser.py`.

Two facts matter:

1. **Not a regression.** Under R0 (no loop detector) this same task would have
   run to `budget_exceeded` @ 80 steps and still failed reward 0 — the model
   cannot recover the tesseract syntax. The R1 loop detector merely terminated
   the doomed run earlier (`loop_detected` @ 20), which is a *cost win*, not the
   cause of the 0 reward.
2. **The specific fix is a model capability gap** — knowing tesseract's
   positional-output CLI. Embedding that would be task-specific domain
   knowledge and is explicitly out of scope (see memo skip line).

But the failure exposes a **general, real harness weakness** worth one small,
low-risk change (below).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add an **output-identity** loop detector (`OutputLoopDetectionProcessor`) that
complements the existing **input-identity** `LoopDetectionProcessor`: it counts
consecutive tool calls returning byte-identical output, warns from repeat 3 with
a concrete "the output is byte-identical, nothing is changing" nudge, and raises
`LoopDetectedError` at repeat 6.

- Tasks affected (same mechanism — output-identity loop the input detector under-counts):
  - `task_000015_89886d8d` — OCR error output repeated **10×** across
    command variations.
  - `task_001832_dd672877` — reverse-engineering a binary; identical
    `echo "Test 0 1 2 3 4: ..."` output repeated from step 15→22 (killed @ 21).
  - Broader cluster: 11/50 R0-config tasks now exit `loop_detected`; several
    thrash on repeated-output exploration before writing the required file.
- Signal: `exit_reason=loop_detected` cluster; tool-output histograms show one
  result string dominating (task_000015: `STDOUT: STDERR: read_params_file:
  Can't open /tmp/ocr/raw.txt...` ×10 after normalising the injected warnings).
  The in-tree input detector (`_compute_fingerprint` over `name + inputs`)
  resets its consecutive-run counter whenever the command text varies, so these
  output-loops slip past it until a *verbatim* burst finally trips threshold=8.
- Verified (Read):
  - `task_000015_89886d8d` msgs 8/16/22/... — assistant narrates "let me try a
    Python script" but re-issues `tesseract .../ -o raw.txt` (as `-c`, then
    here-doc, then with `os.makedirs`); every tool result is the identical
    `read_params_file: Can't open ...`. Counted 10 identical normalised outputs
    (Bash Counter over `.messages.json`).
  - `task_001832_dd672877` steps 15-22 (tool-call dump) — identical
    `echo "Test 0 1 2 3 4: $(/app/recommender 0 1 2 3 4)" ...` repeated; verifier
    then fails `test_pipeline_script_exists_and_executable` (file never written).
- Why Control not Configuration: the existing `LoopDetectionProcessor` cannot be
  re-tuned to catch this — its fingerprint is over *inputs*, and no threshold
  value makes an input-fingerprint see output-identity across varied commands.
  A new hook that fingerprints the *result* is the minimal mechanism; tuning the
  old knobs would only make the input detector more trigger-happy on legitimate
  varied work (regression risk) without catching the actual shape.
- Why Control not Instruction: a prompt rule ("stop when output doesn't change")
  is exactly the guidance the R1 warning already gives, and the trajectory shows
  the 4B model *narrates awareness yet repeats anyway* — the recovery has to be
  mechanical (surface the concrete fact + hard budget reclaim), not another
  prompt nudge the model ignores.
- Retroactive check (A-corrective): **partial / no for pass-flip, yes for the
  harness deficiency.** If output-loop detection had fired on `task_000015`, the
  agent would have terminated ~10 steps sooner (cost win) with a more concrete
  warning — but it would still not know the correct tesseract syntax, so the
  *pass* does not flip (capability gap, logged in memo). The measurable, honest
  gains are: (a) faster budget reclaim on the output-loop cluster, (b) a strictly
  more actionable warning that gives weak models a better-than-generic recovery
  chance before the raise. Shipped as the smallest defensible edit the assigned
  failure supports rather than drifting onto another proposal's territory.

- expected_global_gain: Cost reclamation across the `loop_detected`/output-loop
  cluster (fires at repeat 6 vs the input detector's fragmented path to 8),
  plus a possible small number of recoveries where the concrete output-identity
  warning breaks a loop the generic nudge did not. No claim that task_000015
  flips (capability gap).
- regression_risk: Low. Byte-identical output ≥6× consecutively is a strong
  "nothing changed" signal; legitimate multi-step work advances state each call.
  `min_output_len=12` excludes short recurring outputs (`""`, `done`, `ok`,
  single digits). Passing R0 tasks finish in 9-68 steps with varied outputs and
  no output-repeat histograms, so none should trip. Worst case a genuinely
  useful idempotent check repeated 6× is terminated — rare, and warned 3×
  first. Detector resets on compaction and per-task.
- cost_shift: Net down — terminates hopeless output-loops ~2 repeats earlier
  than the input detector's verbatim path and catches loops it misses entirely;
  adds only a short warning string to affected tool results.
- rollback_trigger: If R3 shows a previously-passing R0/R1 task
  (task_000009/000602/000710/000760/000790/000791/001108/002096/001031/001382/001697/000682/000133)
  regressing to `loop_detected`, raise `threshold` to 8 or `min_output_len`, or
  revert this processor.
