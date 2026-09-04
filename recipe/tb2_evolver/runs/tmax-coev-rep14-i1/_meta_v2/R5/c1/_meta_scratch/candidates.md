# Candidates — R5 c1

Assigned focus: `task_000015_89886d8d` (software_engineering, reward=0,
exit_reason=done, no_tool_calls, 41 steps). final_pytest:
`Accuracy metric 0.6614 is below the 0.98 threshold`.

## Candidate C-001 — upscale-before-OCR remedy on tesseract low-DPI signal

**Three-axis tag:** lens = tool-result runtime signal · lever = control
(new `MultiHookProcessor`) · intent = close a mechanical OCR-extraction
deficiency for the whole OCR cluster, not the one task.

### Signal
`task_000015_89886d8d` requires `migrate.py` built from routing rules in
`/app/routing_schema.png` (800×400 RGB, a dense multi-row mapping table).
The verifier runs the script on a hidden 2000-URL set; accuracy 0.6614 < 0.98.

### Verified body evidence
- msg 2/4: `tesseract /app/routing_schema.png stdout` →
  `Warning: Invalid resolution 0 dpi. Using 70 instead. Estimating resolution as 111`
  and garbled text (`eatalogyitem`, `Wept=<depariment»`, `fsort=<sortorder>`).
- msgs 9,11,13,17,19,21,23: the agent tried grayscale, contrast enhance,
  binarize (threshold 128), `ImageOps.autocontrast`, `ImageFilter.SHARPEN`,
  and PSM 1/3/6/11/12 — all *contrast* remedies. It **never once upscaled**
  the image (`grep resize|--dpi|resample|LANCZOS` over all tool_calls = ∅).
- msg 25→: unable to read the schema, the agent guessed a 3-route mapping from
  the 3 matching sample URLs in `/home/user/sample_urls.txt` (msg 16); msg 28+
  "verified" only against those samples. Hidden edge cases → 0.6614.
- Cluster witness: `grep "Invalid resolution 0 dpi"` matches all 5 tesseract
  tasks in the round (015, 505, 536 fail; 338, 1652 pass). The two passers
  OCR short/simple text (`121\n000\n-1-2-1`; `Admin Token: TKN-8842-OMEGA`)
  that survives 70 dpi; the failers need dense/lossy extraction. Upscaling a
  clean short-text image still OCRs clean → the nudge cannot regress 338/1652.

### Change
- `processors/ocr_lowdpi_remedy.py` — new `OcrLowDpiRemedyProcessor`
  (`_order=34`, after `CustomEditToolProcessor` 30, before
  `CustomSelfVerifyProcessor` 90). Tracks each Bash command by
  `tool_call_id` in `on_before_tool`; in `on_after_tool`, when the result
  carries the tesseract low-DPI signal AND the command invoked tesseract AND
  the command did not already upscale/`--dpi`, appends ONE content-agnostic
  remedy note: upscale ~3-4x with LANCZOS + re-OCR (optionally `--dpi 300`)
  before inferring a schema from samples. Fires ≤ `max_fires` (2) per run.
  Append-only on the tool result; no removals, no system-prompt mutation, no
  termination. No task literals / schema content / route names.
- `config.yaml` — register the processor via absolute `file://` path; rest
  byte-identical to R0. `system_prompt.txt` sibling copied unchanged.

### Retroactive check (variant: would-it-have-fired + would-it-have-helped)
- **Would it have fired?** Yes — msg 2's result matches `_LOWDPI_SIGNAL`, the
  command matches `_TESSERACT_CMD`, and it contains no upscale token, so the
  first tesseract call would have received the remedy note. It re-fires once
  more (max_fires=2) if the next tesseract call is still low-DPI.
- **Would it have helped?** The note supplies the exact missing action
  (upscale to raise effective DPI) the agent never tried. If the upscaled
  re-OCR reads the table, the schema is correct and the hidden-set accuracy
  clears 0.98. If the upscaled OCR still garbles (pure model/OCR capability
  limit), 015 stays F — but the note also blocks the premature
  "infer-from-samples" shortcut that produced the 0.66 guess, which is the
  proximate cause of the low score.
- **False-positive sweep:** append-only note; fires only on the real tesseract
  low-DPI signal. 0 of the 29 R2-passing tasks invoke tesseract, so no passing
  non-OCR task ever sees it; the 2 passing OCR tasks are not harmed (upscaling
  clean text is still clean).

### Why control-processor, not instruction (Why X not Y)
The instruction lever was already tried for this exact cluster in R3
(`h_ocr_extraction_discipline_v1`, predicted_affected included task_000015)
and was **reverted** for global regression: it appended OCR guidance to the
system prompt on *every* task, inflating the prompt on the ~48 non-OCR tasks
and flipping 4 unrelated tasks to F. This candidate is the corrected form of
that idea moved to the control lever: it carries the *same* mechanical fact
(raise DPI by upscaling) but delivers it only at the moment the low-DPI signal
actually appears, so it is provably inert on every non-OCR run — eliminating
the regression channel that killed the R3 bet. It is distinct from the R3
hypothesis (different lever, runtime-gated, append-only on a tool result, no
prompt mutation), so it does not re-propose a reverted hypothesis_id.

### Pareto
- `expected_global_gain`: relieve the dense-OCR-extraction failing cluster
  (015; mechanism also covers 505 garbled ed25519, 536 garbled memo — all trip
  the same low-DPI signal). Generalizes to any low-DPI tesseract task.
- `regression_risk`: near-zero — append-only, runtime-gated to the tesseract
  low-DPI signal, no prompt bloat, inert on all non-OCR tasks and harmless on
  the 2 passing OCR tasks.
- `cost_shift`: negligible — one ~150-token note at most twice per OCR task;
  net-favourable if it converts a guessed schema into a correct one and
  avoids the sample-inference repetition spiral.
- `rollback_trigger`: if next round shows 015 still F on the same accuracy
  assertion after an upscale+re-OCR attempt (residual = pure OCR-model limit),
  OR any previously-passing OCR task (338, 1652) regresses, revert.
