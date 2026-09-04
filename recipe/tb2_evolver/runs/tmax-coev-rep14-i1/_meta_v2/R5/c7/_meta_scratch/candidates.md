# Candidates — R6 c7 (focus task_000505_50b5162d)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the stock `CustomSelfVerifyProcessor` with `RealArtifactVerifyProcessor`:
a drop-in one-shot self-verify that, **only when a self-referential test fixture
was detected during the run** (agent authored an input file via `echo`/here-doc
AND ran an executable against a file), adds one task-agnostic checklist item
telling the agent to exercise its deliverable against the REAL artifacts present
in the environment rather than a fixture built from a value it derived. When the
antipattern is absent, the injected checklist is byte-for-byte the stock text.

- Tasks affected (corrective, same mechanism — self-referential validation of a
  derived-value program):
  - task_000505_50b5162d (security) — trojan detector
  - task_000536_9c16e8ef (data_querying) — OCR'd id → audit script
  - task_000015_89886d8d (software_engineering) — sampled schema → migrate parser
- Signal: all three are `reward=0`, `exit_reason=done`, `finished=no_tool_calls`
  (voluntary exits where the self-verify processor fires), and all fail on the
  REAL grader input while the agent had "verified" against a self-constructed or
  self-consistent input.
- Verified (Read, task_000505 messages.json):
  - msg 7 (tool): tesseract emits garbled key
    `ssh-ed25519 AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8`
    (real ed25519 prefix `AAAAC3NzaC1lZDI1NTE5` misread; stray spaces inserted).
  - msg 9: hardcodes that string into `/home/user/detect_trojan.sh`.
  - msg 11 (tool call): builds `/tmp/malicious_test.txt` by
    `echo "<garbled key>" > /tmp/malicious_test.txt` then runs
    `/home/user/detect_trojan.sh /tmp/malicious_test.txt` → exit 1 → declares
    "works". This is the exact `echo-into-file` + `run-script-against-file`
    fingerprint the processor keys on. The real trojaned binaries `cat_evil` /
    `ls_evil` (grader's `EVIL_DIR`, present on disk in the agent phase) were
    never grepped.
  - result.json final_pytest: `2 of 2 evil bypassed: cat_evil, ls_evil` — the
    garbled key never matched the real embedded key.
  - task_000536: OCR'd employee-id → `run_audit.sh`; final_pytest a CSV
    quoting diff (`"Source Code Repo"` vs `Source Code Repo`) that comparing
    against the real DB output would have surfaced; voluntary exit.
  - task_000015: OCR'd routing schema, inferred rules from 4 sample URLs, then
    "verified" against those same 4 samples; accuracy 0.6614 on the hidden real
    2000-URL set — the samples the fixture was built from could never expose the
    gap. Voluntary exit.
- Why Control not Instruction: the accepted-elsewhere and reverted instruction
  shapes both live at the prompt (R1 `h_verify_real_artifacts_v1` accepted on a
  different lineage; R3 `h_ocr_extraction_discipline_v1` **reverted**, lost 4
  tasks — a broad always-on prompt append inflated steps on easy tasks). A prompt
  rule fires unconditionally and cannot detect *whether this run actually
  exhibited the self-referential-test shape*, so it pays a global cost on every
  task. The Control lever gates the extra guidance on a mechanical runtime signal
  (fixture-write + run-against-file), so tasks without the antipattern receive the
  byte-identical stock checklist — a much smaller regression surface than the
  reverted prompt shape. This is a genuinely different lever+shape from the
  reverted instruction hypothesis, not a re-proposal.
- Why not R2's `OutputContractVerifyProcessor` shape: that pending candidate adds
  a byte-format audit + independent re-derivation (targets off-by-one / CSV-format
  contract). Mine targets a distinct antipattern — validating against a
  self-authored fixture instead of the real on-disk artifacts — detected by a
  runtime fingerprint, and only 536 overlaps (536 is helped by both because its
  audit was never diffed against the real DB). Different mechanism, different
  trigger; not a collision of the same shape.
- Retroactive check (A-corrective): yes — for 505, had the agent re-run
  `detect_trojan.sh` against the real `cat_evil`/`ls_evil` (present on disk), the
  garbled-key mismatch surfaces immediately, prompting a key re-extraction /
  cross-check; the residual OCR misread is a model limit but the discipline
  converts a silent false-pass into an observable failure the agent can act on.
  For 015/536 the "exercise against real inputs, not the samples/fixture you
  built" item points directly at the uncovered gap. The append-only, one-shot
  mechanism cannot itself regress a passing task, and stock text is preserved
  when the antipattern is absent.

- expected_global_gain: Flips/relieves the self-referential-validation cluster
  (≥3 tasks across security / data_querying / software_engineering). The
  discipline generalizes to any detector/classifier/transformer/parser task where
  the deliverable consumes a derived value and the grader uses real on-disk
  artifacts.
- regression_risk: Very low — drop-in replacement for `CustomSelfVerifyProcessor`
  with identical firing mechanism and contract (+1 user msg, one-shot, keepalive
  tool call, no system-prompt mutation, no message removal). On any run that does
  NOT match the fixture+run-against-file fingerprint the injected checklist is
  byte-for-byte the stock text, so non-matching tasks are unchanged. Worst case on
  a matching task: a few extra `find`/`ls`/re-run Bash calls before exit.
- cost_shift: Negligible → small positive. One conditional ~180-token checklist
  item fires only on antipattern runs; net-favorable because it converts silent
  wrong-answer finishes into corrected deliverables and adds nothing to
  non-matching tasks.
- rollback_trigger: If next round is flat/down AND task_000505/536/015 stay F on
  the same real-input assertions (residual is a pure model extraction/OCR limit),
  OR any previously-passing voluntary-exit task regresses to F, revert to the
  stock `CustomSelfVerifyProcessor`.
