# Evolve journal — tmax-coev-rep12-i3

## Round 1 — fix compaction threshold for 64k model

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-23T10:00:00Z
hypothesis_id: h_compaction_64k_v1
levers: [configuration]
predicted_affected: [task_001044_45c70cf1, task_000444_250abeda]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=11/50; +2/-2 gained=task_000628_dcc7eb30,task_000903_7604c91e lost=task_000437_8450eef7,task_001267_0acfd3a0; score 0.2200 >= incumbent(mean) 0.2200 - tol 0.0400
expected_global_gain: "Eliminates 2 exit_reason=error crashes (structural 0-rewards) and prevents context-overflow 400s on the long-context cluster (task_001028 climbing toward the ceiling)"
regression_risk: "Near-zero: all 11 passing tasks peak <13k rough tokens, far below the new 40000 threshold, so compaction never fires for them — identical behaviour"
cost_shift: "Flat to slightly down: compaction shrinks the re-sent prompt on long tasks; no effect on short tasks"
rollback_trigger: "If pass_rate drops vs R0, or a previously-passing task regresses to exit_reason!=done with a compaction boundary in its trace, revert to token_threshold=140000"
-->

### Why

Baseline R0 = 11/50 (22%). Two tasks (task_001044_45c70cf1,
task_000444_250abeda) came back with `status=agent_error` /
`exit_reason=error`. Their episode traces both end with a hard
`BadRequestError: 400 — This model's maximum context length is 65536 tokens.
However, you requested 4096 output tokens and your prompt contains at least
61441 input tokens`. The eval model (qwen35-9b-tmax-coev-r12) has a real 65536
context window; harness_runner reserves max_tokens=4096 for output, leaving a
~61440-token input ceiling. The pipeline's `CompactionProcessor.token_threshold`
was 140000 — calibrated for a 128k+ model and therefore unreachable here, so
compaction never fired and long histories overflowed the window into an
API-level 400 that crashes the whole task. This is a pure harness
mis-calibration, not a model capability gap.

### Changes

- `config.yaml` — `CompactionProcessor.token_threshold` 140000 → 40000.
  `rough_token_count` (cl100k) under-counts the real request by ~9-10k
  (system prompt + Bash tool schema + Qwen/cl100k tokenizer skew), so firing
  at 40000 rough tokens (~50k real) keeps every request safely below the
  61440 ceiling with margin for the 6-message retention window.
- `system_prompt.txt` — copied byte-for-byte from R0 (sibling required by
  `SiblingSystemPromptBuilder`; unchanged).

### Evidence

- `task_001044_45c70cf1` trace `task_end` step 43: `exit_reason=error`,
  error = "maximum context length is 65536 tokens ... prompt contains at least
  61441 input tokens". step_start rough `token_count` climbed
  40435 → 43408 → 46381 → 49354 with the compaction threshold at 140000 never
  approached; the next request overflowed.
- `task_000444_250abeda` trace `task_end`: identical
  "maximum context length is 65536 tokens" 400 error; history bloated by a
  degenerate verbatim-repeat loop (assistant repeated the same
  "Let me try a different approach…" paragraph across multiple steps).
- Passing cluster peaked far below 40000 rough tokens (max ~13k), so the new
  threshold leaves them untouched.

### Uncertainty

Both target tasks are hard reverse-engineering problems (disassembly / oracle
smoothing) and may still fail on correctness after the crash is removed — the
win here is converting an infrastructure-guaranteed 0 into a fair run, plus
protecting the long-context cluster from the same overflow. If pass_rate does
not hold or a passing task regresses with a compaction boundary in its trace,
revert to 140000.

## Round 2 — break identical-command loops

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-23T12:00:00Z
hypothesis_id: h_repeat_command_breaker_v1
levers: [control]
predicted_affected: [task_000796_828a72cf, task_000325_3fbc5745, task_000506_c13429e7, task_000677_1de47eed, task_000730_265f23f6, task_000910_16cc0daf, task_002033_f03df97e, task_001267_0acfd3a0, task_000437_8450eef7, task_000713_b0778d60]
cited_candidates: [C-002]
gating_outcome: accepted
gating_attribution: score=11/50; +3/-3 gained=task_000325_3fbc5745,task_000444_250abeda,task_001214_f44c0aa2 lost=task_000628_dcc7eb30,task_000710_fa628bd4,task_000903_7604c91e; score 0.2200 >= incumbent(mean) 0.2200 - tol 0.0400
expected_global_gain: "Recovers ~25 wasted steps on the 11-task identical-command loop cluster (6 domains) that all die at budget_exceeded=80 steps, giving the model a fair attempt instead of a guaranteed 0"
regression_risk: "Near-zero: processor only appends one redirect user message after 3 byte-identical consecutive Bash calls; never blocks/kills execution, so a slow legitimate retry is nudged at worst. Passing tasks essentially never repeat one command 3x"
cost_shift: "Net down: cuts ~25 redundant model+tool round-trips per looping task; the injected message is tiny"
rollback_trigger: "If pass_rate drops vs R1, or a previously-passing task regresses with a repeat-breaker nudge in its trace disrupting a legitimate retry, revert to R1 config"
-->

### Why

R1's compaction fix removed the context-overflow crashes; the dominant
remaining failure mode is now a step-budget loop. 16/39 failures exit
`budget_exceeded` at exactly 80 steps. Command-level analysis of the assistant
`tool_calls` shows the agent issuing the **byte-identical Bash command 20–29
times in a row**, receiving the same (usually empty/failing) result each time,
until the budget is exhausted. `LengthTruncationRecoveryProcessor` cannot catch
this because these turns emit a well-formed tool call (`finish_reason != length`).
The agent often even narrates "I keep repeating the same command" yet cannot
self-break — a mechanical loop-breaker is the reliable fix. The only tool is
`Bash`, so an Action lever is impossible; a prompt rule (Instruction) is what the
model is already failing to self-enforce.

### Changes

- `processors/repeat_command_breaker.py` — new `RepeatCommandBreakerProcessor`
  (`_order=6`, right after length_recovery). Tracks the normalized command of
  single-Bash-call turns; after `repeat_threshold=3` consecutive identical
  commands it injects a firm redirect user message (escalating wording on
  persistence), then re-arms. Never blocks execution.
- `config.yaml` — register the processor via absolute `file://` path with
  `repeat_threshold: 3`. All other R1 processors/knobs unchanged.
- `system_prompt.txt` — copied byte-for-byte from R1 (SiblingSystemPromptBuilder
  sibling; unchanged).

### Evidence

- `task_000796_828a72cf` steps 4–32: byte-identical command
  `# The video is only 13KB ... od -c /app/traffic_monitor.mp4 ...` emitted **29
  times consecutively**; every tool result `(exit 0, no output captured)`; exits
  `budget_exceeded`.
- `task_000730_265f23f6` (dup=27), `task_000506_c13429e7` (dup=26),
  `task_000677_1de47eed` (dup=26), `task_002033_f03df97e` (dup=26),
  `task_001267_0acfd3a0` (dup=21): same shape, one command repeated ~all
  remaining steps, all `budget_exceeded`.
- `task_000713_b0778d60`: 26 consecutive identical commands then exits `done`
  with reward 0 — the loop displaced the real work.
- Passing looper `task_001537_fbfffb79`: ran an identical `pip3 install
  pocketsphinx` 4x (all returned the same FileNotFoundError), then moved on and
  passed on unrelated later steps — a 3x redirect would have helped, not hurt.

### Uncertainty

The redirect gives the model a fair chance but does not itself solve the task;
some of these are genuinely hard (video-frame OCR, disassembly). The win is
converting guaranteed budget-loss into a fair attempt for a large cluster while
cutting cost. If R2 pass_rate does not hold or a passing task regresses with a
repeat-breaker nudge disrupting a legitimate retry, revert to R1.

## Round 3 — keep background services alive

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-23T13:00:00Z
hypothesis_id: h_service_persistence_v1
levers: [instruction]
predicted_affected: [task_000796_828a72cf, task_000097_d9d1d187, task_000910_16cc0daf, task_002146_0bc2994c, task_001201_1340f4e2, task_000348_31fb8c8a]
cited_candidates: [C-003]
gating_outcome: accepted
gating_attribution: score=14/50; +5/-2 gained=task_000097_d9d1d187,task_000437_8450eef7,task_000628_dcc7eb30,task_000903_7604c91e lost=task_000325_3fbc5745,task_001214_f44c0aa2; score 0.2800 >= incumbent(mean) 0.2200 - tol 0.0400
expected_global_gain: "Closes a structural 'service killed between Bash calls' failure across the HTTP/socket-service cluster (6 tasks, 5 domains); flips tasks whose only blocker was a dead listener at verify time and gives the rest a fair correctness attempt"
regression_risk: "Near-zero: additive strategy text only; no tool/processor behaviour change. Tasks that never start a service ignore the rule; the detach-and-re-verify nudge cannot harm a non-service task"
cost_shift: "Flat-to-down: a surviving server removes the repeated kill/rebuild/restart loops (task_000796 burned ~13 steps fighting SIGTERM); added prompt text is a few hundred tokens once per task"
rollback_trigger: "If R3 pass_rate drops vs R2, or the server-cluster tasks still show exit-143/Connection-refused with the agent using a plain background job despite the new rule, revert system_prompt.txt to the R2 5-line sidecar"
-->

### Why

R1 removed context-overflow crashes; R2 broke identical-command loops. No
exit_reason=error remain and several R2 loopers now exit done/short. The
dominant harness-caused remaining failure is a service-persistence bug on
HTTP/socket tasks. HarborSandbox.exec runs every command inside a setsid'd
bash process group, and kill_running() sends SIGTERM to that command's process
group between calls / on timeout. A server the agent starts as a plain
background job inherits the exec'd bash's PGID, so it is SIGTERM'd (exit 143,
defunct zombies) and is no longer listening when the external verifier probes
it (Connection refused). The agent has the capability to fix this (setsid is
in the image) but lacks the knowledge that a service must be detached into its
OWN new session to survive — a pure Instruction gap, not a model capability
gap.

### Changes

- system_prompt.txt (R3 sidecar beside config.yaml, read by
  SiblingSystemPromptBuilder) — extended the R2 5-line prompt with a general
  strategy: keep required long-running services alive by launching them
  detached in their own new session (setsid ... < /dev/null &), and re-confirm
  the listener is still up before finishing; plus an external-checker-style
  verify-before-exit reminder. Strategy-only; no task-specific literals, ports,
  paths, or process names.
- config.yaml — copied from R2 byte-for-byte except the header comment, updated
  to document the R3 sidecar change. Processor pipeline, knobs, and file://
  references unchanged.

### Evidence

- task_000796_828a72cf messages steps 52-78: agent repeatedly rebuilds and
  launches the server as a background job; every launch turn's tool result
  ends (exit 143) (SIGTERM) and ps aux lists [server] <defunct> zombies; agent
  narrates "The server was killed again." Final launch (step 77) also
  (exit 143) -> verifier: Connection refused on 127.0.0.1:8443.
- task_000097_d9d1d187 verifier output_tail: "Connection refused. Is the
  service listening on 127.0.0.1:9090?" — server not alive at verify.
- task_000348_31fb8c8a step-45: server WAS reachable (returned JSON) — confirms
  the mechanism (a surviving server yields a real HTTP response); its fail was
  a wrong graph-shortest-path result (partial capability gap), so it is
  mechanism-evidence, not a guaranteed flip.
- task_000910_16cc0daf, task_002146_0bc2994c: budget_exceeded service tasks;
  also expose a secondary gap (verifier ModuleNotFoundError requests because
  the exit-only dep guard never fires on budget_exceeded) — logged as deferred
  C-004, not shipped this round to avoid replay-gate risk.

### Uncertainty

The rule is necessary-not-always-sufficient: some server tasks also have a
correctness bug (task_000348). The guaranteed win is converting "listener dead
at verify" 0s into fair scored runs and cutting the kill/rebuild loops. If the
cluster still shows exit-143 / Connection-refused with a plain background job
after R3, revert the sidecar to R2.

Skipped (model capability gaps, no harness fix): task_000710_fa628bd4
(hash/oracle equivalence), task_000713_b0778d60 (bio primer reverse-engineering),
task_001674_125bb961 (diff-corpus classification), task_001201_1340f4e2
(RPN parser correctness) — require domain reasoning, not a harness mechanism.

## Round 4 — budget-aware verifier dep guard

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-23T12:57:08Z
hypothesis_id: h_verifier_dep_guard_budget_v1
levers: [action]
predicted_affected: [task_000796, task_000910, task_002108]
cited_candidates: [C-005]
gating_outcome: accepted
gating_attribution: score=13/50; +1/-2 gained=task_001267_0acfd3a0 lost=task_000436_cc1c950c,task_000628_dcc7eb30; score 0.2600 >= incumbent(mean) 0.2800 - tol 0.0400
expected_global_gain: "Removes a deterministic structural-zero floor for budget-bound tasks whose hidden verifier imports requests/pyyaml but never reach exit intent (the exit-only R0 guard cannot fire); complements it."
regression_risk: "One extra short Bash step near budget on tasks that approach the ceiling without exit intent; no-op when deps already importable. Passing exit-intent tasks unchanged (R0 guard at order 95 fires first; this one is idempotent)."
cost_shift: "Negligible - at most one idempotent import-check Bash call on runs that near budget without a clean end_turn; pure no-op otherwise."
rollback_trigger: "If R5 shows any previously-passing task regressing to budget_exceeded or a new error attributable to the injected Bash step, revert this processor (keep the exit-intent guard)."
-->

### Why

The R0 `VerifierDepGuardProcessor` installs verifier test-deps (`requests`,
`pyyaml`) only on exit intent - the model ending its turn with no tool call.
But a cluster of tasks (task_000796, task_000910, task_002108) exhaust the
80-step budget mid-work (`exit_reason=budget_exceeded`) and NEVER reach exit
intent, so the guard never fires. Their hidden `test_final_state.py` does
`import requests`; the image lacks it, so pytest aborts at COLLECTION time with
`ModuleNotFoundError` -> a deterministic structural 0 no matter what the final
container state is. R3 logged this as deferred C-004; this round ships the fix
as C-005 (distinct hypothesis id, distinct implementation shape - budget-aware
trigger - not a re-proposal of a reverted bet).

### Changes

- `processors/verifier_dep_guard_budget.py` - new
  `BudgetAwareVerifierDepGuardProcessor` (order 96, own singleton group). Fires
  the same idempotent dep-ensure Bash call on the first no-tool-call turn once
  `step_id >= max_steps - budget_margin` (margin 6), OR on exit intent -
  whichever first. `max_steps` read from `StepStartEvent.task`; fallback 80.
  At-most-once per task; only on turns with no model tool call; guarded per-module
  import check -> pure no-op when deps already present.
- `config.yaml` - register the new processor immediately after the R0
  exit-intent guard; kwargs budget_margin 6, fallback_max_steps 80.

### Evidence

- task_000796 / task_000910 / task_002108 `result.json`:
  `exit_reason=budget_exceeded`, reward 0; all 80 steps consumed.
- `messages.json` for all three: no clean exit-intent turn appears (the run
  ends on budget), confirming the R0 exit-only guard's trigger is never met;
  narration-only no-tool turns recur near the ceiling (task_002108: 12), which
  the budget-approach trigger latches onto to fire before step 80.
- Verifier `test_final_state.py` opens with `import requests`; container lacks
  it -> collection-time `ModuleNotFoundError` is deterministic.

### Uncertainty

This removes a structural floor; it does not guarantee content-correctness -
these tasks may still fail on wrong values / dead services (a capability gap).
The guaranteed effect is converting collection-error 0s into fair scored runs.
If R5 shows no flips AND no regressions, the change is cost-neutral insurance
and can stay; if it causes any T->F regression, revert per the rollback trigger.

## Round 5 — hard-block identical-command loops

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-23T14:05:00Z
hypothesis_id: h_repeat_command_blocker_v1
levers: [control]
predicted_affected: [task_001214_f44c0aa2, task_000908_170e5e4e, task_000506_c13429e7, task_001044_45c70cf1]
cited_candidates: [C-006]
gating_outcome: reverted
gating_attribution: score=11/50; +1/-3 gained=task_000628_dcc7eb30 lost=task_000097_d9d1d187,task_000472_6c2ef24d,task_001267_0acfd3a0; score 0.2200 < incumbent(mean) 0.2800 - tol 0.0400 -> revert to R3
expected_global_gain: "Frees ~40-72 wasted steps on a 4-task byte-identical-command loop cluster (2 budget_exceeded, 2 done-but-wrong-after-looping) so the model gets a real attempt; generalizes to any future task that falls into a command loop"
regression_risk: "Near-zero: no currently-passing task reaches 4 consecutive non-empty identical commands (measured); empty self-verify marker loops (task_000903 x21, task_001537 x26 both PASS) are excluded by normalization; a different next command re-arms the guard to silence"
cost_shift: "Net down: each block removes one real Bash round-trip and truncates ~20+ wasted model turns per looping task; synthetic_result is a few hundred tokens"
rollback_trigger: "If R6 shows any previously-passing task regressing T->F with a BLOCKED BY HARNESS marker disrupting legitimate iteration, revert to R4 config (drop this processor; do NOT restore the dead R2 breaker)"
-->

### Why

R4 = 13/50. No exit_reason=error remain (R1 fix holds). The dominant remaining
failure family is budget_exceeded at 80 steps. Root cause found: the R2
RepeatCommandBreakerProcessor is a SILENT NO-OP. It injected a redirect user
message in on_before_model, but the run loop rebuilds model input from
state.raw_messages each step and never writes on_before_model edits back to
state, so the nudge evaporated the next turn. Grep of "loop detected by the
harness" across all 50 R4 message logs returned 0 matches. Looping tasks still
burned the whole budget re-issuing one byte-identical command (pycdc x24,
cat-heredoc x28, printf x9).

### Changes

- processors/repeat_command_blocker.py — new RepeatCommandBlockerProcessor
  (order 6). Tracks consecutive byte-identical non-empty single-Bash commands
  in on_after_model; once the same command reaches block_threshold (four), it
  intercepts the call in on_before_tool with approved=False plus a
  synthetic_result. The run loop then skips the useless execution AND writes the
  corrective text into state.raw_messages as the tool result (runloop.py
  synthetic branch, lines 600-629), so the block PERSISTS in context and
  compounds. Re-arms on a different command; no task ids/paths/commands
  hard-coded.
- config.yaml — replaced the dead R2 RepeatCommandBreakerProcessor registration
  with the new blocker. All other processors/knobs unchanged.
- system_prompt.txt — copied byte-for-byte from R4 (SiblingSystemPromptBuilder
  sibling; unchanged).

### Evidence

- task_001214_f44c0aa2 messages: pycdc decompile of auth.pyc issued 24
  consecutive identical times, every result the same usage error; exits
  budget_exceeded.
- task_000908_170e5e4e messages: a cat-heredoc rewrite of graph_analytics.py
  (identical content) 28 consecutive times, every result "(exit 0, no output
  captured)"; loop reaches four repeats at step 8 of 80 (~72 steps wasted);
  budget_exceeded.
- task_000506_c13429e7 (9 consecutive), task_001044_45c70cf1 (4 consecutive):
  same shape.
- Retroactive regression check: max consecutive non-empty identical run per
  task; only these 4 (all failures) reach four-plus; every PASSING task is at
  most three. The two passing high-repeat tasks (task_000903 x21, task_001537
  x26) repeat the EMPTY command (self-verify markers), excluded by
  normalization. So the block cannot fire on a currently-passing trajectory.
- Verified via runloop.py that approved=False plus synthetic_result is the
  supported interception path and that the synthetic result is written to
  persisted history via add_raw_message (unlike the R2 before_model nudge).

### Uncertainty

Blocking the loop is necessary-not-always-sufficient: the freed steps may still
end in a correctness gap. The guaranteed win is converting budget-burned-in-a-loop
into a fair scored attempt at lower cost. Any T-to-F regression with a BLOCKED
marker triggers revert to R4.


## Round 7 - collapse max_tokens truncation loop

<!-- journal:frontmatter
round: 7
timestamp: 2026-04-27T00:00:00Z
hypothesis_id: h_truncation_loop_compactor_v1
levers: [control]
predicted_affected: [task_000998_4d9c7852, task_001214_f44c0aa2, task_001028_5bc8bc70, task_000908_170e5e4e, task_001267_0acfd3a0, task_000710_fa628bd4, task_000032_3fb303f6, task_000796_828a72cf, task_000437_8450eef7]
cited_candidates: [C-007]
gating_outcome: accepted
gating_attribution: score=13/50; +3/-1 gained=task_000710_fa628bd4,task_001267_0acfd3a0,task_001537_fbfffb79 lost=task_000097_d9d1d187; score 0.2600 >= incumbent(mean) 0.2600 - tol 0.0400
expected_global_gain: "Recovers dozens of wasted steps across a 9-task truncation-loop cluster (5 domains) that die at budget_exceeded; generalizes to any future task falling into the max_tokens loop (structural trigger, no literals)."
regression_risk: "Fires only on 3+ consecutive near-identical truncated (200+ char, no-tool-call) assistant turns - absent from all passing tasks (passive-nudge count 0). Never blocks/drops/fabricates a tool call; preserves the first narration turn. Long passers (task_000097 nudge=1, task_000628 nudge=0) below the min_run=3 trigger."
cost_shift: "Net down - collapsing the repetition wall shrinks assembled prompts and shortens loops so tasks stop burning the full 80-step budget."
rollback_trigger: "If R8 shows any previously-passing task regressing T->F with a harness collapse note disrupting legitimate work, or pass_rate drops vs R3 incumbent mean, drop the TruncationLoopCompactor registration and keep the R3 pipeline."
-->

### Why

Dominant harness-shaped failure in the r6 draw (11/50): a self-reinforcing
max_tokens verbosity loop. The eval model runs under a hard max_tokens=4096
output cap (set outside config, unchangeable here). On hard tasks it emits
~4096 tokens of prose with NO tool call (finish_reason=length); the run loop
appends a passive "Your previous response was cut off by the token limit.
Please continue" nudge; the model, now staring at a growing wall of its own
near-identical truncated narration, reproduces that wall 8-23x until
budget_exceeded. The pre-existing LengthTruncationRecoveryProcessor fires but
its on_before_model edit is ephemeral (never lands in state.raw_messages), so
the duplicate wall keeps growing and keeps priming the loop.

### Changes

- processors/truncation_loop_compactor.py - new MultiHookProcessor
  TruncationLoopCompactor (on_step_start, order 20, after compaction).
  Collapses 3+ consecutive near-identical truncated assistant turns (200+
  chars, no tool_calls) + interleaved passive nudges into one trimmed turn plus
  one "emit ONE short Bash command" directive. Run loop persists step_start
  structural edits, so the wall is durably removed.
- config.yaml - copied R3 incumbent byte-for-byte, appended the new processor
  registration after PostCompactionRefreshProcessor. Also copied R3
  system_prompt.txt sidecar so SiblingSystemPromptBuilder resolves it beside
  the new config.

### Evidence

- task_000998_4d9c7852 steps 2-34: assistant content truncated to ~1964
  chars every turn (finish_reason=length), prefix "The user is right - I've
  been stuck in a loop. The files are..." repeated x10, each followed by the
  passive nudge; exit budget_exceeded.
- task_001214_f44c0aa2: prefix "...Let me take a..." x11, passive-nudge count
  21, budget_exceeded.
- task_000908_170e5e4e: prefix "...Let me just w..." x13, passive-nudge count
  18, budget_exceeded.
- All 8 short passing tasks and the 2 long passers have passive-nudge count 0-1
  (below min_run=3), so the trigger is provably absent from the passing set.

### Uncertainty

Blocking the loop is necessary-not-always-sufficient: some of these 9 tasks
also carry a content-correctness gap, so freed steps may still end wrong. The
guaranteed win is converting budget-burned-in-a-loop into a fair scored attempt
at lower cost. Distinct from R2 (identical-tool-call breaker, still live) and
the reverted R5 (tool-block) - this touches only truncated narration turns,
never tool calls. Rollback if any passer regresses with the collapse note in
its trace.

## Round 8 — durably break identical-command loops

<!-- journal:frontmatter
round: 8
timestamp: 2026-04-27T16:00:00Z
hypothesis_id: h_identical_command_loop_compactor_v1
levers: [control]
predicted_affected: [task_000730_265f23f6, task_001028_5bc8bc70]
cited_candidates: [C-008]
gating_outcome: accepted
gating_attribution: score=13/50; +2/-2 gained=task_000325_3fbc5745,task_000437_8450eef7 lost=task_001267_0acfd3a0,task_001537_fbfffb79; score 0.2600 >= incumbent(mean) 0.2600 - tol 0.0400
expected_global_gain: "Recovers ~20-30 wasted steps each on the identical-Bash-command loop subcluster (task_000730 x31, task_001028 x19) that die at budget_exceeded, converting a guaranteed budget-loss 0 into a fair scored attempt; generalizes to any future task that falls into a byte-identical Bash loop (structural trigger, no literals)."
regression_risk: "Near-zero: fires only at 10-plus consecutive identical non-empty single-Bash commands; the passing set's max identical run is 7 (task_001537, a truncation-interleaved iterative PASS), so the collapse provably cannot fire on any currently-passing R7 trajectory. Never blocks/drops/fabricates a tool call (commands already ran); contract-clean net message reduction ending on one user directive."
cost_shift: "Net down: collapsing the repetition wall shrinks the assembled prompt and the directive breaks the loop early, so looping tasks stop burning the full 80-step budget."
rollback_trigger: "If R9 shows any previously-passing task regressing T->F with a harness collapse note ('your previous N turns issued the SAME command') in its trace disrupting legitimate iteration, or pass_rate drops vs the R7 incumbent mean, drop the IdenticalCommandLoopCompactor registration and keep the R7 pipeline."
-->

### Why

R7 = 13/50. No exit_reason=error remain (R1 fix holds). The budget_exceeded
family is still the largest failure bucket. Within it, a subcluster burns the
whole 80-step budget on ONE byte-identical single Bash command repeated turn
after turn, getting the same (empty/failing) result each time. This is distinct
from the finish_reason=length narration loop that the R7 TruncationLoopCompactor
handles (those turns carry NO tool call); here every turn emits a well-formed
Bash tool call. The R2 RepeatCommandBreaker (registered, order 6) is a proven
SILENT NO-OP — its on_before_model redirect never lands in state.raw_messages,
so the nudge evaporates each step and the model never sees it (grep of "loop
detected by the harness" across all 50 R7 traces = 0 hits). The reverted R5
blocker persisted but BLOCKED execution and disrupted legitimate iteration.

### Changes

- `processors/identical_command_loop_compactor.py` — new MultiHookProcessor
  IdenticalCommandLoopCompactor (on_step_start, order 21, right after the R7
  TruncationLoopCompactor at 20). Collapses a maximal run of min_run-plus
  consecutive assistant turns whose single Bash tool call is byte-identical
  (with their interleaved tool-result messages) down to the FIRST turn + its
  result, and appends ONE corrective directive user message. Runs at
  on_step_start so the edit changes history_hash and the run loop auto-generates
  a SegmentBoundary that writes the trimmed window durably into
  state.raw_messages/state.messages (runloop.py ~345-368) — the same durable
  path the R7 compactor uses. NEVER blocks/drops/fabricates a tool call.
- `config.yaml` — copied R7 byte-for-byte; registered the new processor after
  TruncationLoopCompactor with min_run=10 (safely above the passing set's max
  identical run of 7). system_prompt.txt copied byte-for-byte from R7.

### Evidence

- `_meta_scratch/dupscan.py` over all 50 R7 traces (max consecutive identical
  non-empty single-Bash run): task_000730_265f23f6 = 31, task_001028_5bc8bc70
  = 19 (both budget_exceeded, reward 0); highest PASSING task = task_001537
  = 7 (truncation-interleaved iterative disasm, PASSES); all other passers 3 or below.
- `task_000730_265f23f6`: `tesseract /app/bug_report.png stdout 2>&1` issued
  31 consecutive identical times; exit budget_exceeded.
- `task_001028_5bc8bc70`: `strings /home/user/log_analyzer` issued 19
  consecutive identical times; last assistant even narrates "I've been stuck in
  a loop repeatedly running the same strings command"; exit budget_exceeded.
- No "loop detected by the harness" redirect appears in either trace, proving
  the R2 breaker is inert on exactly the tasks it was meant to fix.
- runloop.py 345-368: on_step_start message mutation triggers auto
  SegmentBoundary which rewrites state.raw_messages/state.messages (durable).

### Uncertainty

Breaking the loop is necessary-not-always-sufficient: the freed steps may still
end in a content-correctness gap (both tasks are hard reverse-engineering / OCR
problems). The guaranteed win is converting budget-burned-in-a-loop into a fair
scored attempt at lower cost. Distinct mechanism and distinct hypothesis id from
the reverted R5 blocker (that intercepted on_before_tool with approved=False and
blocked execution at threshold 4; this trims already-produced history without
blocking, at a passing-set-safe threshold 10). If any passer regresses with the
collapse note in its trace, revert per the rollback trigger.

Skipped this round (model capability / correctness gaps, no harness fix): the
~20 tasks that exit done/no_tool_calls having self-verified as complete (the
CustomSelfVerifyProcessor checklist already fires once) but whose hidden verifier
finds wrong values / missing files (e.g. task_000763 PIN 2778 vs 2394;
task_000567 PC1_Sum 6.95 vs 7.77). Embedding the correct answers would be
task-specific injection, not a harness mechanism. The verifier-requests
ImportError floor (task_000796/task_000910/task_002108, budget_exceeded) was
addressed by R4's accepted dep guard but dropped in the R5-to-R3 revert; not
re-added this round because the dep guard alone did not flip them in R4 (the
services are also functionally wrong), so it is pure insurance with no expected
flip — deferred to keep this round's attribution clean.

## Round 9 — defuse interleaved max_tokens truncation loop

<!-- journal:frontmatter
round: 9
timestamp: 2026-04-28T00:00:00Z
hypothesis_id: h_truncation_nudge_escalator_v1
levers: [control]
predicted_affected: [task_001267_0acfd3a0, task_000713_b0778d60, task_001214_f44c0aa2, task_001028_5bc8bc70, task_000097_d9d1d187, task_000796_828a72cf, task_000348_31fb8c8a, task_000910_16cc0daf, task_000908_170e5e4e, task_000032_3fb303f6]
cited_candidates: [C-009]
gating_outcome: accepted
gating_attribution: score=13/50; score 0.2600 >= incumbent(mean) 0.2600 - tol 0.0400 (final-round scoring)
expected_global_gain: "Recovers dozens of wasted steps across a 10-task INTERLEAVED-truncation-loop cluster (services, decompilation, git-bisect, OCR) that all die budget_exceeded at 80 steps; generalizes to any future task that falls into the interleaved max_tokens loop (structural trigger, no literals)."
regression_risk: "Near-zero: fires only when the assembled window holds 8-or-more passive 'cut off by the token limit' nudges; the passing set's max is 6 (task_000628 PASS), so it provably cannot fire on any currently-passing R8 trajectory. Never blocks/drops/fabricates a tool call; only trims runaway prose and dedupes the passive-nudge wall, ending on one firm user directive."
cost_shift: "Net down: collapsing the passive-nudge wall and trimming runaway prose shrinks the assembled prompt each subsequent step, and the terseness directive shortens the loop so tasks stop burning the full 80-step budget."
rollback_trigger: "If R10 shows any previously-passing task regressing T-to-F with the firm-directive marker ('your responses have repeatedly hit the output token limit') in its trace disrupting legitimate work, or pass_rate drops vs the R7/R8 incumbent mean, drop the TruncationNudgeEscalator registration and keep the R8 pipeline."
-->

### Why

R8 = 13/50. No exit_reason=error remain (R1 fix holds); the byte-identical
Bash loop (R8) and the consecutive-no-tool-call narration loop (R7) are both
covered. The dominant UNCOVERED harness-shaped failure is an INTERLEAVED
max_tokens truncation loop: the eval model runs under a hard max_tokens=4096
output cap, emits ~4096 tokens of prose (often with a partial tool call), gets
finish_reason=length, the run loop appends the passive "cut off by the token
limit ... please continue" nudge, a tool result lands, and the model repeats —
accruing 8-23 passive nudges per run, interleaved with tool calls and
PostCompaction messages. Neither existing compactor fires: R7's
TruncationLoopCompactor requires 3-or-more CONSECUTIVE truncated turns with NO
tool call (a single interleaved tool-call turn breaks the run), and R8's
IdenticalCommandLoopCompactor requires 10-or-more byte-identical Bash commands
(the commands vary). Meanwhile LengthTruncationRecovery's firm nudge is written
in on_before_model and is EPHEMERAL — it never lands in state.raw_messages, so
the passive-nudge wall keeps growing and re-primes the runaway generation every
step until budget_exceeded at 80. This is a mechanical artifact the model
narrates but cannot self-break; a durable Control processor is the fix.

### Changes

- `processors/truncation_nudge_escalator.py` — new MultiHookProcessor
  TruncationNudgeEscalator (on_step_start, order 22, after the R7/R8
  compactors). When the assembled window holds nudge_threshold-or-more passive
  "cut off by the token limit" nudges, it drops every passive nudge except the
  most recent, replaces that one with a single firm terseness directive, and
  head+tail-trims over-long truncated assistant narration turns WHETHER OR NOT
  they carry a tool call (tool calls preserved verbatim; only runaway prose
  shrinks). Runs at on_step_start so the edit changes history_hash and the run
  loop auto-generates a SegmentBoundary that writes the trimmed window durably
  into state.raw_messages/state.messages (runloop.py ~345-368) — the same
  durable path the R7/R8 compactors use. Never blocks/drops/fabricates a tool
  call; structural trigger only, no task ids/paths/commands.
- `config.yaml` — copied R8 byte-for-byte; registered the new processor after
  IdenticalCommandLoopCompactor with nudge_threshold=8 (safely above the
  passing set's max nudge count of 6). system_prompt.txt copied byte-for-byte
  from R8.

### Evidence

- task_000097_d9d1d187 messages.json: 13 passive "cut off by the token limit"
  nudges (turns 10,14,16,22,26,30,34,38,42,54,59,63,67); assistant turns repeat
  the identical prefix "The service is working correctly..." / "The Python
  script is not receiving any data. This is strange..."; interleaved tool-call
  turns (11,17,23,27,31,...) break every consecutive run so R7's compactor never
  assembles a 3-or-more run. budget_exceeded at 80.
- Passive-nudge counts on failing budget tasks: task_001267_0acfd3a0=23,
  task_001028_5bc8bc70=18 ("I've been stuck in a loop"), task_000713_b0778d60=17,
  task_001214_f44c0aa2=17, task_000796_828a72cf=11, task_000032_3fb303f6=10,
  task_000348_31fb8c8a=10, task_000910_16cc0daf=9, task_000908_170e5e4e=8.
- Passing set max passive-nudge count = 6 (task_000628_dcc7eb30, PASS); all
  other passers 0-1. nudge_threshold=8 sits above the passing max, so the
  trigger is provably absent from the passing set.
- Root cause verified in source: LengthTruncationRecoveryProcessor rewrites the
  nudge in on_before_model (ephemeral, never persisted); TruncationLoopCompactor
  `_is_truncated_narration` returns False when tool_calls is set; runloop.py
  345-368 confirms an on_step_start message mutation triggers an auto
  SegmentBoundary that durably rewrites state.raw_messages/state.messages.

### Uncertainty

Breaking the loop is necessary-not-always-sufficient: the freed steps may still
end in a content-correctness gap (several of these are hard service-debugging /
reverse-engineering problems). The guaranteed win is converting budget-burned-
in-an-interleaved-loop into a fair scored attempt at lower cost, on a 10-task
cluster the two existing compactors provably miss. Distinct mechanism and
distinct hypothesis id from R7 (consecutive-no-tool collapse) and R8
(byte-identical Bash collapse), and from the reverted R5 blocker (which blocked
execution). If any passer regresses with the firm-directive marker in its
trace, revert per the rollback trigger.
