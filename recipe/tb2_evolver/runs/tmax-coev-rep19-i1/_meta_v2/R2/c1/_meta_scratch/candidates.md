# Candidates — R2 / c1

Assigned focus: `task_000015_89886d8d` (software_engineering) fails with
`reward=0`, `exit_reason=done`. Final pytest: "Accuracy metric 0.3411 is below
the 0.98 threshold." The agent produced a runnable `migrate.py` but its route /
mapping logic was wrong on the hidden 2000-URL dataset.

## Candidate C-001
[lens: capability-gap | lever: control | intent: corrective]

Add an `OcrQualityAdvisor` `MultiHookProcessor` that appends a one-time,
general OCR-quality recipe (upscale image, force `--dpi`, try alternate
`--psm` modes, verify legibility before trusting output) to the tool result
the first time the agent invokes OCR (`tesseract` / `pytesseract`) in a task.

- Tasks affected: task_000015_89886d8d, task_000505_50b5162d
  (both fail; both root-caused to garbled OCR of a small image).
- Signal: both tasks `exit_reason=done`, `reward=0`, failing on a *correctness*
  metric (not loop/budget). Both trajectories show tesseract emitting
  `Warning: Invalid resolution 0 dpi. Using 70 instead` on every run and
  returning garbled text. Neither loop breaker fires because the agent varies
  the command slightly (contrast/whitelist tweaks) rather than repeating it
  byte-identically.
- Verified (Read):
  - task_000015 step [2] tool result: OCR = "Legacy toV2 Schema Mapping ... 1.
    atalogyitem <item _id>\"Mept=<depariment>Esort=..." — garbled. Steps
    [3]/[9]/[12]/[16]/[20]/[24] repeat garbled OCR with contrast/threshold
    tweaks; the agent never upscales or sets `--dpi`. At step [17]/[21] it gives
    up on the image and *infers* the schema from the 3 sample URLs
    (msg [14]). Final `migrate.py` (step [82]) hard-codes 3 routes inferred that
    way → 0.34 accuracy on the hidden edge-case dataset.
  - task_000505 step [4] tool result: OCR = "ssh-ed25519
    AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8" — the
    base64 SSH key is mis-read (`IZDIINTES` for `lZDI1NTE5`, spurious spaces /
    `|`). Verifier: "2 of 2 evil bypassed" — the detector never matches the real
    key because the extracted key string is wrong.
- Why Control not Instruction: a static system-prompt note would bloat every
  task's prompt (most tasks never touch OCR) and is easily ignored buried in a
  general prompt. A processor fires the advisory *at the moment the agent
  invokes OCR*, in-context, so it lands exactly when relevant and stays inert
  otherwise.
- Why Control not Action: the OCR capability is already present (tesseract on
  `Bash`); the agent reaches for it. The gap is *quality of use*, not a missing
  action, so no new tool is warranted.
- Why not the existing RepeatedCommandBreaker: that only fires on
  byte-identical repeats; here the agent tweaks the command each time, so the
  loop never trips even though every variation shares the same unfixed
  resolution problem.
- Retroactive check (A-corrective): yes (plausible). Both failures trace to
  garbled OCR the agent trusted / worked around. If the upscale+dpi+psm recipe
  had been in context at the first OCR call, legible extraction of the full
  schema (000015) and the exact SSH key (000505) is the standard, achievable
  fix — the recipe directly targets the "Invalid resolution 0 dpi" warning both
  tasks emit. Caveat: the advisor guarantees the guidance is present; it does
  not guarantee the model executes the upscale correctly, so the flip is
  plausible not certain. The sure gain is redirecting the agent away from
  fabricating/trusting garbled data.

- expected_global_gain: closes the "garbled-OCR → wrong artifact" failing
  cluster (>=2 tasks: 000015, 000505). Generalizes to any unseen task that must
  read structured text from an image — a recurring TB2 class (7 OCR tasks in
  this round). The recipe is standard OCR practice, not task knowledge.
- regression_risk: very low. The advisor only mutates the tool result of the
  *first* OCR command per task (contract-safe `event.result` append), fires at
  most once, and is completely inert on the ~5 passing OCR tasks and every
  non-OCR task. Worst case is a few hundred extra tokens on OCR tasks whose OCR
  was already clean — harmless. No message insertion, no prompt mutation.
- cost_shift: negligible/positive. One ~200-token advisory on tasks that invoke
  OCR (a small minority); zero on all others. Net expected effect is *fewer*
  wasted tokens on OCR tasks that currently thrash through 6+ failed OCR
  variations before giving up.
- rollback_trigger: if R2 pass_rate is flat/down AND neither 000015 nor 000505
  flips, revert (the advisory did not change the outcome and adds surface area).
