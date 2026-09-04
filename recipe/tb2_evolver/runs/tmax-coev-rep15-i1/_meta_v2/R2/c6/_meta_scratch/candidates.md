# Candidates — R2/c6 (focus: task_000505_50b5162d)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a one-shot exit-intent reminder that fires when the agent both (a) extracted a
value from a lossy/authoritative source (OCR / image / binary strings) and then (b)
verified its deliverable ONLY against fixtures it fabricated by echoing that same
extracted value — a circular self-test that cannot detect an extraction error —
nudging it to re-verify the extracted value against the source and test the
deliverable against the real provided inputs.

- Tasks affected (corrective, ≥2 distinct, same mechanism):
  - task_000505_50b5162d — detector greps for an OCR'd SSH key; agent tested it only
    against `/tmp/malicious_test.txt` it created by `echo`-ing the SAME garbled key,
    and against `/bin/ls` (trivially clean). final_pytest:
    `2 of 2 evil bypassed: ls_evil, cat_evil` — the real trojaned binaries contain
    the *correct* key, which the garbled OCR string never matches.
  - task_000015_89886d8d — schema mapping OCR'd from a PNG; agent verified its
    migrate script only by running it on the 3-line provided `sample_urls.txt`
    (a self-selected trivial fixture), never on the golden corpus the grader uses.
    final_pytest: `Accuracy metric 0.0000 ... below 0.98`. Same shape: the
    verification fixture is derived from / narrower than the authoritative inputs, so
    a wrong extraction survives the self-test.

- Signal: `exit_reason=done`, `finished=no_tool_calls`, `reward=0`, low step count
  (9 steps for 505). Body shows an extraction tool (`tesseract`) followed by a
  self-test whose input file is created in the SAME session by `echo`/`cat >` of the
  extracted value, then a "works correctly" declaration.

- Verified (Read):
  - task_000505 msg 3 (tool result): `tesseract ... ssh-ed25519
    AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8` — note the
    OCR garble (`IZDII` for `lZDI1`, spaces around `+`, a `|` pipe, truncated tail).
  - task_000505 msg 7: `cat > /home/user/detect_trojan.sh` hardcodes
    `SSH_KEY="ssh-ed25519 AAAAC3NzaC1IZDII..."` (the garbled string) into the script.
  - task_000505 msg 9 (test): `echo "ssh-ed25519 AAAAC3NzaC1IZDII..." >
    /tmp/malicious_test.txt` then runs the detector on it → exit 1 "correct". This is
    circular: the fixture contains the exact string the detector searches for, so the
    test passes regardless of whether the OCR read was right.
  - task_000505 msg 11: only other test is `/bin/ls` (a clean system binary) → exit 0.
    The agent never listed `/app` for real trojaned binaries, never re-checked the OCR.
  - task_000015 msgs 40 & 66: the only correctness check runs migrate.py on the
    3-line `sample_urls.txt`; msg 73 concludes "works correctly with the sample URLs".

- Why Control not Instruction: the trigger is a mechanical, cross-task shape in the
  agent's own Bash activity (extraction tool → self-fabricated fixture built from the
  extracted value → declare done). A static prompt rule would fire on every task
  including the majority that have no extraction/self-test pattern, adding always-on
  tokens and noise; a Control hook keyed off the observed activity fires only on the
  small subset that actually exhibits the circular-test shape and delivers the nudge
  exactly at the exit decision, via the same proven keepalive-tool-call exit-intercept
  path the existing ServiceDepsReminderProcessor uses.

- Why Control not a duplicate of the pending correctness-self-verify / OCR-ladder
  candidates: those two target *general* re-derivation (recompute a number a second
  way) and *OCR driving quality* (upscale/--dpi/--psm). Neither names the specific
  circular-fixture pathology — the agent DID "verify" and DID run OCR, but its test
  was self-fulfilling because it fed the deliverable a fixture built from its own
  possibly-wrong output instead of the real provided inputs. This candidate closes
  that distinct verification-integrity gap and is additive to (not overlapping with)
  either pending bet.

- Retroactive check (A-corrective): yes — if, at its exit turn, task_000505 had been
  told "your test fixture contains the exact value you extracted, so it cannot detect
  an extraction error; re-read the source and test against the real provided inputs",
  the agent (still at 9/80 steps, ample budget) would re-run tesseract with better
  settings and/or list `/app` for real binaries, catch the garble, and fix the key.
  For task_000015 the same nudge points it at the golden corpus rather than the
  3-line sample.

- expected_global_gain: Flips the "circular self-test / self-fabricated-fixture"
  slice of the large `exit_reason=done`/`reward=0` cluster — tasks that extract a
  value from a lossy source and then rubber-stamp a self-built fixture. Generalizes
  to any unseen task where the correctness of a deliverable depends on a value
  extracted from an authoritative artifact (OCR, parsed config, decoded blob) and the
  grader tests against real inputs the agent never exercised.
- regression_risk: Low. New singleton group `self_test_integrity_reminder`, `_order=93`
  (after CustomSelfVerify=90 and ServiceDeps=91), replaces nothing, injects one user
  message via the contract-clean keepalive path, fires at most once per task, and only
  when BOTH an extraction-tool signal AND a self-fabricated-fixture signal have been
  observed. Tasks without both signals are byte-for-byte unaffected. Worst false
  positive: one ~300-token advisory on a task that happened to extract text and also
  echo it into a scratch file. Always yields to a genuine exit next turn.
- cost_shift: +~300 tokens once + typically +1-3 Bash re-verify round-trips on the
  small armed subset; ~0 on the non-extraction majority. Net positive vs a wasted
  0-reward run.
- rollback_trigger: If R3 shows task_000505 still failing `test_adversarial_corpus`
  AND task_000015 still failing `test_migrate_script_accuracy` with the processor
  firing, the residual blocker is extraction/reasoning capability (the model cannot
  read the key correctly even when told to re-check) — revert this processor.
