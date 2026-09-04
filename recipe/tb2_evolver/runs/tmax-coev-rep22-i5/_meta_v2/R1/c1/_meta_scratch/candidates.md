# Candidates

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add a general OCR-quality strategy to the system prompt: when extracting
text from an image, do not trust a first raw `tesseract` pass — check for
the "Invalid resolution" warning, re-run with an explicit high DPI and an
upscaled image, cross-check the result for plausibility, and never commit a
garbled/low-confidence read as a literal value.

- Tasks affected: task_000015_89886d8d, task_000505_50b5162d
  (both fail; both decisive step is a garbled OCR read committed verbatim /
  guessed around). Related image tasks in the round: task_001013_7f3bf12e
  (OCR of a short token succeeded, so unaffected — evidence the fix is
  scoped to hard reads, not all OCR).
- Signal: every image in these tasks makes tesseract print
  `Warning: Invalid resolution 0 dpi. Using 70 instead` and then
  `Estimating resolution as <N>` where N != 70 — the classic cause of
  garbled OCR. `exit_reason=done`, `finished=no_tool_calls` (agent
  committed a bad read, not a crash). `final_pytest.accuracy=0.6875` on
  task_000015.
- Verified (Read):
  - task_000015 step 1 tool output: `Warning: Invalid resolution 0 dpi.
    Using 70 instead. Estimating resolution as 111 ... 1. atalogyitem
    <item _id>"Mept=<depariment>Esort=<sort order>` — garbled. Agent then
    (step ~14) says "Based on the sample URLs and the garbled OCR output, I
    can infer the following mapping" and hardcodes a *guessed* schema.
    Final accuracy 0.6875 (< 0.98). Never tried `--dpi`/upscaling.
  - task_000505 step 2 tool output: `Warning: Invalid resolution 0 dpi.
    Using 70 instead. Estimating resolution as 173 ssh-ed25519
    AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8` —
    an ed25519 key mis-OCR'd (`lZDI1NTE5` -> `IZDIINTES`, stray spaces and
    a `|`). Agent embedded this corrupt string verbatim into the detector.
    Reward 0.
- Why Instruction not Action: TB2 hard-limits the agent to a single `Bash`
  tool (playbook: tool_registry additions are ignored by the evaluator), so
  a new OCR tool cannot be surfaced. Tesseract already fires and returns
  data — the capability is present; what is missing is the *technique* to
  make it produce clean output (DPI override + upscale). That is knowledge,
  not capability.
- Why Instruction not Control: a post-`Bash` processor cannot re-run OCR
  with better parameters (that is the agent's own next Bash call), and it
  cannot tell an OCR command from any other Bash call without task-shaped
  heuristics. The remedy must be applied by the agent at author-time, which
  is exactly what a prompt strategy rule does. The rule is fully general
  (no key strings, schema keys, or filenames) and transfers to any unseen
  image-OCR task.
- Retroactive check (A-corrective): yes. task_000015 — a clean OCR read of
  the mapping (achievable via upscale + `--dpi 300`, the standard fix for
  the exact "Invalid resolution 0 dpi" warning present here) removes the
  need to guess, so the emitted schema keys match and accuracy clears 0.98.
  task_000505 — a clean read yields the correct ed25519 key, so the
  detector matches. Both decisive failures are downstream of the garbled
  read, which the rule targets directly.
- expected_global_gain: flips the hard-image-OCR sub-cluster (>=2 tasks)
  where exact/structured extraction is the blocker; generalizes to any
  future task that OCRs a low-DPI image because the rule keys off the
  tesseract warning shape, not any task specifics.
- regression_risk: low. The rule only adds "verify/upscale before trusting
  a garbled read" guidance; it does not change behaviour on non-image
  tasks. Worst case a few extra Bash calls on image tasks (task_001013
  already succeeds with a clean short read and would simply confirm and
  move on). No change to the processor pipeline, so passing clusters are
  structurally untouched.
- cost_shift: negligible-to-slightly-positive on image tasks only (one or
  two extra tesseract re-runs); zero on the ~43 non-image tasks. Net
  per-round token delta expected < 1%.
