# Evolution Journal — tb21-coev-rep1-i1

## Round 1 — bound runaway context

<!-- journal:frontmatter
round: 1
timestamp: 2026-04-27T00:00:00Z
hypothesis_id: h_context_overflow_guard_v1
levers: [control]
predicted_affected: [regex-log, overfull-hbox, configure-git-webserver, large-scale-text-editing, protein-assembly, sam-cell-seg, llm-inference-batching-scheduler]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=3/18; +1/-1 gained=configure-git-webserver lost=headless-terminal; score 0.1667 >= incumbent(mean) 0.1667 - tol 0.0400
expected_global_gain: "Unblocks a 7+ task cluster that dies at step ~0/1 with exit_reason=error from an uncaught provider 400 (context overflow); any non-error continuation is strictly better than the current instant death"
regression_risk: "Very low — char caps (per-msg 120k, total 280k) sit far above every message seen on the 3 passing tasks (<=~5.4k chars); truncation preserves message count/roles/tool_call_id so track invariants hold"
cost_shift: "Neutral-to-down — previously-instant-death tasks now run (more tokens) but runaway 170k-344k-char turns are bounded, shrinking the request that actually leaves the harness"
rollback_trigger: "If R2 shows these 7 tasks still exit_reason=error at step ~0/1, the char budget is still above the server's real limit — lower total_char_budget/per_message_char_cap before abandoning the mechanism"
-->

### Why

R0 scored 3/18 (16.7%). A per-task trace scan (`.jsonl`, biggest-string per
line) found a 1:1 correlation: every `exit_reason=error` task has a single
`raw_assistant` message whose `/message/content` is a runaway 170k–344k-char
string. Model `Qwen/Qwen3.5-9B` rejects inputs over ~65536 tokens with a
400 BadRequest; the run loop does not wrap the provider call in try/except, so
that 400 propagates to `exit_reason=error` and kills the task at step ~0/1 —
before it does any recoverable work. The stock `CompactionProcessor` can't
help: its token counter (`rough_token_count`, cl100k_base) undercounts this
model's real tokenizer, and it summarises OLD messages while leaving the single
oversized recent turn intact. This is a harness deficiency (a missing size-
bounding mechanism), not a model-knowledge gap.

### Changes

- `processors/context_overflow_guard.py` — new `ContextOverflowGuard`
  (`MultiHookProcessor`, `on_step_start`, `_order=9` right after compaction).
  Char-based (not token-based, since the token counter undercounts): head/tail-
  truncates any message whose text `content` exceeds `per_message_char_cap`,
  then shrinks the largest older messages until the transcript fits
  `total_char_budget`. Truncation is in place — message count, roles, ordering,
  and `tool_call_id` links preserved.
- `config.yaml` — register `ContextOverflowGuard` via `file://` target
  (`per_message_char_cap=120000`, `total_char_budget=280000`, `keep_recent=4`,
  `min_message_chars=2000`), positioned after `CompactionProcessor`.

### Evidence

- regex-log: 6-line trace; line 4 `type=raw_assistant` `/message/content` =
  343,954 chars; trajectory frontmatter `steps: null` (never completed a step),
  `passed: false`.
- overfull-hbox: assistant content 218,307 chars, `exit=error`.
- configure-git-webserver: 249,882, `exit=error`.
- large-scale-text-editing: 207,337, `exit=error`.
- protein-assembly: 205,709, `exit=error`.
- sam-cell-seg: 175,527, `exit=error`.
- llm-inference-batching-scheduler: 172,511, `exit=error`.
- Passing tasks git-leak-recovery / headless-terminal / kv-store-grpc: max
  message content <= ~5.4k chars, `exit=done` — well below the caps, so
  structurally unaffected.

### Uncertainty

The char→token ratio is estimated (regex-log's 343,954 chars ≈ 65,536 tokens →
~5.25 chars/token); if the real ratio is lower, 280k chars could still exceed
the limit and the cluster stays red — rollback trigger above covers this by
lowering the budget. Also unresolved this round: write-compressor's malformed-
tool-call loop (`bash_tool() missing ... 'command'`) despite
`ParseRetryProcessor.max_consecutive_errors: 1` — different mechanism, deferred.

### needs_from_human

- Ideal fix would also wrap the provider `.complete()` call in the run loop
  with try/except to translate a 400 into a graceful `exit_reason` instead of
  `error`, but `harnessx/**` is read-only from this harness. Noting for a
  human: `runloop.py` ~line 419 provider call is unguarded against
  BadRequestError.

## Round 2 — repeatable act-loop keepalive

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-24T10:00:00Z
hypothesis_id: h_act_loop_keepalive_v1
levers: [control]
predicted_affected: [regex-log, large-scale-text-editing, db-wal-recovery, feal-linear-cryptanalysis, filter-js-from-html, adaptive-rejection-sampler, sam-cell-seg, train-fasttext, write-compressor, overfull-hbox, llm-inference-batching-scheduler]
cited_candidates: [C-002]
gating_outcome: accepted
gating_attribution: score=4/18; +1/-0 gained=count-dataset-tokens; score 0.2222 >= incumbent(mean) 0.1667 - tol 0.0400
expected_global_gain: "Converts the dominant terminal failure signature (episode ends on a large text-only assistant turn with no tool call) into a bounded continuation, giving the model up to 6 more chances to actually produce the required output file via Bash across ~10 of 15 failing tasks"
regression_risk: "The 3 passing tasks never hit the stall branch (their turns carry tool calls), so untouched; worst case a genuinely-finished prose turn gets extra nudges before stopping — cost only, mitigated by the SUCCESS sentinel escape hatch"
cost_shift: "Up modestly on previously-early-dying tasks (they now run more steps toward a real solution); bounded by max_reprompts=6 and existing max_steps/budget guards"
rollback_trigger: "If R3 shows these stall tasks still passed:false and now hit max_steps/budget_exceeded with repeated nudges but no filesystem progress, it's a capability gap — lower max_reprompts to 2 or revert; revert immediately if any previously-passing task regresses"
-->

### Why

R1's context-overflow guard fully resolved the `exit_reason=error` instant-death
cluster — every previously-dying task now runs many steps — yet the score stayed
3/18. A frontmatter + episode-JSONL sweep of R1 trajectories found a new dominant
terminal signature: failing tasks end on a **large text-only assistant turn with
no tool call** (94k–254k chars). The model rambles / plans in prose instead of
executing Bash, and the run loop treats that as "done". The stock
`CustomSelfVerifyProcessor` intercepts the first such turn — but only once per
task — so the second ramble ends the run with the required output file never
written. This is a harness give-up-after-one-nudge deficiency, not a
domain-knowledge gap.

### Changes

- `processors/act_loop_keepalive.py` — new `ActLoopKeepalive` (`MultiHookProcessor`,
  `_order=91`, right after the stock self-verify at 90). On `on_after_model`,
  detects a no-tool-call stall with non-empty content and, while `max_reprompts`
  budget remains, injects a synthetic keepalive tool call so the loop does not
  `break`, and queues a short generic user nudge ("act via Bash, or emit
  `SUCCESS: task complete.` to finish") delivered on the next `on_before_model`.
  Completion-sentinel escape hatch lets a genuinely-finished agent stop. Budget
  exhausted → yields unchanged so the loop terminates. Keepalive tool swallowed in
  `on_before_tool` (`approved=False` + `synthetic_result`), mirroring the stock
  keepalive contract.
- `config.yaml` — register `ActLoopKeepalive` via `file://` after
  `CustomSelfVerifyProcessor` (`max_reprompts=6`, `completion_marker=SUCCESS`,
  `min_content_chars=1`).

### Evidence

- regex-log (5 steps): episode ends on a 90,105-char text-only turn (no tool
  call); verifier got 4 of 9 expected dates. `passed:false`.
- large-scale-text-editing (2 steps, `n_output_tokens:0`): ends on a 254,265-char
  text-only turn; `apply_macros.vim` never written → FileNotFoundError.
- db-wal-recovery (13 steps): ends on a 104,933-char text-only turn;
  `/app/recovered.json` never written.
- feal (149k), filter-js-from-html (125k), adaptive-rejection-sampler (97k),
  sam-cell-seg (175k), train-fasttext (212k), write-compressor (214k),
  overfull-hbox (97k), llm-inference-batching (94k): biggest assistant turn
  carries NO tool call (`toolInMax=False`).
- Passing tasks configure-git-webserver (max 1.5k), git-leak-recovery (879),
  kv-store-grpc (659): every biggest turn carries a tool call (`toolInMax=True`).
- Root cause: `harnessx/core/runloop.py` ~L745 breaks on `finish_reason in
  ("end_turn","stop")` + no tool calls; `CustomSelfVerifyProcessor` docstring:
  "Fires at most once per task run. On the next no-tool-call turn it stays silent."

### Uncertainty

If the model rambles because it genuinely cannot solve the task (capability gap),
extra nudges just burn steps without flipping the task — detectable next round as
these tasks moving from `done` to `max_steps`/`budget_exceeded` with no filesystem
progress. Rollback trigger covers this. The keepalive/nudge mechanism itself is a
byte-for-byte reuse of the stock self-verify contract, validated by contract +
dry_fire + a standalone async unit exercise.

## Round 3 — output-artifact-aware keepalive

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-25T00:00:00Z
hypothesis_id: h_output_artifact_keepalive_v1
levers: [control]
predicted_affected: [regex-log, large-scale-text-editing, vulnerable-secret, feal-linear-cryptanalysis, protein-assembly, sam-cell-seg]
cited_candidates: [C-003]
gating_outcome: reverted
gating_attribution: score=2/18; +0/-2 lost=configure-git-webserver,count-dataset-tokens; score 0.1111 < incumbent(mean) 0.2222 - tol 0.0400 -> revert to R2
expected_global_gain: "Closes the largest R2 failing cluster — 6 tasks whose verifier fails its FIRST precondition (declared /app/<X> output file does not exist) — by re-surfacing the task's own declared output path(s) at the terminal-stall step so the agent commits its worked-out answer to the exact file instead of ending mid-prose"
regression_risk: "Low — strict superset of the accepted R2 keepalive; the 4 passing tasks never hit the terminal-stall branch (winning turns carry tool calls) so the nudge never fires for them; when no output path is extracted the nudge degrades byte-for-byte to R2's generic text, so behaviour is never worse than R2; SUCCESS completion escape hatch preserved"
cost_shift: "Neutral-to-slightly-down — same bounded max_reprompts=6 as R2; a specific 'write to /app/regex.txt now' nudge tends to end the loop sooner (agent commits the file, then legitimately SUCCEEDs) rather than adding turns"
rollback_trigger: "If R4 shows these 6 tasks still passed:false AND their verifier still fails on file-exists (FileNotFoundError) despite the targeted nudge, the agent is not acting on the nudge (capability/formatting gap, not a context gap) — revert to R2's ActLoopKeepalive; also revert immediately if any of the 4 R2-passing tasks regresses"
-->

### Why

R1's context-overflow guard and R2's repeatable act-loop keepalive both landed
and are load-bearing: previously instant-dying / one-prose-stall tasks now run
20-100 Bash steps (R2 = 4/18, up from 3/18). A full R2 trajectory sweep found
the NEW dominant terminal signature is neither instant death nor a single prose
stall — it is that **the output file the task explicitly names is never written
to its exact path**. Six of the fourteen failing tasks fail the verifier's very
first check ("output file exists") with a `FileNotFoundError` on a `/app/<X>`
path that the task description told the agent to create. The agent visibly loses
track of the deliverable mid-run. This is a harness deficiency: the R2 keepalive
nudges generically ("act via Bash, verify outputs"), but the specific required
path is dynamic per-task context the agent stops tracking — a Control hook can
extract it once at task start and re-inject it at the decisive step.

### Changes

- `processors/output_aware_keepalive.py` — new `OutputAwareKeepalive`
  (`MultiHookProcessor`, `_order=91`, same slot R2 occupied). Strict superset of
  R2's `ActLoopKeepalive`: identical repeatable-bounded keepalive contract
  (synthetic keepalive tool call on a no-tool-call terminal stall; one queued
  user nudge on next `on_before_model`; swallow synthetic call in
  `on_before_tool`; SUCCESS escape hatch; budget exhausted → yield unchanged).
  The one addition: `on_task_start` parses `task_description` for output paths
  adjacent (<=40 chars) to a produce-verb (save/write/create/output/produce/
  store/generate/place/put/emit), and the stall nudge names those exact paths
  ("You have not confirmed these required output files exist yet: /app/regex.txt.
  Write your current best result to that exact path via Bash now, then `ls -l`").
  When no path extracts, the nudge is byte-for-byte R2's generic text.
- `config.yaml` — replace the R2 `ActLoopKeepalive` file:// target with the R3
  `OutputAwareKeepalive` (same kwargs: max_reprompts=6, completion_marker=SUCCESS,
  min_content_chars=1). One-for-one swap keeps a single stall-interceptor and
  preserves the +1 message-insertion contract.

### Evidence

- regex-log: final segment `raw_assistant` turn = 113,297 chars of prose
  ("Let me think about this more carefully: 1. For the date pattern: ...") with
  `tool_calls=0`; `/app/regex.txt` never created; task text "Save your regex in
  /app/regex.txt"; grep count of that path in the episode = 14 (agent knew it,
  never wrote it). Verifier: `assert regex_file.exists()` fails.
- vulnerable-secret: L50 = 45,298-char text-only turn, L51 = 184-char
  "previous response was cut off ... XORing with 0x42", then `exit_reason=error`;
  `/app/results.txt` never written (`FileNotFoundError`).
- large-scale-text-editing: 5 steps then `FileNotFoundError: '/app/apply_macros.vim'`;
  path named explicitly in task text.
- feal-linear-cryptanalysis: ends `done` on an 11,377-char summary of a failed
  brute-force; the required `/app/plaintexts.txt` is never referenced in the
  whole trajectory (agent drifted onto /app/pairs.txt, /app/feal.c, /app/attack.c).
- protein-assembly (79 steps) / sam-cell-seg (50 steps): both end without the
  declared `/app/gblock.txt` / `/app/test_output.csv`; verifier fails file-exists
  first. (protein-assembly also hit a network wall for a PDB lookup — capability
  gap on correctness; the nudge may still yield a best-effort file.)
- Passing tasks configure-git-webserver / count-dataset-tokens / git-leak-recovery
  / kv-store-grpc: winning turns carry tool calls (R2 journal `toolInMax=True`),
  never enter the stall branch → nudge never fires.

### Uncertainty

If the agent rambles because it genuinely cannot solve the task (feal
cryptanalysis, protein PDB lookup behind blocked network), a targeted nudge
still produces no *correct* file — detectable next round as these tasks moving
to `FileNotFoundError`-cleared but content-assertion failures, or staying red
with no file. The three "answer-in-reasoning-but-uncommitted" tasks (regex-log,
large-scale-text-editing, vulnerable-secret) are the higher-confidence flips.
Extraction over-inclusion risk (listing an input path as an output to verify) is
benign — the nudge asks to confirm existence, and existing inputs pass that
check without being overwritten. Rollback trigger covers the capability-gap case.

## Round 5 — semantic loop breaker

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-24T12:00:00Z
hypothesis_id: h_semantic_loop_breaker_v1
levers: [control]
predicted_affected: [vulnerable-secret, protein-assembly, write-compressor, adaptive-rejection-sampler, llm-inference-batching-scheduler, sam-cell-seg, train-fasttext]
cited_candidates: [C-004]
gating_outcome: accepted
gating_attribution: score=3/18; +0/-0; score 0.1667 >= incumbent(mean) 0.1944 - tol 0.0400
expected_global_gain: "Closes the largest R4 failing cluster — 7 tasks that burn to max_steps stuck in a repeated-reasoning loop (identical assistant content x5..x48) the byte-hash loop detector cannot see, and never commit their declared output file. A content-repetition trigger re-anchors the agent on the deliverable and forces a strategy change."
regression_risk: "Low and bounded. The 3 passing tasks top out at maxrep=2, well below threshold=4, so the redirect never fires for them. Worst case a legitimately-progressing agent gets one extra user message (cost only, capped at max_interventions=3). Processor never swallows tool calls, never fabricates a keepalive, never blocks run termination; only appends a contract-safe +1 user message when prior role != user."
cost_shift: "Neutral-to-down — looping tasks currently burn to max_steps (118-120 Bash calls) with zero progress; a redirect that converges the spin earlier reduces wasted steps."
rollback_trigger: "If R6 shows the 7 tasks still passed:false AND runs still exhibit maxrep>=4 with no output file, the redirect is ignored (capability gap) — revert the processor. Revert immediately if any of configure-git-webserver / git-leak-recovery / kv-store-grpc regresses."
-->

### Why

R1's context-overflow guard and R2's act-loop keepalive are load-bearing: no
task instant-dies with `exit_reason=error` anymore and none ends on a single
prose stall — every R4 task runs 16-120 Bash steps. R3's output-artifact
keepalive was reverted (regressed 2 passing tasks). A full R4 trajectory sweep
(episode JSONL, all sessions per trial) found a NEW dominant terminal
signature: a **semantic loop**. The agent emits the *same reasoning content
verbatim* across many turns, re-running the same class of command, making no
progress until it exhausts max_steps — never writing the required output file.
The stock byte-hash loop detector misses this: tool-call IDs and exact command
bytes vary each iteration while the reasoning is identical. The model is often
self-aware ("I'm stuck in a loop repeating the same command") but cannot break
out. This is a harness deficiency (missing mechanical loop-break), not a
domain-knowledge gap.

### Changes

- `processors/semantic_loop_breaker.py` — new `SemanticLoopBreaker`
  (`MultiHookProcessor`, `_order=92`, right after the R2 keepalive at 91). On
  `on_after_model` it computes a normalised prefix signature of the assistant
  content and tracks it in a bounded rolling deque per run; when the same
  signature recurs `repeat_threshold`(=4) times within `window`(=8) it queues
  ONE bounded redirect (delivered on the next `on_before_model` as a
  contract-safe +1 user message when prior role != user). The redirect tells
  the agent to (1) write its current best answer to the required output path
  now and `ls -l` it, then (2) take a genuinely different next step. Bounded by
  `max_interventions`(=3); resets history after each fire so it only re-fires on
  a fresh run of repeats. Never touches tool_calls / never blocks run
  termination.
- `config.yaml` — register `SemanticLoopBreaker` via `file://` after
  `ActLoopKeepalive` (repeat_threshold=4, window=8, sig_chars=80,
  max_interventions=3, min_sig_chars=12).

### Evidence

- Max identical assistant-content-prefix count per run (across all sessions):
  PASSING configure-git-webserver=2, git-leak-recovery=2, kv-store-grpc=2;
  FAILING write-compressor=48, vulnerable-secret=41, protein-assembly=39,
  adaptive-rejection-sampler=32, llm-inference-batching=10, sam-cell-seg=5,
  train-fasttext=5, overfull-hbox=3. Clean separation at threshold 4.
- vulnerable-secret session `abdee88b…` steps 25-49: identical
  `"Let me analyze the disassembly more carefully. I see that: 1. At 401200…"`
  with identical objdump tool output each turn; `/app/results.txt` never written.
- protein-assembly `8f31106c…`: `"I'm stuck in a loop trying the same
  commands…"` (x8), `"The PDB API is consistently returning 404 errors…"` (x39).
- adaptive-rejection-sampler `81b2107a…`: `"Let me try a different approach.
  The issue is that the sample_envelope…"` (x32).
- Standalone async exercise: redirect fires exactly on the 4th repeat as a +1
  user message, does not re-fire on the next two turns, and a 2-repeat
  passing-style pattern never fires.

### Uncertainty

For pure-capability tasks (feal-style cryptanalysis, PDB lookup behind a
blocked network) the redirect frees wasted steps and may produce a best-effort
file that clears the file-exists precondition, but will not by itself flip
content-correctness. The higher-confidence flips are the "answer-in-reasoning-
but-uncommitted" loop tasks (vulnerable-secret, adaptive-rejection-sampler,
protein-assembly file-exists). Rollback trigger covers the ignored-redirect
case. Validated with canonicalize + dry_fire + contract + literals(0 findings)
+ a standalone async unit exercise.

## Round 6 — runaway-reasoning breaker

<!-- journal:frontmatter
round: 6
timestamp: 2026-08-24T14:00:00Z
hypothesis_id: h_runaway_reasoning_breaker_v1
levers: [control]
predicted_affected: [regex-log, large-scale-text-editing, protein-assembly, db-wal-recovery, llm-inference-batching-scheduler, vulnerable-secret, sam-cell-seg]
cited_candidates: [C-005]
gating_outcome: reverted
gating_attribution: score=2/18; +0/-1 lost=configure-git-webserver; score 0.1111 < incumbent(mean) 0.1944 - tol 0.0400 -> revert to R2
expected_global_gain: "Closes the largest remaining harness-fixable failing cluster (7 tasks) that spirals in length-truncated no-tool-call reasoning — a shape both existing keepalive mechanisms structurally miss. Converts '0 Bash calls, dies at context cap' into a bounded push to act; several tasks have the answer in-reasoning and just need to be forced to commit it via Bash."
regression_risk: "Low. Passing git-leak-recovery (max assistant turn 986 chars) and kv-store-grpc (742) never produce a large no-tool-call turn above the 20k trigger, so it never fires. configure-git-webserver (passing) does emit big no-tool-call turns but recovers to Bash on its own; the redirect only tells it to do what it already does, capped at 4 firings. Net +0 insertion (rewrites the run loop's own continuation nudge), no history growth."
cost_shift: "Neutral-to-down. These 7 tasks currently burn 150k-3M cumulative tokens generating 65k-token prose blobs; forcing an early switch to short Bash commands cuts per-step generation cost and ends wasted reasoning sooner."
rollback_trigger: "If R7 shows these 7 tasks still passed:false AND still exhibit a length-truncated no-tool-call turn with no subsequent Bash progress after the redirect, the redirect is ignored (capability gap) — revert the processor. Revert immediately if configure-git-webserver / git-leak-recovery / kv-store-grpc regresses."
-->

### Why

R1's context-overflow guard, R2's act-loop keepalive, and R5's semantic loop
breaker are all load-bearing, but the score has been flat at 3/18 across R4 and
R5. A full R5 trajectory sweep (episode JSONL + trace cumulative_tokens) found a
dominant terminal signature none of the three existing mechanisms can see: a
runaway reasoning spiral. On 7 failing tasks the model emits one or more
enormous (50k-320k char) assistant turns that contain zero tool calls and are
cut off at the 65,536-token output cap (finish_reason == "length"). It then
repeats — regex-log made literally 0 Bash calls across its entire 900s /
262k-token run, cumulative tokens climbing 65536 -> 131072 -> 196608 -> 262144
until it produced nothing at the context ceiling. Worse, the run loop itself
(harnessx/core/runloop.py L726-738) feeds the spiral: on a length-truncated
no-tool-call turn it injects "Your previous response was cut off by the token
limit. Please continue from where you left off." — instructing the model to
generate more of the same runaway prose. The R2 keepalive only fires on
end_turn/stop (not length); the R5 loop breaker needs 4 identical prefixes
(these vary). This is a harness deficiency — a missing mechanical redirect on a
length-truncated no-tool-call turn — not a domain-knowledge gap (the model runs
Bash 20-120x on other tasks).

### Changes

- processors/runaway_reasoning_breaker.py — new RunawayReasoningBreaker
  (MultiHookProcessor, _order=93, after the R5 loop breaker at 92). On
  on_after_model it detects a runaway turn (finish_reason length OR a very large
  60k+ char turn, AND no tool calls, AND content at least 20k chars) and arms a
  pending redirect, bounded to max_interventions 4 per run. On on_before_model
  it delivers the redirect by rewriting the run loop's own "continue from where
  you left off" user message in place (net +0) into a forceful "STOP reasoning,
  emit ONE short Bash command now" directive — guarded on the stock-nudge
  markers so a genuine task/user turn is never clobbered (kept pending for a
  safe step instead). Falls back to a contract-safe +1 append only when no stock
  nudge is present.
- config.yaml — register RunawayReasoningBreaker via file:// after
  SemanticLoopBreaker (max_interventions=4, min_content_chars=20000,
  trigger_on_large_content=true, large_content_chars=60000).

### Evidence

- regex-log session fe98cadb: 5 assistant turns of 66,297 / 321,454 / 223,619 /
  100,429 chars, 0 Bash calls in the whole session; trace step_end
  cumulative_tokens = 65536 -> 131072 -> 196608 -> 262144 (each call hit the
  65,536 output cap and truncated). /app/regex.txt never created.
- large-scale-text-editing b99e5cec: 2 no-tool-call turns totalling 309,530
  chars, 6 steps, /app/apply_macros.vim never written.
- protein-assembly: 2 no-tool-call turns totalling 312,199 chars.
- db-wal-recovery (162,789), llm-inference-batching-scheduler (153,250),
  vulnerable-secret (152,745), sam-cell-seg (123,809): each has a no-tool-call
  assistant turn above 100k chars.
- Passing git-leak-recovery max assistant turn 986 chars, kv-store-grpc 742 —
  well below the 20k trigger; configure-git-webserver emits 2 big turns
  (133,978 / 255,453) but the subsequent assistant turns carry tool calls
  (recovers on its own).
- Root cause: harnessx/core/runloop.py L726-738 injects the "continue from where
  you left off" nudge on finish_reason length + no tool calls.

### Uncertainty

For pure-capability tasks (protein PDB lookup behind blocked network) forcing
Bash action frees wasted budget and may produce a best-effort file that clears
the file-exists precondition, but won't by itself flip content-correctness. The
higher-confidence flips are the answer-in-reasoning-but-never-committed tasks
(regex-log, large-scale-text-editing, vulnerable-secret). Rollback trigger
covers the ignored-redirect case. Validated with canonicalize + dry_fire +
contract (0 violations) + literals (0 findings) + a standalone async exercise
confirming: arms on a length-truncated giant no-tool-call turn, rewrites the
stock nudge net +0, does not fire on a small turn, and never clobbers a genuine
user task turn.
