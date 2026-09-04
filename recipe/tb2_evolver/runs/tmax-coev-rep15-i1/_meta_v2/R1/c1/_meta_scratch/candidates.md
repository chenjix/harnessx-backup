# Candidates — Round 1 (focus: task_000015_89886d8d)

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add a general "image/OCR robustness" strategy section to the system prompt:
when a task requires extracting text from an image and the first OCR pass looks
garbled, upscale the image several times, set an explicit DPI, and try multiple
tesseract page-segmentation (`--psm`) modes, then reconcile the readings — rather
than accepting the first noisy pass or applying ad-hoc contrast/threshold tricks.

- Tasks affected: task_000015_89886d8d, task_000505_50b5162d
  (both OCR-from-image tasks that FAILED because dense text was mis-read).
  Same mechanism also present in passing OCR tasks (task_000536_9c16e8ef,
  task_001652_86e1d185, task_001264_9f4ca84a) — those passed only because the
  target was a single short token (an `EMP-…` id / a key) that survived noisy OCR.
- Signal: both failing tasks call `tesseract <img> stdout` once (or with random
  contrast/threshold enhancement) and get garbled multi-line output; the agent
  then *infers* the schema/key from the noise. task_000015 final_pytest accuracy
  0.3389 (≈1/3 — one of three route key-sets guessed right, two wrong).
  task_000505 extracts a corrupted SSH key (`+ |J9tY +X07yG` artifacts).
- Verified (Read of messages.json):
  - task_000015 step 1: `tesseract /app/routing_schema.png stdout` → garbled
    `atalogyitem <item _id>…ordert *<sort order»`. Steps 9-31: agent tries
    grayscale+contrast(2.0/3.0), autocontrast, SHARPEN, binary threshold; step 21
    threshold produces pure garbage (`eet / Dee ee ee…`). Never upscales the
    800x400 image or varies `--psm`/`--dpi`. Step 25: "OCR is still garbled …
    make reasonable inferences", then hardcodes guessed keys (`sort_order`,
    `traffic_source`, `ui_theme`).
  - task_000505 step ~2: single `tesseract /app/evidence.png stdout` pass;
    the resulting SSH key string carries OCR artifacts and the detection script
    then greps for the wrong key.
  - Contrast — passing task_000536 step: `tesseract … | grep -oE 'EMP-[0-9]+'`;
    the short regex-recoverable target tolerates OCR noise, so no robustness
    technique was needed.
  - I independently confirmed the image is a clean high-contrast target
    (light bg, ~2132 dark text px of 320000; extrema 1..255) — i.e. legible,
    the failure is low-res single-pass OCR, exactly what upscale+psm fixes.
- Why Instruction not Action: the agent already HAS the capability — `tesseract`
  and `PIL` are installed and it invokes them successfully; a new tool would
  duplicate what Bash already does. The gap is *method knowledge* (when the first
  OCR is noisy, upscale + vary `--psm` instead of guessing) — a general strategy,
  not a missing action. Not Control either: there is no tool-return to normalise
  in the harness pipeline (OCR runs inside the agent's own Bash sub-process, whose
  intermediate output the processor layer never sees).
- Why Instruction not domain-injection: the guidance is a generic image-OCR
  recipe (upscale, dpi, psm sweep, cross-check) that helps any unseen
  image-extraction task; it contains no task-specific keys, routes, ids, or
  file paths from the training tasks. Passes the generalization test.
- Retroactive check (A-corrective): yes — if the agent had upscaled
  routing_schema.png ~3-4x and tried `--psm 6/4` with `--dpi 300`, the dense
  schema text (clean, high-contrast) would have been read accurately enough to
  extract the exact JSON key names, flipping accuracy above the 0.98 gate.
  Likewise task_000505 would have recovered the exact SSH key.
- expected_global_gain: flips the OCR-dense-text failure cluster (>=2 tasks:
  000015, 000505); generalizes to any future image-extraction task where the
  target is multi-line/dense rather than a single grep-able token.
- regression_risk: low. The passing OCR tasks (000536, 001264, 001652) already
  succeed with a single pass on short targets; the new guidance is conditional
  ("if the first pass looks garbled"), so it adds a few extra Bash calls only
  when OCR is actually noisy — it does not change the short-target happy path.
  Non-OCR tasks are untouched (the section is scoped to image tasks).
- cost_shift: small increase only on image-OCR tasks that hit noisy output
  (a handful of extra tesseract passes ~seconds each). Negligible token growth
  from a ~15-line prompt addition across all tasks.

## Candidate C-002
[lens: failure | lever: instruction | intent: corrective]

Add a general "verify before you finish" discipline to the system prompt: when a
task says a hidden/automated suite will run your script/output on a larger or
edge-case dataset, self-check by re-reading the task's own requirements and
running your artifact on every provided sample (and hand-built edge cases like
URL-encoded / empty / unusual values) before declaring done — do not stop at the
happy-path sample.

- Tasks affected: task_000015_89886d8d, task_000505_50b5162d
- Signal: task_000015 ran migrate.py only on the 4-line sample (all clean,
  simple values) and stopped; the hidden suite exercised 2000 URL-encoded
  edge-case URLs where the guessed schema + naive decoding failed. task_000505
  tested only a self-made positive sample.
- Verified (Read): task_000015 step 39 runs `migrate.py sample_urls.txt` →
  3 clean lines, agent declares success; never tests URL-encoded/edge values
  despite the task explicitly warning "handles URL decoding correctly" and
  "massive hidden dataset of edge-case URLs". No robustness self-check.
- Why Instruction not Control: the check the agent must run (re-read task,
  exercise edge inputs) is agent-authored reasoning about the *task's own*
  correctness criteria — a mechanical processor can't synthesize task-specific
  edge inputs or know the required output schema. A CustomSelfVerifyProcessor
  already exists in the pipeline (it fired at step 33) but only nudges
  existence-of-files; it can't judge accuracy. The missing piece is a knowledge
  rule about *what* to verify.
- Why not domain-injection: the rule is generic ("run on all provided samples
  + construct edge cases from the stated constraints before finishing") with no
  task literals; helps any unseen task with a hidden verification suite.
- Retroactive check (A-corrective): partial-yes — pairs with C-001. On its own
  it makes the agent notice its output is wrong on edge cases; combined with
  C-001 (accurate schema) it produces a correct, edge-case-robust script.
  Marked complementary, not standalone-sufficient.
- expected_global_gain: reduces "happy-path only" premature-done failures across
  tasks whose prompt announces a hidden/edge-case suite (recurring TB2 shape).
- regression_risk: low; adds verification steps, not behavioural changes to
  already-passing tasks. Slight step-count increase.
- cost_shift: minor step/token increase on tasks that adopt the extra self-check.
