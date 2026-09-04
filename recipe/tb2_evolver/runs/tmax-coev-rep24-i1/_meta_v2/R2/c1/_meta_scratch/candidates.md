# Candidates — R2 / c1

Assigned focus: `task_000015_89886d8d` (fails, reward=0).

## Diagnosis of the focus task

`task_000015_89886d8d` requires reading `/app/routing_schema.png` via OCR
(tesseract), extracting a set of legacy URL → JSON-schema routing rules, and
writing a `migrate.py` that reproduces those mappings. The verifier runs the
script on a hidden 2000-URL dataset and requires accuracy ≥ 0.98. The agent
scored **accuracy 0.3564**.

Reading the trajectory (`messages.json`): the OCR step happened *before* the
first context compaction (msg 1 is already a summary), so the raw tesseract
output is not visible — but the downstream behaviour is unmistakable. The
agent extracted a `ROUTES` table into `migrate.py` (visible in the final
`test_parser.py` writes, msgs 4/9/13/15/29/45), then spent the **entire**
visible trajectory (~50 messages) debugging its `hypothesis` property test —
never once re-examining whether the extracted routing rules were *correct*.
The 0.36 accuracy means the OCR-derived mappings (param names, query-key →
output-key mappings, which routes exist) were substantially wrong, and the
agent trusted its single OCR pass as ground truth. It even had
`/home/user/sample_urls.txt` (a provided reference) but did not use it to
validate the extracted rules end-to-end before committing.

## This is a recurring OCR-extraction cluster, not a one-off

Six tasks in this round require extracting structured data from a PNG via
tesseract:

| task | reward | what must be extracted from the image |
|---|---|---|
| task_000015_89886d8d | 0 | multi-row URL routing schema (this focus) |
| task_000505_50b5162d | 0 | exact SSH public-key string (grep must match byte-for-byte) |
| task_000206_a943669b | 1 | (short strings) |
| task_000338_27d6a1be | 1 | 3×3 integer matrix |
| task_000536_9c16e8ef | 1 | single Employee ID |
| task_001652_86e1d185 | 1 | single admin token |

Both failures share one root cause: **the agent trusted a single, un-hardened
tesseract pass and did not cross-validate the extracted value before building
on it.** `task_000505` failed `2 of 2 evil bypassed` — its `detect_trojan.sh`
grepped for a key string that didn't match the trojaned binaries, i.e. the
OCR'd key was off by ≥1 character. `task_000015` failed on accuracy because
the routing table itself was wrong. The four passing tasks extract *short,
low-ambiguity* strings where a naive tesseract pass happens to be correct — so
the harness must not penalise them; it must add discipline that only bites when
the naive pass is wrong.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add a general **OCR-hardening strategy block** to the system prompt: when a
task depends on data extracted from an image, treat the first OCR pass as a
draft — improve extraction fidelity (preprocess the image; try more than one
tesseract page-segmentation/engine mode and reconcile) and cross-validate the
extracted values against any reference/sample data the task provides and
against structural expectations before building downstream logic on them.

- Tasks affected: task_000015_89886d8d, task_000505_50b5162d (both fail on
  un-validated OCR output; four passing OCR tasks are protected — see below).
- Signal: `final_pytest` accuracy 0.3564 on task_000015 (routing table wrong);
  `2 of 2 evil bypassed` on task_000505 (key string wrong). Both tasks' bodies
  show OCR run once, output trusted, no re-validation against provided
  reference data.
- Verified (Read):
  - task_000015 msg 56 (assistant, post-OCR summary): lists three extracted
    routes as fact and declares "The task is complete", never re-checking them;
    msgs 4-54 are all `test_parser.py` debugging, zero OCR re-verification.
    `sample_urls.txt` was provided (msg 0) but never used to validate the
    end-to-end mapping.
  - task_000505 result.json `final_pytest.output_tail`: `2 of 2 evil bypassed:
    cat_evil, ls_evil` — the extracted SSH key did not match the planted key,
    so the classifier's grep matched nothing. A single OCR character error is
    fatal here and went uncaught.
- Why Instruction not Action: the capability is present — `tesseract` is
  installed, `Bash` is available, and image-preprocessing tooling (ImageMagick
  / PIL) is standard in these containers. TB2 hard-locks the agent to a single
  `Bash` tool (playbook), so a new `@tool` cannot be added regardless. The gap
  is *knowledge of OCR-hardening discipline* (preprocess, multi-mode,
  cross-validate), which the agent can execute through Bash once told to — a
  textbook Instruction gap, not a missing action.
- Why Instruction not Control: a processor cannot know *which* extracted value
  is wrong or *how* to reconcile OCR variants — that reconciliation is
  task-specific reasoning the agent must do. A mechanical hook that force-ran
  tesseract or injected preprocessing would fire on non-image tasks and cannot
  express "compare against the provided sample". The discipline must stay
  agent-authored and conditional.
- Retroactive check (A-corrective): yes — had the agent treated the first OCR
  pass as a draft and validated the extracted routing rules against
  `sample_urls.txt` (task_000015) / re-run tesseract with preprocessing + a
  second PSM mode and diffed the key (task_000505), the character/mapping
  errors would have surfaced before the deliverable was built, flipping both
  failures.
- expected_global_gain: closes the two failing members of a concrete 6-task
  OCR-extraction cluster by adding fidelity + self-validation discipline that
  generalises to any unseen image-extraction task (the guidance names no task
  literals — no route names, no key formats, no file paths).
- regression_risk: low. The block is conditional ("when a task depends on data
  extracted from an image"). The four passing OCR tasks extract short strings a
  naive pass already gets right; an extra validation pass costs a few Bash
  calls but does not change a correct extraction. Non-OCR tasks ignore the
  block entirely. Worst case: a handful of extra Bash turns on image tasks.
- cost_shift: small positive on image tasks only (1-3 extra Bash calls for a
  preprocess + second OCR pass + a validation run); negligible aggregate since
  most tasks touch no image.
- rollback_trigger: if R3 shows pass_rate flat/down AND any previously-passing
  OCR task (000206/000338/000536/001652) regresses T→F, revert the prompt block.
