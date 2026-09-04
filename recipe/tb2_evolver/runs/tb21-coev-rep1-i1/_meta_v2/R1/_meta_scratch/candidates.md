# Candidates — R1 (tb21-coev-rep1-i1)

Baseline R0: 3/18 pass (16.7%). Model `Qwen/Qwen3.5-9B`, real server input
limit ~65536 tokens (rejects larger with 400 BadRequest). The provider call in
the run loop is not wrapped in try/except, so a 400 kills the task with
`exit_reason=error`.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `ContextOverflowGuard` `before-model`-adjacent processor (on `on_step_start`)
that head/tail-truncates any single oversized message `content` and enforces a
total transcript character budget, so requests stay under the model's real
context window instead of 400-ing uncaught.

- Tasks affected (same mechanism — runaway single-turn `content` → context
  overflow → uncaught 400 → `exit_reason=error`):
  regex-log, overfull-hbox, configure-git-webserver, large-scale-text-editing,
  protein-assembly, sam-cell-seg, llm-inference-batching-scheduler
  (7 distinct tasks; adaptive-rejection-sampler shares the same shape at 67k
  chars and is likely also helped).
- Signal: trace `exit_reason=error` correlates 1:1 with a single `raw_assistant`
  message whose `/message/content` string is 170k–344k chars. Passing tasks
  (git-leak-recovery, headless-terminal, kv-store-grpc) all have max message
  content <= ~5.4k chars and `exit_reason=done`.
- Verified (Read of per-task trace `.jsonl`, biggest-string scan):
  - regex-log: 6 trace lines total; line 4 `type=raw_assistant`
    `/message/content` = **343,954 chars**; run dies at step ~0/1 with
    `exit=error` (`steps: null` in the trajectory frontmatter — never completed
    a step).
  - overfull-hbox: `raw_assistant /message/content` = **218,307**, `exit=error`.
  - configure-git-webserver: **249,882**, `exit=error`.
  - large-scale-text-editing: **207,337**, `exit=error`.
  - protein-assembly: **205,709**, `exit=error`.
  - sam-cell-seg: **175,527**, `exit=error`.
  - llm-inference-batching-scheduler: **172,511**, `exit=error`.
  All seven: the single largest string in the entire trace is the assistant
  `content`, and the run ends `exit=error` — the uncaught overflow shape.
- Why Control not Configuration: the failure is a *missing mechanism*, not a
  mis-tuned knob. `CompactionProcessor.token_threshold` cannot be lowered to fix
  this: (a) it summarises OLD messages and leaves the single oversized recent
  turn intact, and (b) its token counter (`rough_token_count`, cl100k_base)
  materially undercounts this model's real tokenizer and ignores non-`content`
  fields, so no token threshold reliably prevents the 400. A char-based per-
  message + total-budget guard is a new mechanism, hence Control.
- Why Control not Instruction: a prompt rule ("don't emit huge single turns")
  cannot *guarantee* the request stays under the hard limit — the model already
  produced 340k-char turns despite the static prompt. Only a mechanical cap on
  the transcript that actually leaves the harness prevents the uncaught 400.
- Retroactive check (A-corrective): yes — had the transcript been bounded below
  the server limit at the decisive step, the provider call would have returned
  instead of 400-ing, the run would have continued past step 0/1, and the agent
  would have had the normal number of steps to solve the task (these tasks died
  before doing any real work, so any non-error continuation is strictly better).
- expected_global_gain: unblocks a 7+ task failing cluster that currently dies
  at step ~0/1 with zero chance of passing. Even a fraction converting to
  passes is a large move off 16.7%.
- regression_risk: very low. Caps (per-msg 120k chars ≈ ~23k tokens; total 280k
  chars ≈ ~53k tokens) sit far above every message seen on passing tasks
  (<= ~5.4k chars) and far above normal tool output, so passing clusters are
  structurally untouched. Truncation preserves message count, roles, and
  `tool_call_id` links (raw/effective track invariant holds), and only fires on
  transcripts that would otherwise 400. Head+tail excerpting keeps both ends of
  a truncated turn readable.
- cost_shift: neutral-to-down. Tasks that previously died instantly now run
  (slightly more tokens), but bounding runaway 170k–344k-char turns *reduces*
  per-request input size versus the (rejected) unbounded request.

Rollback trigger: if replay/next round shows these 7 tasks still `exit=error`
at step ~0/1, the char budget is still above the real limit — lower
`total_char_budget` / `per_message_char_cap` before abandoning the mechanism.
