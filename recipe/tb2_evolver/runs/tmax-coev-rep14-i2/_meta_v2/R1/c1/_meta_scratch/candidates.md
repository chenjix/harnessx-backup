# Candidates — R1 / c1

Assigned focus: `task_000015_89886d8d` (software_engineering) fails —
`final_pytest` accuracy 0.6592 < 0.98 threshold on a URL→JSONL migration
whose spec must be OCR'd from `/app/routing_schema.png`.

## Root-cause of the assigned task

- The task requires extracting a routing schema from an image via OCR
  (`tesseract`), then implementing `migrate.py` that transforms 2000 URLs.
- Tesseract runs at ~111 dpi ("Invalid resolution 0 dpi. Using 70 instead")
  on a small 800x400 image → all reads are garbled. The agent tried
  contrast/threshold/sharpen and multiple PSM modes but never resolved the
  spec cleanly, then **guessed** the mapping and stopped.
- The concrete implementation bug the guess produced: it mapped BOTH
  `product_id` AND `category` to the same path segment (`item_id`), e.g.
  `{"product_id":"9921","category":"9921",...}`, dropping the real
  `category` mapping. Result: ~34% of records wrong → 0.659 accuracy.
- Crucially: the agent had `/home/user/sample_urls.txt` (concrete example
  inputs) and produced output from them, but never **validated the semantic
  correctness** of its mapping — it only checked "the script runs and emits
  lines" and that an invalid route was skipped. Its self-check was
  checklist-shaped ("did I do step N?"), not output-correctness-shaped.

This is a harness/instruction gap, not pure model capability: the agent had
the information and the runtime to catch its own error, but lacked a
verification discipline that inspects the *actual output against the task's
own correctness criteria* before declaring done.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add an explicit **output-grounded verify-before-finish discipline** to the
system prompt: before stopping, re-derive the exact required output shape/
values from the task, inspect the actual produced artifact against that, and
when the task supplies concrete samples or checkable invariants, confirm they
hold — treat "the script runs" as necessary but not sufficient. Also add a
general "improve the signal before committing to a noisy extraction" clause
(e.g. up-scale low-DPI images before OCR; cross-check an extracted spec
against any provided examples) — phrased as strategy, no task literals.

- Tasks affected (>=2 distinct, same mechanism — declared done with
  runnable-but-semantically-wrong output and no output-grounded self-check):
  - `task_000015_89886d8d` — OCR schema guessed; `category` mis-mapped;
    accuracy 0.659 < 0.98.
  - `task_000264_ab8c7253` — recursive-CTE / query-plan task; agent did a
    "re-read task" pass but only ticked a checklist (✅ per step); the actual
    CSV output and EXPLAIN QUERY PLAN output both failed the verifier
    (`test_csv_output`, `test_query_plan_output`).
  - (supporting) `task_000111_cbada64a` — numeric result 2.5056 vs expected
    ~2.5997 (|Δ|=0.094 > 0.001); finished in 7 steps with no re-derivation /
    recompute cross-check.
- Signal: `final_pytest.passed=false` with content/accuracy assertions;
  `agent.finished=no_tool_calls`, `exit_reason=done` (agent believed it was
  finished). Not budget/loop failures — the agent committed early.
- Verified (body-quoted):
  - task_000015 step 31: "The OCR is garbled but I can infer the routing
    schema ... Let me create the migrate.py"; step 34 output shows
    `{"product_id":"9921","category":"9921",...}` — category duplicated from
    item_id. No step ever validates the mapping semantics against the sample.
  - task_000264 last assistant: "Let me re-read the task and verify
    everything is correct: 1. ... ✅ 2. ... ✅ ..." — a checklist pass, not an
    inspection of the emitted CSV/EXPLAIN content; both outputs failed.
- Why Instruction not Control: the correctness criterion is task-specific and
  only knowable from the natural-language task text (which output file, what
  columns, what accuracy). A mechanical processor cannot know what "correct"
  means for an arbitrary task, and there is no tool-return to post-process —
  the artifact is a file the agent wrote. The gap is that the agent doesn't
  *reach for* an output-grounded check, not that it lacks a capability. This
  is the playbook's "double-confirmation before exit" lever, kept
  agent-authored so the agent scopes the check to the task at hand.
- Why not Action: the agent already has Bash and can inspect any file /
  recompute any value; no new action space is missing.
- Retroactive check (A-corrective): yes — if an output-grounded verification
  pass had fired on task_000015, comparing its emitted records against the
  sample URLs and the OCR'd rules would have exposed the duplicated
  `category==product_id` mapping before committing; on task_000264, inspecting
  the actual CSV rows and EXPLAIN output (not a checklist) would have caught
  the content mismatch. The information needed was already in reach.

- expected_global_gain: targets the "runnable-but-wrong output, committed
  early" failing cluster (>=2-3 tasks across software_engineering /
  data_querying / scientific_computing). A verify-before-finish discipline is
  a broadly-transferable lever on this benchmark (the biggest single lever in
  the playbook) and helps any unseen content/accuracy task.
- regression_risk: low. Extra verification steps rarely break an
  already-correct solution; worst case is a few more Bash inspections. Risk of
  over-verifying and burning steps on tasks that finish fast is bounded by the
  existing TaskTimeReminder / step budget and the guidance to stop once the
  check passes. Two tasks in the round already hit `budget_exceeded`
  (000028, 000313) for unrelated reasons (missing deps / service wiring); the
  prompt keeps the check proportionate to avoid worsening those.
- cost_shift: modest increase (a handful of extra Bash inspection/recompute
  steps per task on average); acceptable given the accuracy-cluster upside.
