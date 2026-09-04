# Candidates — R2 (proposal c7)

Assigned focus: `task_000505_50b5162d` (security, reward=0, exit_reason=done, 10 steps).

## Diagnosis

Task 505: extract an SSH public key from `/app/evidence.png` via OCR, then write
`/home/user/detect_trojan.sh` that flags ELF binaries containing that key.

The agent ran `tesseract` and got a **garbled** key:
`ssh-ed25519 AAAAC3NzaC1IZDIINTES...` — the standard ed25519 blob prefix
`AAAAC3NzaC1lZDI1NTE5` was misread (`l`→`I`, `1`→`I`, `5`→`S`) and stray spaces
were inserted (`+ |J9tY +X0...`). The agent hardcoded this corrupted string into
the script, then **verified against test files it created from the same corrupted
string** (msgs 9, 11, 15). Those synthetic tests "passed" trivially — the script
matches the exact string it was given — so the agent declared done at step 10.
The verifier runs the script against the REAL trojaned binaries (which contain the
uncorrupted key); `2 of 2 evil bypassed` → fail.

Two harness-addressable defects, both general:
1. The agent trusted a lossy-source (OCR) extraction without any format sanity
   check (a valid ssh-ed25519 blob is base64 and starts with a fixed prefix).
2. The agent validated its deliverable against **self-fabricated inputs echoing its
   own derived value**, guaranteeing a false pass, instead of against the **real
   artifacts already present in the sandbox** (the actual trojaned/clean binaries).

The OCR misread itself is a model capability limit, but the false-confidence
verification loop (never testing against real on-disk inputs) is a harness
discipline gap the system prompt can close generally.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add a general "validate against the real environment artifacts before finishing"
discipline to the system prompt: survey inputs up front; when the deliverable is a
script/program/classifier, run it end-to-end over the REAL inputs the task points
at (not self-fabricated inputs that echo a derived value); and treat values pulled
from lossy sources (OCR/scan) as suspect until cross-checked against the format
they must obey and the real target data on disk.

- Tasks affected (same mechanism, distinct inputs):
  - `task_000505_50b5162d` — OCR key → script; self-tested against its own
    garbled string; real evil binaries never exercised.
  - `task_000536_9c16e8ef` — OCR employee-id → SQLite audit script; failed on
    output format (`"Source Code Repo"` vs `Source Code Repo`) that comparing
    against the real DB rows / stated format would have surfaced.
- Signal: `exit_reason=done` with a short step count and `reward=0`; final_pytest
  shows the deliverable behaves wrong on the verifier's real inputs while the
  agent's own in-run tests all "passed".
- Verified (Read of messages.json):
  - 505 msg 7: tesseract output `ssh-ed25519 AAAAC3NzaC1IZDIINTES...` (garbled
    prefix). msg 9: `SSH_KEY="ssh-ed25519 AAAAC3NzaC1IZDIINTES..."` hardcoded.
    msgs 9/11/15: agent creates `/tmp/malicious_test.txt` etc. by echoing the
    SAME string, then confirms the script matches — never touches a real binary.
    result.json final_pytest: `2 of 2 evil bypassed: cat_evil, ls_evil`.
  - 536 msg 0: task requires OCR of `/app/target_memo.png` + a script producing a
    CSV from `/app/audit.db`. result.json final_pytest: row 0 differs only by CSV
    quoting — a format mismatch checkable against the real DB output before exit.
- Why Instruction not Control: a Control hook cannot know, per task, which on-disk
  files are the "real inputs" nor what the correct behavior is — that judgment is
  task-specific and must stay agent-side. The gap is procedural knowledge ("verify
  against real artifacts, distrust lossy extractions"), not a mechanical transform
  of tool output, and there is no tool return to post-process (the agent simply
  didn't run the right test). A blocking processor would risk false-positives on
  tasks with no on-disk reference and cannot be scoped safely across the benchmark.
- Why Instruction not Action: the agent already has Bash and can run anything; no
  capability is missing — only the discipline to point it at the real inputs.
- Retroactive check (A-corrective): yes — for 505, running the script against the
  real trojaned binaries present on disk would have shown 0 evil detected, exposing
  the OCR corruption and prompting re-extraction/format cross-check before exit; the
  format-validity hint (ed25519 prefix) independently flags the garble. For 536,
  validating the CSV against the real DB rows/stated format surfaces the quoting
  mismatch before declaring done.

- expected_global_gain: closes the OCR/extraction→script cluster (>=2 tasks:
  505 security, 536 data_querying) where a false-confidence self-test masks a wrong
  deliverable; the "verify against real inputs before stopping" rule generalizes to
  any script/detector/transformer task and reinforces the known-effective TB2
  "double-confirmation before exit" and "upfront environment survey" patterns.
- regression_risk: low. The prompt only adds verification discipline; it does not
  remove capability or change the pipeline. Worst case is a few extra Bash
  verification steps on tasks that were already passing (they will simply re-confirm
  and stop). No previously-passing task depends on skipping verification. Short
  correct tasks may run 1–3 more steps.
- cost_shift: small positive token/step cost from extra verification runs on some
  tasks; likely net-neutral to net-positive by converting silent wrong-answer
  finishes into corrected deliverables. No change to per-call token cap or pipeline.

## Mechanism

`config.yaml` is byte-identical to R0 (still `SiblingSystemPromptBuilder`); the
change is entirely in the sibling `system_prompt.txt` written next to this config.
No new processors/tools/templates — Instruction lever via the prompt sidecar.
