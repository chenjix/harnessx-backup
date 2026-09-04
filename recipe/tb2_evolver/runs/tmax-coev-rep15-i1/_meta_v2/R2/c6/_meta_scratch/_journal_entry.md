## Round 2 — self-test integrity (anti-circular-fixture) (c6)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T07:10:00Z
hypothesis_id: h_self_test_integrity_v1
levers: [control]
predicted_affected: [task_000505_50b5162d, task_000015_89886d8d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the 'circular self-test / self-fabricated-fixture' slice of the large exit_reason=done/reward=0 cluster: tasks that extract a value from a lossy/authoritative source (OCR of an image, strings/decode of a binary) then 'verify' the deliverable only against a fixture built by echoing that same extracted value, so the self-test passes regardless of extraction correctness while the grader tests against the REAL provided inputs. Generalizes to any unseen task whose deliverable depends on an extracted value the grader exercises against real artifacts the agent never touched."
regression_risk: "Low. New singleton group self_test_integrity_reminder, _order=93 (after CustomSelfVerify=90 and ServiceDeps=91) so the exit-intent hooks serialize; replaces nothing; injects one user message via the proven contract-clean keepalive-tool-call path; fires at most once per task and ONLY when BOTH an extraction signal (tesseract/ocr/strings|grep/base64 -d/hexdump/...) AND a self-fabricated-fixture signal (echo/printf > file, cat > file <<EOF, tee) have been observed. Tasks without both signals are byte-for-byte unaffected. Worst false positive: one ~300-token advisory on a task that both extracted text and echoed to a scratch file. Always yields to a genuine exit on the next turn."
cost_shift: "+~300 tokens once + typically +1-3 Bash re-verify round-trips on the small armed subset; ~0 on the non-extraction majority. Net positive vs a wasted 0-reward run."
rollback_trigger: "If R3 shows task_000505 still failing test_adversarial_corpus AND task_000015 still failing test_migrate_script_accuracy with this processor firing, the residual blocker is extraction/reasoning capability (the model can't read the value correctly even when told to re-check), not verification integrity — revert SelfTestIntegrityReminderProcessor."
-->

### Why

Assigned focus task_000505_50b5162d fails `exit_reason=done`, `finished=no_tool_calls`,
`reward=0` at only 9/80 steps. The task: OCR an SSH public key out of
`/app/evidence.png`, then write `detect_trojan.sh` that flags any ELF binary
containing that key. tesseract returned a GARBLED key
(`ssh-ed25519 AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8` —
`IZDII` for `lZDI1`, spaces around `+`, a stray `|`, truncated tail). The agent
hardcoded that garble into the script and then "verified" it by `echo`-ing the SAME
garbled key into `/tmp/malicious_test.txt` and confirming the detector matched it —
a circular self-test that passes no matter how wrong the OCR was — plus a `/bin/ls`
check (trivially clean). It never listed `/app` for the real trojaned binaries and
never re-checked the OCR. The grader runs the detector against the real evil corpus
(whose binaries carry the CORRECT key), so the garbled search string matches nothing:
`2 of 2 evil bypassed: ls_evil, cat_evil`.

The same verification-integrity gap recurs at task_000015_89886d8d: a routing schema
is OCR'd from a PNG, and the agent verifies its migrate script ONLY by running it on
the 3-line provided `sample_urls.txt` (a self-selected trivial fixture), never on the
golden corpus the grader scores — accuracy 0.0000. Both share one harness-actionable
root: the agent's verification fixture is fabricated from / narrower than the
authoritative inputs, so a wrong extraction survives the self-test. This is distinct
from the pending correctness-self-verify bet (re-derive a NUMBER a second way) and the
pending OCR-ladder bet (drive OCR better) — here the agent DID verify and DID run OCR;
the specific pathology is the self-fulfilling fixture.

### Changes

- `processors/self_test_integrity_reminder.py` — new `SelfTestIntegrityReminderProcessor`
  (MultiHookProcessor, singleton group `self_test_integrity_reminder`, `_order=93`).
  Keyed off the agent's own Bash activity, never task ids. Arms only when BOTH an
  extraction signal AND a self-fabricated-fixture signal are observed. On the next
  exit-intent turn it injects one task-agnostic user message (via the proven
  keepalive-tool-call exit-intercept path) telling the agent that a fixture built from
  its own extracted value cannot detect an extraction error, and to (1) re-verify the
  extracted value against the authoritative source (re-run extraction with different
  settings; reconcile l/1, O/0, I/1 look-alikes and whitespace) and (2) exercise the
  deliverable against the REAL provided inputs, not only self-built fixtures. Bounded
  to one fire; contract-clean; content-agnostic. (C-001)
- `config.yaml` — registered the processor via absolute
  `file://…/R2/c6/processors/self_test_integrity_reminder.py::SelfTestIntegrityReminderProcessor`
  immediately after `ServiceDepsReminderProcessor` (order 93 so exit-intent hooks
  serialize); everything else byte-identical to R1/c2.

### Evidence

- `task_000505_50b5162d` msg 3 (tool): `tesseract ... ssh-ed25519
  AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8` (OCR garble).
- `task_000505` msg 7: `cat > /home/user/detect_trojan.sh` hardcodes
  `SSH_KEY="ssh-ed25519 AAAAC3NzaC1IZDII..."` (the garble) into the script.
- `task_000505` msg 9: `echo "ssh-ed25519 ...IZDII..." > /tmp/malicious_test.txt`
  then runs the detector on it → exit 1 "correct" — circular.
- `task_000505` result.json final_pytest: `AssertionError: 2 of 2 evil bypassed:
  ls_evil, cat_evil`.
- `task_000015_89886d8d` msgs 40 & 66: only correctness check runs migrate.py on the
  3-line `sample_urls.txt`; msg 73 concludes "works correctly with the sample URLs";
  final_pytest `Accuracy metric 0.0000 ... below 0.98`.

### Uncertainty

Assumes the model acts on the injected integrity reminder rather than skimming past it
and re-declaring done — the same instruction-adherence risk as any exit nudge, but
delivered exactly at the exit decision via the existing gate. For task_000505 it also
assumes that, once prompted, the model can re-run tesseract well enough to read the key
correctly (partly a capability question) OR find and test against the real binaries in
the environment. If R3 shows both tasks still failing with the processor firing, the
residual blocker is extraction/reasoning capability and the next escalation is a
Control hook that actively enumerates provided input artifacts at exit rather than more
guidance. Watch for regressions where the reminder pushes a needless re-check on an
already-correct extraction task — the arm requires BOTH an extraction signal AND a
self-fabricated-fixture signal to limit this.
