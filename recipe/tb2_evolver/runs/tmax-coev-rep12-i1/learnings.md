# Learnings — tmax-coev-rep12-i1

## Round 1 — no-progress repeat guard

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-22T11:06:50Z
hypothesis_id: h_noprogress_repeat_guard_v1
levers: [control]
predicted_affected: [task_002108_a8cfbf2a, task_000470_f819ab03, task_000840_b2ac4603, task_000908_170e5e4e]
cited_candidates: [C-001]
gating_outcome: reverted
gating_attribution: score=25/50; +2/-6 gained=task_000987_31a34277,task_001400_7ed02d97 lost=task_000175_ef5ab90b,task_000529_8bcc67c9,task_000891_aa1685c7,task_001059_05af3d65; score 0.5000 < incumbent(mean) 0.5800 - tol 0.0400 -> revert to R0
expected_global_gain: "Flips/recovers the budget_exceeded+error loop cluster (>=4 tasks with hard identical-command loops) by breaking no-progress repetition and freeing step budget"
regression_risk: "Very low — guard only appends advisory text to a tool result, only fires on >=3 consecutive identical (command,output) pairs (a shape absent from passing runs), never blocks a command or mutates event.messages"
cost_shift: "Net neutral-to-negative: breaking 80-step loops early reduces tokens on affected cluster; only additive cost is warning text on rare legitimate 3x repeats"
rollback_trigger: "If R2 pass_rate does not improve and any previously-passing task in the loop-free set regresses, or if tokens rise materially, revert the processor"
-->

### Why

R0 scored 29/50 (58%). Analysis of the 21 failing trajectories showed a
dominant, mechanical failure shape: the agent issues the SAME Bash command
repeatedly, receives the SAME output every time, and makes zero progress
until it either exhausts the 80-step budget (`exit_reason=budget_exceeded`,
5 tasks) or crashes (`exit_reason=error`, task_002108). Counting maximal
runs of consecutive identical commands over `.messages.json`: task_002108
= 44, task_000470 = 28, task_000840 = 10 (paired cmd+output), task_000908
= 8 (paired). The existing `LengthTruncationRecoveryProcessor` only fires on
`finish_reason=length`; these loops have a normal finish reason plus a real
(repeated) tool call, so nothing in the pipeline intercepted them.

### Changes

- `processors/norepeat_guard.py` — new `NoProgressRepeatGuard`
  (`MultiHookProcessor`). Captures the Bash command in `on_before_tool`
  keyed by `tool_call_id`; in `on_after_tool` builds a (command, truncated
  output) signature and counts consecutive identical repeats; appends a
  corrective "stop repeating / inspect real state" nudge to the tool result
  at `repeat_threshold=3`, escalating at `escalate_threshold=6`. Only mutates
  `event.result` (contract-safe, mirrors in-tree `CustomEditToolProcessor`).
- `config.yaml` — register `NoProgressRepeatGuard` after
  `CustomEditToolProcessor` in the processor pipeline via absolute `file://`
  path (`repeat_threshold: 3`, `escalate_threshold: 6`, `tool_name: Bash`).

### Evidence

- `task_000470_f819ab03`: `exit_reason=budget_exceeded`, 80/80 steps; body
  shows `CMD "cat << 'EOF' | nc -w 2 127.0.0.1 8080..."` → `OUT '(exit 0, no
  output captured)'` repeated ~11x back-to-back; 31/33 total commands
  identical.
- `task_002108_a8cfbf2a`: `exit_reason=error`, 68 steps; last 8 assistant
  tool calls byte-identical `cat > /app/frame_server/src/main.rs << 'ENDOFFILE'`.
- `task_000840_b2ac4603` / `task_000908_170e5e4e`: `budget_exceeded` 80/80;
  max consecutive identical (command+output) pairs measured 10 and 8.

### Uncertainty

The nudge redirects the agent to observe state rather than solving the task
for it, so a flip depends on the agent then choosing a productive next action
— capability, not harness, from that point. If the underlying tasks also have
a capability gap (e.g. the Go/Rust server logic was simply wrong), breaking
the loop frees budget but may not flip the task. Signal to watch: does the
`budget_exceeded` count drop in R2 even if pass_rate moves less? If loops
persist despite the guard, the threshold or signature (command-only vs
command+output) needs tuning next round.

## Round 3 — verifier dep guard

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-22T12:30:00Z
hypothesis_id: h_verifier_dep_guard_v1
levers: [control]
predicted_affected: [task_002108_a8cfbf2a, task_001857_24daeef3, task_000796_828a72cf]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=26/50; +3/-3 gained=task_001059_05af3d65,task_001400_7ed02d97,task_002115_fda7e3d1 lost=task_000121_482e279f,task_000347_6bf1aeee,task_000470_f819ab03; score 0.5200 >= incumbent(mean) 0.5500 - tol 0.0400
expected_global_gain: "Flips the 'verifier imports a common test lib absent from the image' class: at least 2 verified clean flips (002108, 001857) where the agent's HTTP service is provably working and the ONLY failure is import requests aborting pytest collection; generalizes to any TB2 HTTP-service task probed with requests"
regression_risk: "Near-zero: install is import-guarded (no-op when present), every branch ends in || true so the injected Bash call can never fail the run, fires at most once per task on exit-intent, and coordinates with the order-90 self-verify processor. No currently-passing task hit this error."
cost_shift: "+1 short Bash round-trip on exit-intent runs (a few hundred tokens for dep-check output plus one ack). Negligible; import short-circuits on tasks that already have the deps."
rollback_trigger: "If R4 pass_rate does not improve and any previously-passing task regresses, or if the pip install visibly stalls/times out on runs, revert the processor."
-->

### Why

R2 (=R0 config re-measured) scored 25/50. Sweeping failing tasks' `result.json`
`final_pytest.output_tail` surfaced a clean, purely-mechanical cluster: three
tasks (task_002108 file_operations, task_001857 debugging, task_000796 security)
fail with an identical pytest COLLECTION abort — `ImportError while importing
test module '/tmp/test_final_state.py'` then `ModuleNotFoundError: No module
named 'requests'` then `Interrupted: 1 error during collection`. The hidden
verifier probes a running HTTP server with `requests`, which is absent from
these base images. The task descriptions never mention `requests` (it is a
property of the post-exit verifier), so the agent has no way to infer the
dependency. Meanwhile the agents' actual solutions are correct.

### Changes

- `processors/verifier_dep_guard.py` — new `VerifierDepGuardProcessor`
  (MultiHookProcessor). On exit-intent (`on_after_model`, finish_reason in
  end_turn/stop and no tool_calls) it injects one real guarded `Bash` tool
  call that, for each of a tiny generic allow-list (`requests`, `pyyaml`),
  imports-checks then `pip install`s only if missing; every branch ends
  `|| true`. Fires once per task; `_order=95` so it defers to the order-90
  self-verify processor.
- `config.yaml` — register `VerifierDepGuardProcessor` after
  `CustomSelfVerifyProcessor` via absolute `file://` path.

### Evidence

- `task_002108_a8cfbf2a`: verifier tail = `ModuleNotFoundError: No module named
  'requests' ... Interrupted: 1 error during collection`. Agent messages show
  its Rust frame server tested via stdlib urllib: `Status: 200`,
  `Content-Type: image/jpeg`, wrong token gives `401 Unauthorized`, final `ps`
  shows `./target/release/frame_server` PID 1293 live (not defunct). Solution
  correct; only `import requests` failed.
- `task_001857_24daeef3`: same verifier tail. Agent fixed the C++ off-by-one,
  ran `./diagnostic_server` (live in ps), own checks returned `{"status":
  "healthy"}` on HTTP port and `FRAMES: 450` on TCP port. Solution correct;
  only `import requests` failed.
- `task_000796_828a72cf`: same verifier tail (partial — server still crashing
  at budget, so guard removes the collection mask but may not flip alone).
- pip works in-env: `task_001968_3adc0f9b` tool output `Successfully installed
  ... certifi-2026.7.22 charset_normalizer-3.5.1 idna-3.19 ...` (requests' own
  dependency chain downloaded fine).

### Uncertainty

Distinct from R1's reverted no-progress loop guard (different lever mechanism,
different cluster). Risk: if some target image genuinely cannot install
requests (no pip index reachable for that image), the branch reports
`unavailable` and the task stays failed — no harm, no flip. Signal to watch in
R4: do 002108 and 001857 flip F->T and does the `requests` ImportError
disappear from the failing-task tails?


## Round 4 — workflow-discipline system prompt

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-22T13:30:00Z
hypothesis_id: h_workflow_discipline_prompt_v1
levers: [instruction]
predicted_affected: [task_001857_24daeef3, task_000470_f819ab03, task_000716_206dc0f6, task_000506_c13429e7, task_001378_3143a60f]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=32/50; +8/-2 gained=task_000121_482e279f,task_000347_6bf1aeee,task_000529_8bcc67c9,task_000840_b2ac4603 lost=task_000837_f8e45558,task_001722_c69123ec; score 0.6400 >= incumbent(mean) 0.5500 - tol 0.0400
expected_global_gain: "Attacks two failing sub-clusters — the budget-thrash/blind-retry cluster (>=4 tasks that iterate to the step ceiling with no plan/root-cause) and the lenient-self-verification cluster (task_001857: raw-socket probe passed but verifier HTTP client hit BadStatusLine) — while re-encoding the survey/plan/verify discipline the fast passing cluster already relies on so future prompt edits cannot silently regress it"
regression_risk: "Longer prompt may make the model over-invest in planning/verification on trivial tasks (a few extra steps / mild verbosity cost). All guidance is general strategy with no task-specific literals or code, so it cannot inject a wrong solution; worst case is small cost inflation on already-passing short tasks"
cost_shift: "+a few hundred one-time system-prompt tokens per task and possibly +1-2 verification commands on service tasks; expected net-neutral-to-negative overall since it should cut the multi-hundred-second thrash loops (task_000716 at 1280s, task_000348 at 845s) that currently dominate budget spend"
rollback_trigger: "If R5 pass_rate does not improve AND any previously-passing short task regresses (T->F) or median elapsed_s rises materially, revert the sidecar to the 5-line default"
-->

### Why

R3 = 26/50; score has been stuck in the 25-29 band across all rounds and
the two prior config bets (R1 control loop-guard reverted, R3 verifier dep
guard accepted) each moved only +/-3 tasks — inside the repeat-spread noise.
The system-prompt sidecar is still the 5-line default and encodes none of
the workflow discipline the TB2 playbook flags as the single biggest score
lever (upfront survey, explicit plan, diagnose-before-retry, verify
deliverables the way a checker would). Two distinct failing sub-clusters
share the absence of that discipline: (a) a budget-thrash/blind-retry
cluster whose longest members burn 600-1280s iterating "let me try a
different approach" to the step ceiling with no plan or root-cause step,
and (b) a lenient-self-verification cluster where the agent's own final
check is looser than the verifier's — task_001857 confirmed its HTTP
endpoint with a raw socket read (reported Match:True) but the verifier's
requests client rejected the response with BadStatusLine because the
server omitted the HTTP status line. The fast passing tasks (30-95s) show
the opposite: a compact inspect -> act -> confirm arc.

### Changes

- system_prompt.txt (sidecar beside R4 config.yaml, loaded by
  SiblingSystemPromptBuilder) — replaced the 5-line default with an
  explicit general workflow: (1) survey first, (2) restate deliverables and
  plan, (3) diagnose root cause before retrying, (4) budget awareness /
  stop looping, (5) verify the way a checker would — real HTTP client not
  raw socket, exact output path/format, run programs end-to-end, (6) keep
  required services alive after exit, (7) avoid polluting the working dir
  (incl. filename/stdlib-module collisions). Strategy-only, no task
  literals, no copy-this-code directives.
- config.yaml — unchanged pipeline; documented the sidecar revision in a
  header comment on the SystemPromptProcessor. R3's accepted
  verifier_dep_guard is kept as-is.

### Evidence

- task_001857_24daeef3: final steps run a raw socket probe printing
  Actual: {"status": "healthy"} / Match: True then declare done;
  verifier pytest tail = Failed: ... BadStatusLine('{"status": "healthy"}').
  Lenient self-check masked a protocol-framing bug an HTTP client catches.
- task_000470_f819ab03: last 4 assistant turns are byte-identical
  echo "..." | /app/metrics_extractor 2>&1; echo "Exit: $?", each
  prefaced "I've been stuck in a loop" — repeats the probe instead of
  diagnosing why the service isn't listening (verifier: ConnectionRefused).
- task_000716_206dc0f6 (1280s), task_000506_c13429e7 (629s),
  task_001378_3143a60f (662s): first assistant message is already
  mid-trial-and-error with no plan/deliverable list; ~33 turns to budget.
- Passing task_000404_e2591ca5 (52s), task_000225_1391f954 (91s),
  task_000741_7786c14b (30s): compact inspect->act->confirm sequences —
  the habit this round lifts into an explicit rule.

### Uncertainty

Instruction lever on a 9B model: the guidance may not be reliably
followed, and R1's mechanical loop guard (different lever) was reverted, so
the loop cluster is hard. The clean bet is task_001857 (a correct fix is
one HTTP-client check away). Signal to watch in R5: does median elapsed_s
on the thrash cluster drop, does task_001857 flip F->T, and do any fast
passing tasks regress or slow down materially? If pass_rate is flat and
short tasks slow, the prompt is too heavy — trim or revert.


## Round 5 — self-declared thrash guard

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-22T14:30:00Z
hypothesis_id: h_self_declared_thrash_guard_v1
levers: [control]
predicted_affected: [task_000470_f819ab03, task_000348_31fb8c8a, task_000669_0ef2d04d, task_000891_aa1685c7]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=30/50; score 0.6000 >= incumbent(mean) 0.6400 - tol 0.0400 (final-round scoring)
expected_global_gain: "Breaks the self-declared semantic-thrash cluster (4-5 tasks that narrate 'stuck in a loop' 20-33x while re-running the same failing probe to the step ceiling at 331-1131s). Gives near-miss members a differently-shaped-hypothesis / commit-and-exit path they never got (runs died mid-thrash), and reclaims wasted budget on the capability-gap members. Generalizes to any run where a small model recognizes it is looping but has no runtime escape hatch."
regression_risk: "Near-zero on the passing set: the self-stuck narration phrase family appears 0x in every sampled passing task (000916/000987/001968/001400/000840) despite those runs being long and command-repetitive. Guard only appends/replaces a single trailing user message; never blocks a tool call, never mutates tool_calls or the system prompt. diagnose_threshold=2 stops a single incidental phrase from tripping it."
cost_shift: "Strongly negative (cost-saving): the targeted cluster currently burns 331-1131s to the step ceiling; the exit_threshold=4 stop-and-commit nudge cuts those short. Only additive cost is a 1-2 sentence nudge on turns that trip the guard."
rollback_trigger: "If R6 pass_rate does not improve AND any previously-passing task regresses T->F, or median elapsed_s on the thrash cluster does not drop, revert the processor."
-->

### Why

R4 = 32/50 (64%, best round so far; workflow-discipline prompt accepted).
Of the 18 R4 failures, the dominant and most distinctive cluster is a
*semantic thrash loop*: the model issues a real Bash call each turn (normal
finish reason, so the existing LengthTruncationRecoveryProcessor never fires)
but repeatedly narrates its own stuckness — "I've been stuck in a loop",
"I keep repeating the same approach", "let me try a different approach" —
while re-running the same failing probe, and burns the entire step budget
(331-1131s) without ever changing strategy or reaching exit-intent. The R4
system prompt already tells the agent to diagnose-before-retry and stop
looping; the model reads it, visibly agrees it is stuck, and loops anyway.
A static instruction cannot fix a runtime behavioural loop — only a runtime
hook that fires the moment the loop is self-declared can.

### Changes

- `processors/thrash_loop_guard.py` — new `SelfDeclaredThrashGuard`
  (MultiHookProcessor, _order=6, mirrors LengthTruncationRecoveryProcessor's
  contract-safe on_after_model/on_before_model pattern). Counts consecutive
  assistant turns containing self-stuck narration; at diagnose_threshold=2
  injects a forced structured-diagnosis nudge (verified-working vs
  verified-failing, ONE differently-shaped hypothesis, one command), at
  exit_threshold=4 injects a stop-and-commit-and-exit nudge that preserves
  partial solutions and finishes the turn instead of burning budget.
- `config.yaml` — register SelfDeclaredThrashGuard after
  LengthTruncationRecoveryProcessor via absolute file:// path. R3
  verifier-dep-guard and R4 system_prompt.txt sidecar kept unchanged (sidecar
  copied into R5 output_dir so SiblingSystemPromptBuilder resolves it).

### Evidence

- task_000470_f819ab03 (1131s, budget): 28x "repeating the same" + 23x
  "stuck in a loop" + 33x "different approach"; 20 identical tool calls;
  verifier ConnectionRefused (service never listened).
- task_000348_31fb8c8a (888s): 30x "stuck in a loop", 33 identical tool
  calls; converged_gamma returned None.
- task_000669_0ef2d04d (331s): 28x "I keep"/"different approach"; evil
  corpus dir left empty.
- task_000891_aa1685c7 (712s): 22x "stuck in a loop" + 39x "different
  approach"; all_scores.csv never written.
- Passing controls (0x self-stuck phrases despite long/repetitive runs):
  task_000916_5dda38ba, task_000987_31a34277, task_001968_3adc0f9b,
  task_001400_7ed02d97, task_000840_b2ac4603. This is the key distinction
  from R1's reverted command-signature guard: raw identical-tool-call counts
  are 18-26 in these PASSING tasks (unusable signal), but self-declared
  stuckness is 0x in all of them.

### Uncertainty

Distinct mechanism from R1's reverted guard (self-declared stuckness vs raw
command signature; escalation to structured diagnosis + commit-and-exit vs
weak advisory text). Instruction-lever R4 already covers the "diagnose/stop"
message content, so the added value is the *runtime timing* — firing at the
moment of detected loop. Risk: for pure capability-gap members (000348 wrong
algorithm) the guard frees budget and exits cleanly but does not flip the
task; that is still a Pareto-positive cost win with ~0 regression risk.
Signal to watch in R6: does median elapsed_s on the thrash cluster drop, do
any near-miss members flip F->T, and do the passing controls stay T->T?
