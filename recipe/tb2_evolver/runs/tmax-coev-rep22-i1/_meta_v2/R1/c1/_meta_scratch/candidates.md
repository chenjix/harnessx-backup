# Candidates

## Candidate C-001
[lens: failure | lever: configuration | intent: corrective]

Give `CompactionProcessor` a `summarize_prompt_template` that forbids
meta-reasoning preamble and orders the summariser to preserve concrete
artifacts (file contents, extracted/OCR data, exact commands, paths,
schema mappings, test failures) verbatim instead of collapsing them to
2-4 vague sentences.

- Tasks affected (assigned): task_000015_89886d8d
- Tasks affected (same mechanism, cluster): task_000010_644ab1c2,
  task_000118_3043e92d, task_000264_ab8c7253, task_000958_4bb2b05d,
  task_001032_1adaccb9, task_001321_658ce4a8
- Signal: 7 of the 8 tasks whose `messages.json` contains an
  `[Earlier conversation summary: ...]` / `[PostCompaction]` marker
  ended with `reward=0`. The lone survivor (task_001701) is the
  exception. The summariser output on every failing task opens with a
  verbose "Thinking Process: 1. Analyze the Request... Constraints:
  2-4 sentences" meta-preamble and then compresses the whole working
  state into 2-4 sentences.
- Verified (Read):
  - task_000015_89886d8d — pre-compaction trace (steps 0-15, oh_runs
    jsonl) shows the agent DID successfully OCR `/app/routing_schema.png`
    and had the real schema in context (routes → `product_id`,
    `category`, `order`, `session_token`, `traffic_source`,
    `account_id`, `ui_theme`, `notifications`). The step-1 compaction
    summary (msg[1]) discarded that extracted schema and recorded only
    "the assistant switched to inferring the schema from provided
    sample URLs". Post-compaction the agent never recovers the real
    schema and thrashes on `test_parser.py` (rewritten at steps
    6,10,14,18,22,24,32,36,40,50); final verifier fails on the
    inferred-schema URL-decoding semantics.
  - task_000264_ab8c7253 — msg summary reduces a Recursive-CTE SQL task
    to "Write an SQLite query using a Recursive CTE ... filter for 3+
    subordinates, sort by" (truncated) — the actual query text the
    agent had built was dropped.
  - task_000118_3043e92d — summary reduces a disk-usage daemon task to
    a prose paraphrase; the concrete script path/thresholds the agent
    had established are lost.
  - task_000010_644ab1c2 — summary paraphrases a mock-API/socat
    port-forwarding debug session; concrete port/command state lost,
    task then `budget_exceeded`.
- Why Configuration not Control/Instruction: the pipeline already has a
  `CompactionProcessor` with a first-class `summarize_prompt_template`
  constructor knob that is currently unset (falls back to the built-in
  "Summarize ... in 2-4 sentences" prompt). The mechanism (evict +
  summarise + re-inject) is correct; only the *summary quality* is the
  defect. A new Control processor would duplicate machinery that
  already exists; an Instruction/template edit to the agent prompt
  cannot influence the separate summariser sub-harness that produces
  these lossy blobs. The narrowest correct lever is the existing knob.
- Retroactive check (A-corrective): yes — had the summariser been told
  to preserve extracted data verbatim, task_000015's OCR-derived schema
  (and task_000264's CTE query, task_000118's script spec) would have
  survived compaction and remained in context at the decisive
  post-compaction steps, so the agent would not have reverted to a
  guessed schema. The URL-decode edge is downstream, but keeping the
  real schema (which encodes the intended value handling) is the
  upstream unblock.
- expected_global_gain: targets the compaction-loss failing cluster
  (7 failing tasks share the marker); generalises because the fix is
  content-agnostic — any task that extracts reference artifacts before
  compaction benefits.
- regression_risk: a richer summary is longer, so a compacted context
  is slightly larger than before; capped by keeping compaction
  triggers unchanged and instructing brevity for non-artifact chatter.
  Risk to already-passing tasks is low: only 8/50 tasks compact at all,
  and the one passing compacted task (task_001701) is unaffected in
  kind (a better-preserved summary cannot hurt a task that already
  succeeds through compaction).
- cost_shift: modest increase in summariser output tokens and in the
  size of the re-injected summary message on the ~16% of tasks that
  compact; negligible on the majority that never trigger compaction.
