# Candidates

## Candidate C-001
[lens: capability-gap | lever: instruction | intent: corrective]

Add a general "reading text from images (OCR)" strategy section to the
system prompt: upscale the image before OCR, try multiple page-segmentation
modes, and treat garbled OCR as a STOP condition (re-run with better
preprocessing) rather than guessing the content from mangled text.

- Tasks affected (corrective, same mechanism): task_000015_89886d8d,
  task_002063_8c8adcfe, task_000505_50b5162d
- Signal: 5 of 7 tesseract-using tasks fail (reward=0). Each failing task
  invokes `tesseract <image> stdout` at **default settings** exactly once,
  gets character-level garbling, and then builds task logic on the garbled
  text. `finished=no_tool_calls`/`budget_exceeded`. None of them ever
  upscales the image (the single highest-leverage OCR preprocessing step).
- Verified (Read + reproduced against the actual container images):
  - task_000015 msgs step 1: `tesseract /app/routing_schema.png stdout` →
    "eatalogyitem <item _id>...category* edepartment>...". Agent tried
    contrast/threshold/sharpen/PSM permutations but never upscaled, hit a
    repetition loop, then guessed the schema and emitted a wrong route-1
    mapping (`{product_id, category, department, sort_order}` vs golden
    `{product_id, category, order}`) → accuracy 0.6733 < 0.98 threshold.
    Reproduced: 4x LANCZOS upscale + `--psm 6` yields a fully legible
    schema ("product_id ... category ... order ... traffic_source ...").
  - task_002063 msgs: `tesseract /app/hw_specs.png stdout` (default) →
    "CRO-1 POLYNOMIAL: 0x9E82 / INITIAL VALUE OXFFFE" (garbled hex).
    Reproduced: 4x upscale + `--psm 6` → "CRC-16 POLYNOMIAL: 0x9EB2 /
    INITIAL VALUE: 0xFFFF" — correct constants that the CRC logic needs.
  - task_000505 msgs: `tesseract /app/evidence.png stdout` (default) →
    an SSH ed25519 key mangled at the character level
    ("...H9 + |J9tY +X07yG..."); agent proceeded on the mangled key.
- Why Instruction not Control: the capability is already present — the
  agent has `tesseract`, `python3`/PIL, and shell to preprocess; it simply
  does not know the general OCR-reliability recipe (upscale first; multiple
  PSM modes; verify legibility before consuming). A Control processor that
  injected the hint on every `tesseract` call would mechanically bypass the
  agent's own judgement (e.g. blindly upscaling a container that lacks PIL —
  task_000505's image has no PIL, so a naive PIL-resize hint silently
  produces empty output) and adds standing pipeline surface for a technique
  that should be applied selectively. A prompt-level strategy keeps the
  method-selection agent-side and carries no per-task constants.
- Why not Action: adding a tool is impossible on this benchmark — the agent
  is restricted to `Bash` only (tb2-playbook). And the fetch/OCR path is not
  broken; only the agent's technique is.
- Retroactive check (A-corrective): yes — for task_000015 and task_002063,
  a legible OCR read (verified reproducible via upscaling) removes the
  guessing step that produced the wrong output; with correct schema/
  constants in context the agent's already-working downstream logic passes.
  task_000505's key is at least made character-accurate by better OCR
  discipline (multiple PSM / verification), removing the mangled-key blocker.
- expected_global_gain: OCR-from-image is a recurring task family (>=7 tasks
  here, 5 failing). A general OCR-quality strategy plausibly flips the
  garbled-read cluster and generalizes to any future image-reading task.
- regression_risk: Low. The added guidance is conditional ("when a task
  asks you to read text from an image"); it does not alter behaviour on the
  non-image majority of tasks. The prompt grows by ~10 lines → negligible
  per-step token cost. No processor/tool code added, so no new crash surface.
- cost_shift: Slightly positive tokens on image tasks (a few extra OCR
  attempts + verification) — net-cheaper than the current failure mode where
  task_000015 burned a repetition loop and task_001877 hit budget_exceeded.
  System-prompt grows ~300 tokens, amortised across all tasks.
