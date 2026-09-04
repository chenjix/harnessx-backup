# Candidates — R2/c1 (assigned focus: task_000015_89886d8d)

## Candidate C-001
[lens: capability-gap | lever: control | intent: corrective]

Add `OcrQualityAdvisor`, a one-time `on_after_tool` control hook that, when a
`tesseract`/`pytesseract` Bash call reports a missing / guessed image
resolution, appends a generic high-accuracy OCR-preprocessing advisory (upscale
~3-4x, explicit `--dpi 300`, grayscale+binarize, then diff the re-run and
disambiguate ambiguous glyphs) before the agent builds anything on the text.

- Tasks affected: task_000015_89886d8d, task_000505_50b5162d (two distinct
  tasks — software_engineering URL-migration and security trojan-detection —
  sharing one root cause: garbled tesseract output from a low-DPI image, then a
  downstream artifact built on the misread text).
- Signal: both trajectories contain a real tesseract call whose result carries
  tesseract's own low-resolution warning
  (`Warning: Invalid resolution 0 dpi. Using 70 instead. / Estimating
  resolution as 111`), and both end reward=0 with the verifier failing on
  exact-match / accuracy over the OCR-derived content.
- Verified (Read):
  - task_000015 step 2 tool result: `Legacy toV2 Schema Mapping ... 1.
    atalogyitem <item _id>"Mept=<depariment>Esort=<sort order> ...
    trafic source ... feccount id` — garbled. Steps 9,11,13,17,19,21,23,25,27:
    the agent re-ran contrast/threshold/sharpen/PSM variants on the ORIGINAL
    800x400 resolution; it NEVER upscaled or set `--dpi`. Step 31 committed a
    ROUTES table inferred from the garble (both `category` and `department`
    mapped to the same `dept` group; guessed key names). result.json:
    `Accuracy metric 0.6664 < 0.98`.
  - task_000505 step 3 tool result:
    `Warning: Invalid resolution 0 dpi ... ssh-ed25519
    AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8` —
    OCR character confusions (`IZDII` vs `lZDI1`, `NTES` vs `NTE5`) and spurious
    spaces in a base64 key. result.json verifier: `2 of 2 evil bypassed` — the
    exact-string detector missed every trojan because the key was misread.
- Why Control not Instruction: an Instruction (system-prompt) rule would load
  the OCR guidance onto all ~50 tasks — the vast majority non-OCR — inflating
  the prompt with irrelevant context for negligible benefit and violating the
  targeting principle. A Control hook fires ONLY when a real tesseract call
  emits the low-resolution signal (≤1x/task), so it is invisible to non-OCR
  clusters: zero regression surface and zero cost there. The information the
  agent needs (that its OCR read is unreliable and how to fix it) can be
  delivered from what the tool already returned — no new tool/capability is
  required, so Control beats Action too.
- Why Control not Action: TB2 exposes only Bash (hard benchmark limit); the
  agent already HAS tesseract + PIL. The gap is not a missing action but a
  missing preprocessing step it never reached for. A post-hook advisory closes
  that without adding an (impossible) tool.
- Retroactive check (A-corrective): yes. task_000505 — a clean OCR read of the
  fixed-format base64 SSH key makes the exact-string grep match → detector
  flags the trojans → pass; the advisory's upscale+`--dpi` recipe is precisely
  what recovers a small-image key read. task_000015 — the 66.6% accuracy comes
  from structural mapping errors traceable to the garble (collapsed
  category/department, guessed key names); a clean schema read yields the
  correct route→key mapping and lifts accuracy over the 0.98 bar. The advisory
  targets exactly the technique both agents omitted (upscale + explicit DPI),
  not a symptom downstream of a different blocker.
- expected_global_gain: Unblocks the OCR-input cluster (≥2 tasks, distinct
  domains) whose failures are caused by low-DPI tesseract garble rather than
  reasoning errors. Generalizes to any unseen task that reads text from a
  small/no-DPI image, because the recipe is standard tesseract practice with no
  task-specific literals.
- regression_risk: Very low. Fires at most once per task and only when a real
  tesseract/pytesseract call emits the `Invalid resolution` / `Estimating
  resolution` signal — so non-OCR tasks never see it. It only appends text to
  one tool-result string (same contract shape as CustomEditToolProcessor); no
  message insertion/drop/reorder. Worst case on an OCR task where the first read
  was already fine-but-flagged: one extra advisory the agent can ignore.
- cost_shift: Negligible-to-slightly-positive on OCR tasks (may prompt 1-2 extra
  OCR re-runs that were the correct move anyway, replacing wasted
  near-identical retries); exactly zero on the ~47 non-OCR tasks. No forced
  extra model turns.
- rollback_trigger: Revert if any previously-passing task regresses to F
  attributable to the advisory (e.g. an OCR task where the agent chased the
  nudge into a worse read), or if replay fails on the processor.
