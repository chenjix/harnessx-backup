# Candidates — R7 (tmax-coev-rep12-i3)

Incumbent = R3 config (R5 reverted back to R3). The r6 trajectory draw scored
11/50. Behaviour breakdown across the 50 tasks:

- 14 `budget_exceeded` at 80 steps
- 26 `done` but reward 0 (correctness / output-location gaps)
- 11 pass, all `done`, mostly short (<=31 steps; only 2 passers >31)

The clearest **harness-shaped** cluster is a max_tokens-truncation
self-reinforcing loop (distinct from R2's identical-command loop and R5's
tool-block, which was reverted). Correctness-gap `done`-but-wrong tasks are
model capability gaps and are logged, not patched.

## Candidate C-007
[lens: failure | lever: control | intent: corrective]

Add a step_start processor (`TruncationLoopCompactor`) that collapses a run of
>=3 consecutive near-identical **truncated** assistant narration turns (plus
the run loop's passive "please continue" nudges between them) into a single
trimmed turn + one actionable "emit ONE short Bash command" directive, so the
model stops seeing — and reproducing — its own repetition wall.

- Tasks affected (failing, same mechanism): task_000998_4d9c7852,
  task_001214_f44c0aa2, task_001028_5bc8bc70, task_000908_170e5e4e,
  task_001267_0acfd3a0, task_000710_fa628bd4, task_000032_3fb303f6,
  task_000796_828a72cf, task_000437_8450eef7
- Signal: per-task count of the run loop's passive nudge "Your previous
  response was cut off by the token limit. Please continue" is 8-23 on the top
  failing tasks and 0 on all 8 short passers. The truncated assistant turns are
  byte-near-identical (measured: the same 60-char assistant prefix repeated
  10-13x in one run).
- Verified (Read):
  - `task_000998_4d9c7852.messages.json` steps 2-34: assistant content
    truncated to ~1964 chars every turn (finish_reason=length), prefix
    "The user is right - I've been stuck in a loop. The files are..." repeated
    x10, each followed by the passive "cut off by the token limit. Please
    continue" user nudge; run ends `budget_exceeded`.
  - `task_001214_f44c0aa2.messages.json`: prefix "The user is right - I've been
    stuck in a loop. Let me take a..." x11; passive-nudge count 21;
    `budget_exceeded`.
  - `task_000908_170e5e4e.messages.json`: prefix "...Let me just w..." x13;
    passive-nudge count 18; `budget_exceeded`.
  - `task_001028_5bc8bc70.messages.json`: prefix "...loop of analysis wi..."
    x11; passive-nudge count 18.
- Why Control not Configuration: the only tunable knob nearby is
  `LengthTruncationRecoveryProcessor.repeat_threshold`, but that processor is
  already firing (threshold=2) and its `on_before_model` nudge is *ephemeral*
  (never written to `state.raw_messages`), so the duplicate wall keeps growing
  regardless of the knob. The fix needs a structural rewrite of the assembled
  window at `on_step_start` — which the run loop persists via its auto-boundary
  path — not a parameter change.
- Why Control not Instruction: the system prompt already tells the model to be
  concise and act; the model *cannot* self-limit its output under the hard
  `max_tokens=4096` cap (set outside config, unchangeable here). No prompt rule
  removes the accumulated repetition wall that primes the loop — only a
  mechanical context rewrite does.
- Why not re-proposing R5 (`h_repeat_command_blocker_v1`, reverted): different
  lever mechanics and different trigger. R5 intercepted **tool calls**
  (`on_before_tool`, approved=False) on byte-identical *commands* and was
  reverted for disrupting legitimate iteration. C-007 never touches tool calls
  or blocks execution; it only collapses redundant *truncated narration* turns
  (no tool_calls) in the assembled context. Distinct hypothesis id
  (`h_truncation_loop_compactor_v1`), distinct hook, distinct target.
- Retroactive check (A-corrective): yes — on task_000998 the model DID make
  progress on the turns where it emitted a Bash call (steps 8/20/24/36); it was
  starved of those turns because 4096 output tokens were consumed by
  reproducing the repetition wall. Collapsing the wall + a "one short command"
  directive at each step gives the model a clean context and a fair shot at
  reaching the tool call before the cap, instead of a guaranteed budget-burn.
  Necessary-not-sufficient: some of these tasks also have a correctness gap, so
  the guaranteed effect is converting budget-burned-in-a-loop into a fair
  scored attempt at lower cost.
- expected_global_gain: recovers dozens of wasted steps across a 9-task
  truncation-loop cluster (5 domains) that currently die at `budget_exceeded`
  or `done`-after-thrash; generalizes to any future task that falls into the
  max_tokens loop (structural trigger, no task literals).
- regression_risk: near-zero. Fires only on >=3 consecutive near-identical
  truncated (>=200-char, no-tool-call) assistant turns — a shape that appears
  in ZERO of the 8 short passing tasks (all have passive-nudge count 0) and in
  none of the passing tasks generally. It never blocks, drops, or fabricates a
  tool call/result; it only compresses self-narration the model already
  emitted, and it preserves the first narration turn so no genuine reasoning is
  lost. The two long passers (task_000097 step-count 65, task_000628 74) have
  passive-nudge counts 1 and 0 respectively — below the min_run=3 trigger.
- cost_shift: net down. Each collapsed run removes the growing wall of
  ~4096-token truncated turns from the assembled context (smaller prompts) and
  shortens the loop so tasks stop burning the full 80-step budget. Added text
  is one short directive per collapse.
- rollback_trigger: if R8 shows any previously-passing task regressing T->F
  with a `[harness: the previous N turns hit the output token limit...]`
  collapse note in its trace disrupting legitimate work, or pass_rate drops vs
  R3 incumbent mean, revert this processor (drop the registration; keep the R3
  pipeline otherwise).

## Skipped (model capability gaps — no harness fix)

The 26 `done`-but-wrong tasks fail their hidden verifier on content
correctness (e.g. `task_000908` produced an empty `optimized_results.json`
instead of the 3 expected author-pair rows; `task_001201` RPN parser value
error). These require domain reasoning the harness cannot supply and are not
patched. Logged in the journal.
