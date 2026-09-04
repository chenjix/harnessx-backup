# Candidates

## Candidate C-001
[lens: capability-gap | lever: control | intent: corrective]

Add an `on_after_tool` processor that detects the OCR engine's low-DPI
fallback warning in tool output and appends a one-time, task-agnostic
remediation hint (rescale the image to a higher DPI before re-running OCR).

- Tasks affected: task_000015_89886d8d, task_000505_50b5162d, task_002063_8c8adcfe
- Signal: all three tasks require OCR of a PNG via tesseract; every tesseract
  run emits `Warning: Invalid resolution 0 dpi. Using 70 instead.` and
  `Estimating resolution as <low>`. `exit_reason=done`, `reward=0`. The
  extracted text is garbled on exactly the characters that decide correctness.
- Verified (Read):
  - task_000015 tool[2..24]: OCR output repeatedly garbled
    (`eatalogyitem`, `Ppreduet id`, `edepartment>`); agent explicitly says
    "the OCR is still garbled" then (step 31) "let me just proceed ... based
    on the mapping I can infer" — final_pytest accuracy 0.6793 < 0.98 because
    the guessed schema mapping was wrong.
  - task_000505 tool[4]: `Warning: Invalid resolution 0 dpi ...` then
    `ssh-ed25519 AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8`
    — spaces, `1`/`I` and `+`/`|` confusion corrupt the SSH key that the
    detection script must match exactly.
  - task_002063 tool[2]: `(CRO-1 POLYNOMIAL: 0x9E82` (should read CRC-16) and
    `INITIAL VALUE OXFFFE` (`O`/`0` confusion) — the CRC constants are read
    wrong, so the computed CRC is wrong.
  - Grep across all three message logs: no agent ever issued a `--dpi`,
    `resize`, `upscale`, or `.resize` command — the standard remediation was
    never attempted. Agents only tried contrast/threshold/PSM tweaks, which
    do not change the effective resolution.
- Why Control not Instruction: the remediation must fire *conditionally* on an
  observable runtime signal (the engine's own low-DPI warning in tool output),
  which a static system-prompt rule cannot key off — a prompt rule would have
  to unconditionally lecture every task about OCR, inflating context on the
  ~47 non-OCR tasks for no benefit. A Control `on_after_tool` hook injects the
  hint exactly when (and only when) the failure shape is observed.
- Why Control not Action: the agent already *has* the capability (tesseract +
  PIL are installed; upsampling is a one-line PIL/imagemagick call). The gap is
  not a missing action but not knowing to reach for it after a low-DPI read;
  a new tool would duplicate tooling the sandbox already provides and still
  wouldn't tell the agent when to use it.
- Retroactive check (A-corrective): yes — in all three tasks the decisive
  error is a garbled read that the agent then guessed from. Had the hint
  surfaced the rescale-to-300-dpi technique right after the first low-DPI
  warning (which appears on the very first tesseract call in each task), the
  agent had ample steps remaining (task_000015 finished at step 30; 505/2063
  were not step-bound) to re-OCR at higher resolution and recover the exact
  tokens.
- expected_global_gain: closes the OCR-garble cluster (3 failing tasks, all
  sharing one mechanism). Generalizes to any future image-OCR task where the
  source PNG lacks DPI metadata — a common, benchmark-agnostic shape.
- regression_risk: minimal. The processor is silent unless a tool result
  contains the low-DPI OCR warning regex, so the ~47 non-OCR tasks are
  untouched (no context added, no behaviour change). Worst case on an OCR task
  is one extra ~120-word hint appended once to a tool result.
- cost_shift: negligible. At most one short hint per task, and only on tasks
  that already run OCR; net token change on the benchmark is near zero and
  is expected to be cost-negative overall because it should curtail the
  multi-attempt PSM/threshold thrashing seen in task_000015 (11+ OCR retries).
