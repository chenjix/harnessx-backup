## Round 6 — runaway-reasoning breaker

<!-- journal:frontmatter
round: 6
timestamp: 2026-08-24T14:00:00Z
hypothesis_id: h_runaway_reasoning_breaker_v1
levers: [control]
predicted_affected: [regex-log, large-scale-text-editing, protein-assembly, db-wal-recovery, llm-inference-batching-scheduler, vulnerable-secret, sam-cell-seg]
cited_candidates: [C-005]
gating_outcome: pending
gating_attribution: pending
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
