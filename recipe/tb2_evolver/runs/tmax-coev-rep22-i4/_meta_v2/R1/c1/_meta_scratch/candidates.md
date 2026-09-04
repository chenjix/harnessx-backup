# Candidates

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add a general OCR-quality-recovery + exact-transcription strategy to the
system prompt: when `tesseract` reports invalid/low DPI or returns garbled
text, upscale/rescale the image and try multiple `--psm` modes before
trusting the output, and treat OCR-extracted exact identifiers (keys,
tokens, field names, constants) as literal — never "normalize" them into
plausible-sounding substitutes.

- Tasks affected (assigned focus + same-mechanism cluster):
  task_000015_89886d8d, task_000505_50b5162d, task_001013_7f3bf12e,
  task_001877_bd2513aa, task_002063_8c8adcfe
  (5 distinct OCR-extraction tasks, all reward=0)
- Signal: first user message of all 5 tasks requires OCR of a `.png`
  (`tesseract`); every trajectory shows the tesseract warning
  `Invalid resolution 0 dpi. Using 70 instead`; NONE of the 5 ever
  upscaled the image or set a DPI — verified by scanning every OCR
  tool call (`dpi_flag_used=False, resize_used=False` across all 5).
- Verified (Read):
  - task_000015 msg 20/28: OCR output garbled
    (`eatalogyitem`, `Ppreduet id`, `order' "sort order>'`); agent
    then invented schema keys `department`/`sort_order` (msg 34
    output) instead of transcribing the OCR-visible `order`; final
    accuracy 0.6651 < 0.98 (result.json final_pytest).
  - task_000505 OCR call returned
    `ssh-ed25519 AAAAC3NzaC1IZDIINTES...` — garbled (`1IZDIINTES`
    where a valid key has `zDl1NTE5`); agent embedded the garbled
    key into detect_trojan.sh → exact-match detection fails. Same
    `Invalid resolution 0 dpi` warning; no upscale attempted.
  - task_001013 single OCR call `tesseract /app/auth_token.png`,
    no preprocessing/upscale; token used verbatim from a low-res
    single-pass read.
  - task_001877 20 OCR-related calls, none set dpi/resize — repeated
    failed reads instead of the standard upscale fix.
  - task_002063 single OCR call to read a spec sheet, no upscale.
- Why Instruction not Action/Control: the capability is already
  present — `tesseract` + PIL are installed and the `Bash` tool runs
  them fine (playbook: Bash is the only tool and cannot be extended).
  A Control processor cannot improve OCR because it does not itself
  invoke tesseract; it only sees message/tool text. The gap is that
  the agent does not know the standard OCR-quality recovery procedure
  (upscale to ~300 DPI, binarize, sweep `--psm`) and does not know to
  treat extracted exact tokens as literal rather than guessing. That
  is knowledge/strategy, i.e. Instruction. The rule is a general
  strategy (no task-specific keys, constants, or paths embedded), so
  it transfers to any unseen image-OCR task.
- Retroactive check (A-corrective): yes — for task_000505 the failure
  is a single garbled character class (`I`/`l`/`1`, `S`/`5`) that
  higher-resolution OCR resolves; for task_000015 the OCR text already
  contained the correct field name `order`, so "transcribe literally,
  don't normalize" alone flips the catalog route. Upscaling plus
  literal transcription addresses the decisive step in both.
- expected_global_gain: 5-task failing OCR-extraction cluster; the
  fix is the well-known tesseract quality lever + transcription
  discipline, so it plausibly flips the exact-string ones
  (task_000505 token/key match) and materially improves the
  schema/spec ones.
- regression_risk: low. The added guidance is scoped by an explicit
  "When a task requires reading text from an image" condition, so
  non-OCR tasks are unaffected. It adds ~15 lines to a very short
  system prompt; no processor/pipeline change, so already-passing
  clusters keep their exact runtime behavior.
- cost_shift: small increase. Slightly longer system prompt (tokens
  per step) and OCR tasks may run 1-3 extra preprocessing Bash calls;
  offset by avoiding the repeated-garbled-read thrash seen in
  task_001877 (20 wasted OCR calls) and the length-truncation loop in
  task_000015.
