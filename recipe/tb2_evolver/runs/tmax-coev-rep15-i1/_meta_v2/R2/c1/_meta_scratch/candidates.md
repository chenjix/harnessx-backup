# Candidates — R2/c1 (focus: task_000015_89886d8d)

## Candidate C-001
[lens: capability-gap | lever: control | intent: corrective]

Add a one-shot, OCR-activity-triggered `OcrRobustnessReminderProcessor` that
injects the standard OCR-recovery ladder (upscale first, set `--dpi`, sweep
`--psm` modes, binarize) plus a "derive the output contract from the
authoritative source, not the provided sample" discipline — delivered right
after the agent's first `tesseract`/`pytesseract` call.

- Tasks affected (corrective, ≥2 distinct, same mechanism):
  - `task_000015_89886d8d` — URL→schema migration; schema mapping lives in a
    low-DPI synthetic PNG.
  - `task_000505_50b5162d` — forensic task; SSH public key must be transcribed
    from `/app/evidence.png`.
- Signal: `eval_passed=False` on both; both are OCR-from-image tasks
  (`grep tesseract` hits both trajectories). task_000015 `final_pytest`:
  `Accuracy metric 0.0000 is below the 0.98 threshold`. task_000505
  `final_pytest`: `2 of 2 evil bypassed: ls_evil, cat_evil` (a misread key ⇒
  detector never matches). The stock pipeline has NO OCR-quality mechanism.
- Verified (Read of message bodies):
  - `task_000015_89886d8d` step 2 tool result: tesseract emits
    `Warning: Invalid resolution 0 dpi. Using 70 instead` then garbled
    `atalogyitem <item _id>...category ... order ... sort order`. Steps
    3,9,11,15,17,19,21,23 (Read): the agent only ever tweaks
    contrast/brightness/sharpen/autocontrast/threshold and re-runs — it NEVER
    upscales, never sets `--dpi`, never sweeps `--psm`. Step 37 (Read): it
    gives up and writes `ROUTES` with output keys `department`/`sort_order`
    invented from the *sample's* query-param names; step 40 output uses
    `department`,`sort_order`,`ui_theme`,`traffic_source` — none of which match
    the golden schema derived from the image (`category`,`order`,… on the
    right-hand side of the mapping arrows visible even in the garble).
  - `task_000505_50b5162d` first user msg (Read): "Extract the hidden SSH
    public key from the image file `/app/evidence.png`"; run `grep tesseract`
    confirms OCR path; verifier failed `test_adversarial_corpus` — the detector
    it built off the transcribed key matched nothing.
- Why Control not Instruction: the always-on system prompt is the wrong home —
  this guidance is only relevant on image/OCR tasks (a small minority), and
  loading an OCR ladder into every task's prompt is dead weight and dilutes the
  prompt for the majority. A Control hook keyed off the agent's own OCR Bash
  activity delivers the strategy *exactly and only* when it is relevant, at the
  decisive moment (right after the first OCR result), and costs zero tokens on
  non-image tasks. Why Control not Action: the OCR tool (`tesseract`) is already
  present and works — this is a strategy gap in *how* the agent drives it, not a
  missing capability; a new tool would duplicate `tesseract` and still leave the
  model driving it weakly.
- Retroactive check (A-corrective): yes — the garble in task_000015 is a
  classic low-DPI symptom (tesseract's own `0 dpi` warning); upscaling +
  `--dpi`/`--psm` reliably cleans this class of synthetic render, and the second
  half of the message (derive keys from the mapping's right-hand side, not the
  sample) directly targets the exact wrong turn (step 37) where the agent
  invented `department`/`sort_order` from sample params. Had this been in
  context after step 2, the agent had a concrete recovery path instead of
  guessing. For task_000505 a correctly-transcribed key is a prerequisite for
  any matching detector, so cleaner OCR is the unblock.
- expected_global_gain: flips the OCR-from-image failure cluster (≥2 tasks,
  task_000015 + task_000505; task_000536 shares OCR but its failure is a CSV
  quoting-format issue, so it is not counted here). Generalizes to ANY unseen
  task that must read text from a low-quality image — the ladder is standard
  OCR practice with no task literals.
- regression_risk: Low. The processor never arms unless the agent itself runs
  an OCR tool, so non-image tasks (the majority) are byte-for-byte unaffected.
  On an armed task it only *appends* one user message (never rewrites tool
  calls, so it cannot clobber a real or foreign-processor tool call) and fires
  at most once. Worst case on a false trigger (agent ran tesseract for an
  unrelated reason) is a single extra ~400-token message it can ignore. The two
  passing OCR tasks (task_000338, task_001652) already succeeded with good OCR
  habits; a reminder consistent with what they did will not break them.
- cost_shift: +~400 tokens once per OCR task (a small minority of the suite);
  ~0 on all non-OCR tasks. Net positive vs a wasted 0-reward run; may also save
  the repeated cosmetic-tweak retries the agent currently burns (task_000015
  spent ~11 near-identical OCR attempts).
