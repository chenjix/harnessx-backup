# R2 Candidates

## Candidate C-002 — repeatable bounded act-loop keepalive

- **Lens / Lever / Intent**: behavioral-loop-termination / control / close-failing-cluster
- **Signal**: In R1 (config with the R1 context guard), the score stayed 3/18.
  The context-overflow deaths (`exit_reason=error` at step ~0/1) were fully
  resolved — every previously-instant-death task now runs many steps. But the
  failing tasks now share a new, dominant terminal signature: the episode ends
  on a **large text-only assistant turn with no tool call**.

- **Verified body evidence** (biggest assistant message per task, whether it
  carried a tool call):
  - regex-log (5 steps): episode ends on a 90,105-char text-only turn (no tool
    call). `passed:false`, verifier got only 4 of 9 expected dates.
  - large-scale-text-editing (2 steps, `n_output_tokens:0`): ends on a
    254,265-char text-only turn; `apply_macros.vim` never written →
    `FileNotFoundError`.
  - db-wal-recovery (13 steps): ends on a 104,933-char text-only turn;
    `/app/recovered.json` never written.
  - feal-linear-cryptanalysis (max text-only 149k), filter-js-from-html (125k),
    adaptive-rejection-sampler (97k), sam-cell-seg (175k), train-fasttext
    (212k), write-compressor (214k), overfull-hbox (97k), llm-inference-
    batching (94k) — all show `toolInMax=False` (their single biggest assistant
    turn carries NO tool call).
  - Passing tasks configure-git-webserver (max 1.5k), git-leak-recovery (879),
    kv-store-grpc (659) — every biggest turn carries a tool call
    (`toolInMax=True`), small messages.

- **Root cause (harness)**: `harnessx/core/runloop.py` ~L745 treats any
  `finish_reason in ("end_turn","stop")` + no tool calls as a completion and
  `break`s. The stock `benchmarks/terminal_bench_2/harness.py::CustomSelfVerify
  Processor` intercepts this but **only once per task** ("Fires at most once per
  task run. On the next no-tool-call turn it stays silent."). A model that
  rambles in prose more than once loses the run: the first ramble spends the
  one-shot keepalive, the second ends the episode with the required output file
  never produced. This is a harness deficiency (give-up-after-one-nudge), not a
  domain-knowledge gap.

- **Intervention**: new `processors/act_loop_keepalive.py::ActLoopKeepalive`
  (`_order=91`, just after the stock self-verify at 90). On `on_after_model`,
  detects a no-tool-call stall with non-empty content and, while a bounded
  budget remains (`max_reprompts=6`), injects a synthetic keepalive tool call
  (so the loop does not `break`) and queues a short generic user nudge
  ("act via Bash, or emit `SUCCESS: task complete.` to finish") delivered on the
  next `on_before_model`. If the turn already contains the completion sentinel,
  it lets the agent stop. Budget exhausted → yields unchanged so the loop stops
  normally (no infinite loop). Keepalive tool is swallowed in `on_before_tool`
  (`approved=False` + `synthetic_result`), mirroring the stock keepalive
  contract.

- **Retroactive check (would-this-have-helped)**: For regex-log, large-scale-
  text-editing, db-wal-recovery and the other stall-terminated tasks, the final
  text-only turn would have been converted into a continuation nudge instead of
  a run-ending `break`, giving the model 6 more chances to actually write the
  required output file via Bash. Tasks that already end by calling Bash and
  producing files (the 3 passers) never hit the stall branch, so they are
  untouched.

- **Why control not instruction**: the deficiency is a *loop-termination policy*
  (the harness gives up after one nudge), not missing guidance. An instruction/
  template change cannot stop the run loop from breaking on the 2nd text-only
  turn; only a processor that keeps the response "non-terminal" (via the
  keepalive tool-call) can. Reusing the exact keepalive mechanism the stock
  self-verify already relies on keeps this contract-safe.

- **expected_global_gain**: The stall-on-text-only signature is the terminal
  event for ~10 of the 15 failing tasks. Even a fraction of these flipping is a
  meaningful move from 3/18. Generalizes to any TB2 task where the model
  reasons in prose before acting.

- **regression_risk**: Low. The three passing tasks never trigger the stall
  branch (their turns carry tool calls). Worst realistic case: a task genuinely
  finished after a prose turn gets up to 6 extra nudges before stopping — this
  only adds cost, and the completion-sentinel escape hatch lets a well-behaved
  agent stop immediately by ending with `SUCCESS: task complete.`. The
  `max_reprompts=6` bound guarantees termination.

- **cost_shift**: Up modestly on tasks that previously died early (they now run
  more steps toward a real solution) — but those were 0-reward anyway, so the
  spend buys pass-rate. Bounded by `max_reprompts` and by the existing
  `max_steps`/budget guards, so no runaway.

- **rollback_trigger**: If R3 shows these stall tasks still `passed:false` AND
  now burning to `max_steps`/`budget_exceeded` with repeated nudges but no
  progress, the model can't solve them regardless (capability gap) — lower
  `max_reprompts` to 2 or revert. If any previously-passing task regresses,
  revert immediately.
