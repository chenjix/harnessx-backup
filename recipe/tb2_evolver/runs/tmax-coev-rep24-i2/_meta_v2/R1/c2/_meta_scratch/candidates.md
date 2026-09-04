# Candidates — Round 1 (c2)

Assigned focus: `task_000015_89886d8d` fails. Diagnosis below shows it is one
of a 3-task OCR-from-image cluster, so the fix is scoped to the class, not the
single task.

## Candidate C-001
[lens: capability-gap | lever: instruction | intent: corrective]

Add a general robust-OCR extraction procedure to the system prompt: treat raw
tesseract output as unreliable, upscale + grayscale before OCR, try multiple
`--psm` modes, and cross-check the read against sample data / format
constraints before building on it.

- Tasks affected: task_000015_89886d8d, task_000505_50b5162d, task_002063_8c8adcfe
- Signal: all three are `reward=0`; all three require extracting *exact* text
  from a PNG via tesseract, and the tool output shows garbled OCR plus the same
  low-DPI warning `Invalid resolution 0 dpi. Using 70 instead.`
- Verified (Read, body-quoted):
  - task_000015 tool output: OCR renders route schema as
    `"1. eatalogitemy <item _id>'Mept-<depariment>Esort-<sorLorder>"` —
    unreadable; agent then *guessed* a nested `{"path":{...},"query":{...}}`
    schema with keys `department`/`sort_order`, but golden schema is flat with
    keys derived from the image (`dept`→`category`, `sort`→`order`); final
    accuracy 0.0000.
  - task_000505 tool output: SSH key OCR'd as
    `ssh-ed25519 AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8`
    — spurious ` + |` and `1/l/I` confusions corrupt the base64; an exact key
    match against binaries is then impossible; reward 0.
  - task_002063 tool output: `"(CRO-1 POLYNOMIAL: 0x9E82"` and
    `"INITIAL VALUE OXFFFE"` — `CRC-16`→`CRO-1`, `0x`→`OX`, hex digits
    uncertain; a CRC needs exact hex constants; reward 0.
  - None of the three ever upscaled/resized the image before OCR (grep for
    `resize|upscal|--dpi|LANCZOS|resample` = 0 hits in 505/2063; task_15 only
    tried `--psm 6` once and used the word "scale" in prose without acting).
- Why Instruction not Action: TB2 exposes only the `Bash` tool and cannot add
  tools (playbook: "exactly one tool: Bash"), and tesseract + PIL are already
  present in the sandbox — the *capability* exists. The gap is knowing HOW to
  invoke OCR robustly (upscale, psm sweep, verify), which is procedural
  knowledge, not a missing action. A new tool is impossible here and would
  duplicate tesseract anyway.
- Why Instruction not Control: a post-`Bash` processor cannot re-run OCR with
  better settings on the agent's behalf without hardcoding the image path and
  re-implementing the extraction — that would be a task-specific hook, not a
  general mechanism. The recovery has to stay agent-driven because the right
  preprocessing (upscale factor, psm, cross-check) depends on the specific
  image, which only the agent sees at runtime.
- Retroactive check (A-corrective): yes — if the agent had upscaled + swept psm
  and cross-checked against the sample URLs (task_15) / base64 & hex format
  constraints (task_505/2063), it would have recovered clean readings; the
  decisive failure in all three was building on garbled OCR. The guidance also
  explicitly warns against inventing a schema shape and to match the task's
  exact structure, addressing task_15's nested-vs-flat error.
- expected_global_gain: flips a recurring 3-task OCR-extraction cluster
  (software_engineering, security, data_processing domains) and generalizes to
  any future image→exact-value task.
- regression_risk: low. The block only activates on image/OCR tasks ("When a
  task requires reading exact text ... out of an image"); non-OCR tasks (the
  large majority) ignore it. Minor added prompt length applies to every task
  (~25 lines), a small constant token cost.
- cost_shift: +~250 tokens of system prompt per task (constant). On OCR tasks,
  a few extra Bash calls to upscale/sweep psm — cheaper than the current
  thrashing (task_15 spent 37 steps re-running tesseract with no upscale).
