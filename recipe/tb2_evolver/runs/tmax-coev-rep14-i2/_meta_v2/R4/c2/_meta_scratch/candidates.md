# Candidates — R4 c2

Assigned focus: **task_000015_89886d8d** (fails, reward=0, accuracy 0.6556 < 0.98).

## Candidate C-001 — OcrQualityAdvisor: low-DPI OCR preprocessing nudge

- **Three-axis tag**: lens = *harness deficiency (missing dynamic
  guidance at the point of failure)* / lever = **control** (new
  `MultiHookProcessor`) / intent = *unblock a failing OCR-input cluster*.

### Signal (verified in trajectory bodies)

- `task_000015_89886d8d/result.json`: `reward=0`, `exit_reason=done`,
  `final_pytest` → `AssertionError: Accuracy metric 0.6556 ... below the 0.98
  threshold`. The verifier runs the agent's `migrate.py` over 2000 hidden URLs;
  ~1/3 of records are wrong.
- `task_000015` messages steps 2/4/10/12/14/20/24/28: **every** tesseract call
  returns garbled text prefixed by tesseract's own
  `Warning: Invalid resolution 0 dpi. Using 70 instead. Estimating resolution as 111`.
  The OCR read `atalogyitem`, `depariment`, `trafic source`, `feccount`,
  `sortorder`, `notity` — the agent then *inferred* the ROUTES key names
  (`product_id`, `category`, `sort_order`, `session_token`, `traffic_source`,
  `account_id`, `notifications`) from the garble + sample URLs (step 47/49
  "despite the garbled output, I was able to infer the routing rules") and
  shipped it. Wrong/misspelled key names → 1/3 of records mismatch the golden
  output.
- Across steps 2–28 the agent re-ran contrast / PSM / OEM / threshold variants
  on the **original 800×400 resolution** but **never upscaled the image or set
  an explicit `--dpi`** — the two highest-yield fixes for small/low-DPI OCR.

### Cross-task cluster (generalization evidence)

`grep "Invalid resolution" *.messages.json` over the 50-task set returns **3**
tasks, all reward=0:

- `task_000015_89886d8d` (data/URL-migration) — focus; 0.6556 accuracy.
- `task_000505_50b5162d` (security) — `final_pytest`:
  `2 of 2 evil bypassed: cat_evil, ls_evil`. tesseract on the SSH-key evidence
  image hit the identical low-res warning; character confusions (I/l/1, 5/S,
  spurious spaces) made the exact-string trojan detector miss every adversarial
  sample. **Same root cause, different domain.**
- `task_002063_8c8adcfe` — tesseract usage is incidental; its terminal blocker
  is the `import requests` verifier collection abort (a *different* cluster,
  owned by the requests-ensurer bets). Not claimed here; the advisor is a
  harmless no-op relative to that failure.

Two distinct domains (URL-migration, security-detection), one
harness-fixable mechanism: the agent trusts a low-quality first OCR read and
never reaches for the standard preprocessing that would fix it.

### Retroactive check (variant: "would the mechanism have fired and helped?")

On task_000015, the advisor's detector matches on step 2's result (contains
`Invalid resolution` after a `tesseract` Bash call). It appends the one-time
recipe (upscale 3–4×, explicit `--dpi 300`, grayscale+binarize, diff re-runs,
disambiguate glyphs, verify against the sample). A clean OCR read resolves the
exact key names the accuracy test compares against — the delta between 0.6556
and 0.98 is precisely the misread/misspelled schema keys, not an algorithmic
error (the URL-parsing/JSON-Lines logic itself is sound: the agent's own sample
run produced 3 valid outputs from 4 URLs). So a correct read is plausibly
sufficient to clear the 0.98 bar. Same on task_000505: correct glyphs restore
the exact-string match the detector needs.

### Why control (new processor), not the other levers

- **Not instruction (system prompt)**: the failure is contextual and rare — it
  only matters when a tesseract call *just* emitted the low-res signal.
  Embedding an OCR recipe in the always-on system prompt would (a) tax the ~47
  non-OCR tasks with irrelevant guidance every turn, and (b) arrive too early
  (before the agent even knows the image is low-DPI). A processor delivers the
  recipe exactly at the failure point, once, only when the signal is present.
- **Not configuration (knob)**: no existing knob addresses OCR quality.
- **Not action (tool)**: TB2 exposes only `Bash`; adding a tool is a no-op
  (playbook: tool registry cannot be extended for the agent).

### Note on lineage / novelty

`h_ocr_lowdpi_advisor_v1` was proposed and **accepted** in R3 with the same
`predicted_affected` (task_000015, task_000505). However, the promoted
**current_config for this proposal (R1/config.yaml == R3/config.yaml)** does
**not** actually wire `OcrQualityAdvisor` — the pipeline is the stock 12-entry
R1 pipeline with no OCR processor. So the mechanism is *absent* from the lineage
I evolve, and the focus task is still broken. Accepted-not-reverted ⇒ novelty
permits re-establishing it. This round wires it in.

### Pareto statement

- `expected_global_gain`: unblocks the OCR-input cluster (≥2 distinct-domain
  tasks: task_000015, task_000505) whose reward=0 is a garbled-OCR root cause,
  not reasoning. Generalizes to any unseen image-to-text task; the recipe is
  standard tesseract practice with zero task-specific literals.
- `regression_risk`: very low. Fires ≤1×/task and ONLY when a real
  tesseract/pytesseract Bash call emits the low-resolution signal, so the ~47
  non-OCR tasks never see it. Appends only to a tool-result string (same
  contract as CustomEditToolProcessor); no message insert/drop/reorder.
- `cost_shift`: negligible-to-slightly-positive on OCR tasks (may prompt 1–2
  corrective re-runs, replacing wasted near-identical retries); exactly zero on
  non-OCR tasks. No forced extra model turns.
- `rollback_trigger`: revert if any previously-passing task regresses to F
  attributable to the advisory, or if synthetic replay fails on the processor.
