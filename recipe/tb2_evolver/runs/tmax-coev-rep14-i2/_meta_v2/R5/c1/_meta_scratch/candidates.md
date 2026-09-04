# Candidates — R5 c1

## Candidate C-001 — OcrQualityAdvisor (low-DPI OCR quality nudge)

- **Lens / Lever / Intent**: runtime tool-result observation / Control
  (new MultiHookProcessor) / close a failing OCR-input cluster by
  redirecting the agent to the standard high-yield tesseract preprocessing
  it never reaches for on small/no-DPI images.

- **Assigned focus**: `task_000015_89886d8d` (software_engineering).
  Build `/home/user/migrate.py` from a URL→JSON schema embedded in
  `/app/routing_schema.png`. Verifier runs the script over 2000 hidden
  URLs and requires accuracy >= 0.98.

### Signal (verified body evidence)

- `task_000015` result.json: `initial_pytest.passed=true`;
  `final_pytest`: `AssertionError: Accuracy metric 0.0000 is below the 0.98
  threshold. assert 0.0 >= 0.98`; `reward=0`, `exit_reason=done`, 42 steps.
- `task_000015` messages: step 2 tesseract emits
  `Warning: Invalid resolution 0 dpi. Using 70 instead. Estimating
  resolution as 111` and garbled text `atalogyitem`, `depariment`,
  `Esort`, `s2ss_id`, `tafic source`. Steps 3–24 the agent re-runs
  contrast / autocontrast / threshold / PSM 1/3/6 / char-whitelist
  variants **all on the original 800x400 image** and **never upscales
  or sets an explicit --dpi**. Step 25/29/33 it commits a migrate.py
  whose schema keys are guessed (`{"type":"product","item_id",
  "dept","sort"}`) rather than the real OCR'd keys (`product_id`,
  `category`, `order`). Net verifier accuracy 0.0000.
- Cross-task confirmation (same root cause, distinct domain):
  `task_000505_50b5162d` (security) — messages hit the identical
  `Invalid resolution` tesseract signal on an SSH-key image; misread
  glyphs; `final_pytest`: `assert not ['2 of 2 evil bypassed: cat_evil,
  ls_evil']`, `reward=0`.
- `grep "Invalid resolution\|Estimating resolution"` over the 50
  `*.messages.json` matches exactly 3 tasks: task_000015, task_000505
  (both OCR-content failures this advisor targets) and task_002063
  (an unrelated `requests`-collection abort — a different cluster, on
  which this advisor is a harmless no-op since it only appends to the
  tesseract call result and never changes control flow).

### Why this is a harness deficiency, not a capability gap

The agent HAS the capability (PIL + tesseract + Bash) and correctly
recognised the read was bad — it tried many preprocessing variants. What
it lacked was the specific, generic tesseract remedy for the *no-DPI /
small-image* shape: upscale ~3-4x + explicit `--dpi 300` + binarize. The
tesseract stderr literally names the defect (`Invalid resolution 0 dpi`),
so a runtime observer can detect exactly the failure state and inject the
missing generic recipe. This is a runtime-context deficiency the harness
can close without any task-specific knowledge.

### Retroactive check (variant: "would the mechanism have changed the trajectory?")

Both failing tasks emitted the low-res signal on a real tesseract call,
which is precisely the arming condition. Had the one-time advisory been
present, the agent would have received the upscale + explicit-dpi +
binarize + glyph-diff recipe at exactly the moment it was cycling
ineffective contrast/PSM tweaks. A clean OCR read yields the true schema
keys (task_000015) / the exact key bytes (task_000505), the necessary
precondition for the verifier to pass. Yes — the mechanism directly
targets the observed blocker.

### Why Control (new processor), not Instruction/Configuration

- **Not Instruction (system prompt)**: a generic "OCR carefully" prompt
  line would fire on every task (cost + dilution) and, crucially, the
  agent already *was* trying hard on OCR — the miss is a specific runtime
  recipe delivered at the specific failure moment, which a static prompt
  cannot target. Embedding the recipe unconditionally is noise for the
  ~47 non-OCR tasks.
- **Not Configuration (knob)**: no existing processor exposes a knob for
  "detect tesseract low-res stderr and advise". The mechanism is new.
- **Control** fits: observe the real tool result, detect tesseract's own
  defect signal, inject the missing generic recipe exactly once, only on
  armed tasks. Contract-safe (mutates only the tool-result string; no
  message insert/drop/reorder — same shape as CustomEditToolProcessor).

### Tasks affected (intent = close-failing-cluster)

- `predicted_affected`: [task_000015_89886d8d, task_000505_50b5162d]
- Fires on exactly the tasks whose tesseract call emits the low-res
  signal. The ~47 non-OCR tasks never arm.

### Pareto framing

- **expected_global_gain**: unblocks a 2-task, 2-domain OCR-input cluster
  (software_engineering + security) whose reward=0 is caused by garbled
  low-DPI reads, not reasoning. Generalizes to any unseen image-to-text
  task; recipe is standard tesseract practice with zero task-specific
  literals. This mechanism was accepted in a prior round (R3
  h_ocr_lowdpi_advisor_v1) but the config lineage I evolve from (R0)
  does NOT contain it, so the focus task is still fully broken here.
- **regression_risk**: very low. Fires <=1x/task and ONLY when a real
  tesseract/pytesseract Bash call emits the low-resolution signal, so
  non-OCR tasks never see it. Appends only to a tool-result string; no
  message insert/drop/reorder (contract check: 0 violations).
- **cost_shift**: negligible-to-slightly-positive on OCR tasks (may
  prompt 1–2 corrective re-runs, replacing wasted near-identical retries);
  exactly zero on non-OCR tasks. No forced extra model turns.
- **rollback_trigger**: revert if any previously-passing task regresses
  to F attributable to the advisory, or if synthetic replay fails on the
  OcrQualityAdvisor processor.
