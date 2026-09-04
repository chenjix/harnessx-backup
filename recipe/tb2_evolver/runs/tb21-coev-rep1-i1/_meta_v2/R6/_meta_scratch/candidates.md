# Candidates — R6

## Candidate C-005
[lens: failure | lever: control | intent: corrective]

Intercept a length-truncated no-tool-call reasoning turn and rewrite the run
loop's counterproductive "continue from where you left off" nudge into a
forceful "STOP reasoning, emit ONE short Bash command now" redirect.

- Tasks affected: regex-log, large-scale-text-editing, protein-assembly,
  db-wal-recovery, llm-inference-batching-scheduler, vulnerable-secret,
  sam-cell-seg (7 distinct failing tasks with the same mechanism).
- Signal: `agent_result.n_output_tokens`/trace `cumulative_tokens` climb in
  exact 65,536-token (max-output-cap) increments across steps; per-run episode
  JSONL shows one or more assistant turns of 50k–320k chars with **zero
  tool_calls**; run loop trace shows only `ContextOverflowGuard` firing (never
  `ActLoopKeepalive` / `SemanticLoopBreaker`); verifier fails on the declared
  `/app/<X>` output file never existing.
- Verified (Read):
  - regex-log session `fe98cadb…`: FIVE assistant turns of 66,297 / 321,454 /
    223,619 / 100,429 chars, **0 Bash calls in the entire session**; trace
    `step_end cumulative_tokens` = 65536 → 131072 → 196608 → 262144 (each model
    call generated exactly the 65,536 output-token cap, truncated). First
    assistant turn begins `"Let me break down the requirements:"`, later turns
    begin `"The user is asking me to continue from where I left off…"` — the
    prefixes differ, so the R5 loop breaker's 4×-prefix trigger never fires.
    `/app/regex.txt` never created (verifier: `assert regex_file.exists()`).
  - large-scale-text-editing `b99e5cec…`: 2 no-tool-call turns totalling 309,530
    chars; ends after 6 steps, `/app/apply_macros.vim` never written.
  - protein-assembly `ydeLzop`: 2 no-tool-call turns totalling 312,199 chars.
  - db-wal-recovery / llm-inference-batching-scheduler / vulnerable-secret /
    sam-cell-seg: each has ≥1 no-tool-call assistant turn of 123k–162k chars.
  - Root cause in harness: `harnessx/core/runloop.py` L726–738 — on
    `finish_reason=="length"` with no tool calls it injects the user message
    *"Your previous response was cut off by the token limit. Please continue
    from where you left off."*, which instructs the model to keep generating
    the same runaway prose. This candidate rewrites that exact message.
- Why Control not Instruction: the failure is mechanical, not a knowledge gap —
  the model IS capable of running Bash (it does so 20–120× on other tasks) and
  the run loop is actively feeding the spiral with a "continue" nudge. A
  system-prompt rule cannot intercept and neutralise a per-turn message the run
  loop injects mid-run; only an `on_before_model` Control hook can rewrite that
  tail message when `finish_reason=="length"`. No tool output to post-process,
  so it is not Action either.
- Why Control not Configuration: no existing knob controls the run loop's
  length-truncation continuation text, and neither the R2 keepalive
  (`end_turn`/`stop` trigger) nor the R5 loop breaker (repeated-prefix trigger)
  fires on this `finish_reason=="length"` shape — a new orthogonal hook is
  required, not a re-parameterisation.
- Retroactive check (A-corrective): yes — regex-log spent its entire 900s /
  262k-token budget on prose and made 0 Bash calls; had the very first
  length-truncated turn been answered with "stop reasoning, emit ONE Bash
  command now" (net +0 rewrite of the stock nudge, bounded to 4 firings)
  instead of "continue from where you left off", the model — demonstrably
  Bash-capable — would have started executing and had the remaining budget to
  write `/app/regex.txt`. Same logic on the other 6.
- expected_global_gain: closes the largest remaining harness-fixable failing
  cluster (7 tasks) — the runaway length-truncation spiral that both existing
  keepalive mechanisms structurally miss. Converts "0 Bash calls, dies at
  context cap" into a bounded push toward action; several of these tasks
  (regex-log, large-scale-text-editing, vulnerable-secret) have their answer in
  reasoning and just need to be forced to commit it via Bash.
- regression_risk: low. The 3 passing tasks — git-leak-recovery (max assistant
  turn 986 chars) and kv-store-grpc (742 chars) — never produce a >20k-char
  no-tool-call turn, so the trigger never fires for them.
  configure-git-webserver (passing) DOES emit 2 big no-tool-call turns (134k /
  255k chars) but recovers to Bash on its own; the redirect merely tells it to
  do exactly what it already does (stop reasoning, run a command) and is capped
  at 4 firings, so it cannot destabilise a recovering agent. Insertion is net
  +0 in the common case (rewrites the stock nudge) — no message-count growth,
  no history edits beyond the tail user message.
- cost_shift: neutral-to-down. Today these 7 tasks burn 150k–3M cumulative
  tokens generating 65k-token prose blobs; forcing an early switch to short
  Bash commands sharply reduces the giant-generation cost per step and ends
  wasted reasoning sooner.
- rollback_trigger: if R7 shows these 7 tasks still `passed:false` AND still
  exhibit ≥1 length-truncated no-tool-call turn with 0 subsequent Bash progress
  after the redirect, the model is ignoring the redirect (capability gap) —
  revert the processor. Revert immediately if any of configure-git-webserver /
  git-leak-recovery / kv-store-grpc regresses.
