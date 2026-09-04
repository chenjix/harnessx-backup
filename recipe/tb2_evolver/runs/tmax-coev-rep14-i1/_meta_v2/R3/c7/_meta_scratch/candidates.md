# Candidates — R4 (c7), assigned focus task_000505_50b5162d

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a Control processor (`SelfFabricatedTestGuardProcessor`) that detects the
self-fabricated-test tautology at the tool layer — the agent executes a
deliverable it authored using ONLY input files it created itself earlier in the
run — and appends a one-shot corrective note telling it to re-run against the
original, pre-existing task input artifacts instead.

- Tasks affected (corrective): task_000505_50b5162d (primary). Related shape but
  NOT caught by this precise detector: task_000015_89886d8d (tests only against
  the small self-selected sample rather than the hidden/real set) — a cousin
  mechanism whose OCR/edge-case half is addressed by the pending R3 instruction
  hypothesis; called out here for honesty, not claimed as a guaranteed flip.
- Signal: `eval_passed=False`, `exit_reason=done`, `finished=no_tool_calls`,
  steps=14; final_pytest `2 of 2 evil bypassed: cat_evil, ls_evil` — the built
  detector never matched the real trojaned binaries.
- Verified (body-quoted):
  - task_000505 msg 7 (tool result): tesseract emitted the GARBLED ed25519 key
    `ssh-ed25519 AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8`
    (correct prefix is `AAAAC3NzaC1lZDI1NTE5`; `l`→`I`, `1`→`I`, `9`→`S`, stray
    spaces). msg 9 hardcoded that string into `/home/user/detect_trojan.sh`.
  - task_000505 msg 15/17 (tool call + result): the agent created
    `/tmp/malicious_test.txt` by `echo`-ing the SAME garbled string, then ran
    `/home/user/detect_trojan.sh /tmp/malicious_test.txt` → Exit code 1
    ("works"). This is the tautology: a test input manufactured from the derived
    value can only ever pass. The real evil corpus (present on disk) was never
    run through the script.
  - Detector dry-run over this trajectory: fires on msg-19 final-verification run
    `detect_trojan.sh /tmp/test_binary` where `/tmp/test_binary` was fabricated
    (heredoc/echo) with the derived key; does NOT fire on the earlier
    `detect_trojan.sh /app/evidence.png`-style real-artifact runs (none here, but
    a synthetic real-input run confirms silence).
- Why Control not Instruction: the instruction lever has ALREADY been aimed at
  this exact discipline twice on this task — R1-c7 `h_verify_real_artifacts_v1`
  (accepted) and R3 `h_ocr_extraction_discipline_v1` (pending), both telling the
  agent in the system prompt to "validate against real inputs, not
  self-fabricated echoes." task_000505 still failed. A passive prompt sentence
  competes with the model's over-confidence and loses. The analyze skill's
  cross-round rule is explicit: a lever tried repeatedly on the same cluster
  without flipping is a signal to look elsewhere. A Control hook fires the
  correction AT THE DECISIVE MOMENT (right after the tautological test run, in
  tool-result context) rather than once at session start, which is a materially
  different delivery mechanism.
- Why Control not Configuration: no existing knob expresses "detect a run whose
  inputs are all agent-fabricated"; this needs new detection logic, not a tuning
  change.
- Retroactive check (A-corrective): partial-yes. If, right after the msg-15
  tautological test, the agent had received "you tested against a file you
  fabricated; run against the real artifacts on disk," the next natural step is
  `strings /app/<evil binary> | head` — which surfaces the CORRECT key and lets
  it fix the hardcoded string. The residual OCR misread is a model limit; the
  false-confidence loop that PREVENTED discovery is what this closes. Honest
  caveat: if the model reads the note but still declines to inspect the real
  corpus, 505 stays F — but the append-only design cannot itself regress
  anything.
- expected_global_gain: Closes the self-fabricated-test false-pass mechanism for
  detector/classifier/parser deliverables — a general false-confidence shape.
  Primary target 505; the discipline generalizes to any task where ground-truth
  inputs exist on disk and the agent validates against manufactured echoes.
- regression_risk: Very low. Append-only on the tool result (mirrors
  CustomEditToolProcessor's contract), never terminates, never touches the
  system prompt, fires ≤2×/run. Trajectory sweep: fires on 1 of 29 R2-passing
  tasks (task_000760, a reproducibility task that legitimately probes with a
  self-made CSV) — harmless since the note is additive and 760 also has the real
  binary available; no passing task can be broken by extra advisory text.
- cost_shift: Negligible-to-favourable. One ~120-token note (≤2×) only when the
  tautology shape fires; may add a few Bash calls to inspect real artifacts on
  the tasks it targets, converting silent wrong-answer finishes into corrected
  deliverables.
- rollback_trigger: If next round shows task_000505 still F with the same
  `evil bypassed` assertion AND the note demonstrably fired (nudge ignored →
  residual is a pure OCR/model capability limit), OR any previously-passing task
  regresses to F, revert to R0 pipeline.
