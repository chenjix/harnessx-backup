import io

memo = "/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep14-i1/learnings.md"

entry = r'''

## Round 6 (c7) — verify against real artifacts, not self-authored fixtures

<!-- journal:frontmatter
round: 6
timestamp: 2026-08-28T18:00:00Z
hypothesis_id: h_real_artifact_verify_v1
levers: [control]
predicted_affected: [task_000505_50b5162d, task_000536_9c16e8ef, task_000015_89886d8d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
retry_rationale: "Reverted R3 h_ocr_extraction_discipline_v1 was an always-on Instruction prompt append (lost 4 tasks via step inflation on easy tasks). This is a different lever (Control) and a different shape: a drop-in self-verify replacement that GATES the extra guidance on a mechanical runtime fingerprint (echo/here-doc fixture write plus run-executable-against-file), emitting byte-identical stock checklist on non-matching runs -- so it carries none of the always-on regression surface that sank the prior attempt."
expected_global_gain: "Flip/relieve the self-referential-validation cluster (>=3 tasks: 505 trojan detector, 536 audit script, 015 migrate parser) where the agent verifies a derived-value program against a fixture it built from its own derived value; discipline generalizes to any detector/classifier/transformer/parser task graded on real on-disk artifacts."
regression_risk: "Very low -- drop-in for CustomSelfVerifyProcessor with identical firing mechanism/contract (+1 user msg, one-shot, keepalive tool call, no system-prompt mutation). On any run not matching the fixture+run-against-file fingerprint the injected message is byte-for-byte the stock checklist, so non-matching tasks are unchanged."
cost_shift: "Negligible-to-small-positive: one conditional ~180-token checklist item fires only on antipattern runs plus a few find/ls/re-run Bash calls; nothing added on non-matching tasks."
rollback_trigger: "If next round is flat/down AND task_000505/536/015 stay F on the same real-input assertions (pure model extraction/OCR limit), OR any previously-passing voluntary-exit task regresses to F, revert to stock CustomSelfVerifyProcessor."
-->

### Why

Assigned focus task_000505_50b5162d (security, reward=0, exit_reason=done,
no_tool_calls, 10 steps). The agent OCR'd an SSH key from /app/evidence.png and
got a garbled string (real ed25519 prefix AAAAC3NzaC1lZDI1NTE5 misread as
AAAAC3NzaC1IZDIINTES, stray spaces inserted), hardcoded it into
/home/user/detect_trojan.sh, then "verified" by echoing the same garbled string
into a /tmp fixture and running the detector against THAT file -- a guaranteed
false pass. The real trojaned binaries cat_evil/ls_evil (the grader's EVIL_DIR,
present on disk during the agent phase) were never grepped, so the verifier
reported "2 of 2 evil bypassed". The OCR misread is a model limit, but the
self-referential validation loop -- testing a derived-value program against a
fixture built from the same derived value -- is a harness discipline gap. Same
shape recurs: task_000536 (OCR'd id -> audit script, CSV quoting never diffed
against the real DB output) and task_000015 (schema inferred from 4 sample URLs,
"verified" only against those 4 samples -> 0.66 accuracy on the hidden real
2000-URL set). All three are voluntary exits where the self-verify processor
already fires but its generic checklist does not catch the circular test.

### Changes

- processors/real_artifact_verify.py -- new RealArtifactVerifyProcessor
  (MultiHookProcessor, singleton group tb2_self_verify, order 90). Drop-in
  replacement for CustomSelfVerifyProcessor: identical one-shot keepalive +
  single appended user message. on_before_tool tracks a content-agnostic
  fingerprint -- a here-doc or echo/printf redirect into a file (self-authored
  fixture) AND a later run of an executable/script against a file path. On
  voluntary exit, if both fired, injects a checklist with ONE extra task-agnostic
  item: exercise the deliverable against the REAL artifacts present in the
  environment (survey with find/ls), not a fixture built from a derived value;
  cross-check lossy-source values against an authoritative on-disk source.
  Otherwise the injected message is byte-for-byte the stock checklist.
- config.yaml -- swap the final pipeline entry
  benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor for a
  file://...::RealArtifactVerifyProcessor reference. Rest byte-identical to R0;
  system_prompt.txt sibling copied unchanged.

### Evidence

- task_000505_50b5162d messages.json msg 7 (tesseract): garbled ed25519 key;
  msg 9 hardcodes it; msg 11 echo "<key>" into a /tmp fixture then runs
  /home/user/detect_trojan.sh against that fixture -> exit 1 -> "works" (the exact
  echo-into-file + run-script-against-file fingerprint). result.json final_pytest:
  "2 of 2 evil bypassed: cat_evil, ls_evil".
- task_000536_9c16e8ef final_pytest: EMP-4001 quoted-vs-bare CSV field mismatch,
  checkable against the real DB; exit_reason=done, no_tool_calls.
- task_000015_89886d8d final_pytest: accuracy 0.6614 < 0.98 on the hidden real
  dataset; agent verified only against the 4 samples it inferred rules from;
  exit_reason=done, no_tool_calls.

### Uncertainty

If the model reads the nudge but still trusts a garbled OCR value without
re-running against the real binaries, 505 stays F (OCR itself is a model limit;
the rule only helps if the agent acts on the "exercise against real inputs" cue).
For 015/536 the cue is more mechanical (real hidden inputs / real DB) and likelier
to land. Append-only, one-shot, stock-preserving-on-non-match design cannot itself
regress a passing task. If next round shows the cluster unchanged on the same
real-input assertions, the residual is a pure extraction/OCR capability gap;
revert per rollback_trigger.
'''

with io.open(memo, "a", encoding="utf-8") as f:
    f.write(entry)
print("appended", len(entry), "chars")
