
## Round 4 (c2) — low-DPI OCR quality advisor (re-establish in R1 lineage)

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-29T06:00:00Z
hypothesis_id: h_ocr_lowdpi_advisor_v2
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000505_50b5162d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Unblocks the OCR-input cluster (>=2 distinct-domain tasks: task_000015 URL-migration schema image, task_000505 SSH-key evidence image) whose reward=0 is caused by garbled tesseract output on small/no-DPI images, not by reasoning errors. Generalizes to any unseen image-to-text task; the injected recipe (upscale + explicit --dpi + binarize + cross-check) is standard tesseract practice with zero task-specific literals."
regression_risk: "Very low: fires <=1x/task and ONLY when a real tesseract/pytesseract Bash call emits the low-resolution signal (Invalid resolution / Estimating resolution), so the ~47 non-OCR tasks never see it. Appends only to a tool-result string (same contract as CustomEditToolProcessor); no message insert/drop/reorder. contract=0 violations."
cost_shift: "Negligible-to-slightly-positive on OCR tasks (may prompt 1-2 corrective re-runs, replacing wasted near-identical retries); exactly zero on non-OCR tasks. No forced extra model turns."
rollback_trigger: "Revert if any previously-passing task regresses to F attributable to the advisory, or if synthetic replay fails on the OcrQualityAdvisor processor."
retry_rationale: "h_ocr_lowdpi_advisor_v1 was ACCEPTED (not reverted) in R3 with the same predicted_affected. But the current_config for this proposal (R1/config.yaml, byte-identical to R3/config.yaml) does NOT wire OcrQualityAdvisor -- the promoted R3 baseline is the stock 12-entry R1 pipeline with no OCR processor. The mechanism is therefore ABSENT from the lineage I evolve and task_000015 is still broken. Novelty permits re-establishing an accepted-not-reverted hypothesis; this round wires it in."
-->

### Why

Assigned focus task_000015_89886d8d (build a URL-migration utility from a
schema image) failed reward=0 at 28 steps exit=done: the verifier ran the
agent migrate.py over 2000 hidden URLs and required accuracy >= 0.98; the
agent got 0.6556. Root cause is upstream of the code: EVERY tesseract call
(messages steps 2,4,10,12,14,20,24,28) returned garbled text prefixed by
tesseract own "Warning: Invalid resolution 0 dpi. Using 70 instead.
Estimating resolution as 111". The OCR read atalogyitem, depariment,
trafic source, feccount, sortorder, notity. The agent then INFERRED the
ROUTES key names from the garble + sample URLs (steps 47/49: "despite the
garbled output, I was able to infer the routing rules") and shipped it --
misspelled/guessed key names make ~1/3 of records mismatch the golden output.
Across steps 2-28 the agent re-ran contrast/threshold/PSM/OEM/whitelist
variants on the ORIGINAL resolution but NEVER upscaled the image or set an
explicit --dpi -- the two highest-yield fixes for small/low-DPI OCR. A
cross-task scan (grep "Invalid resolution" *.messages.json) found the SAME
root cause on task_000505_50b5162d (security): tesseract on the SSH-key
evidence image hit the identical low-res warning; character confusions
(I/l/1, 5/S, spurious spaces) made the exact-string trojan detector miss
every adversarial sample (2 of 2 evil bypassed). Two distinct domains, one
harness-fixable mechanism: the agent trusts a low-quality first OCR read and
never reaches for the standard preprocessing. (A third task, task_002063,
emits the tesseract signal but its terminal blocker is the import requests
collection abort -- a different cluster; the advisor is a no-op there and is
not claimed.)

This is a harness deficiency (missing dynamic guidance at the failure point),
not a model capability gap: the agent has full Bash/PIL/tesseract capability;
what is missing is the just-in-time nudge to apply upscale+--dpi when tesseract
itself flags a guessed resolution.

### Changes

- processors/ocr_quality_advisor.py -- OcrQualityAdvisor MultiHookProcessor.
  on_before_tool records Bash calls invoking tesseract/pytesseract/
  image_to_string; on_after_tool, if that call result carries tesseract own
  low-resolution signal (Invalid resolution / Estimating resolution / Using N
  instead), appends a ONE-TIME generic advisory: upscale ~3-4x, pass explicit
  --dpi 300, grayscale+binarize with --psm 6, diff the re-run, disambiguate
  ambiguous glyphs (I/l/1, 0/O, 5/S, 8/B, rn/m, spaces), verify against any
  provided sample before committing downstream code. Fires <=1x/task, only on
  real OCR calls with the low-res signal. Contract-safe: mutates only the
  tool-result string, no message insertion. _order=32.
- config.yaml -- R1 pipeline + register OcrQualityAdvisor via absolute file://
  path (inserted between CustomEditToolProcessor and CustomSelfVerifyProcessor).
- system_prompt.txt -- sibling read by SiblingSystemPromptBuilder, byte-identical
  to R1 baseline.

### Evidence

- task_000015 result.json: reward=0, exit=done, 28 steps; final_pytest
  "Accuracy metric 0.6556 ... below the 0.98 threshold".
- task_000015 messages steps 2/4/10/12/14/20/24/28: repeated garbled tesseract
  output, each with Invalid resolution 0 dpi; steps 47/49 committed inferred
  ROUTES; NO upscale/--dpi anywhere.
- task_000505 result.json: final_pytest "2 of 2 evil bypassed"; reward=0.
- Cross-scan: exactly 3 tasks emit the tesseract low-res signal; 2
  (task_000015, task_000505) have OCR as terminal root cause, 1 (task_002063)
  blocked by a different cluster (requests-collection).
- Validators: canonicalize ok; dry_fire likely_bugs 0/0; contract violations 0;
  literals findings 0.

### Uncertainty

Two ways to know the bet is wrong: (1) the predicted tasks do not flip even
though the advisory fired -- then either the agent still did not act on the
recipe (following-instructions gap) or a clean OCR read alone is insufficient
(schema-interpretation ambiguity beyond the garble), reclassifying the residual
as a capability gap; (2) an OCR task whose first read was already correct gets
the advisory and the agent chases it into a worse read -- the rollback trigger.
The signal is narrow (tesseract own DPI warning), so non-OCR regression is nil.
