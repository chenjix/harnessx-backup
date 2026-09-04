# Evolve Journal — tmax-coev-rep21-i3

## Round 1 — strict literal self-verify

<!-- journal:frontmatter
round: 1
timestamp: 2026-04-27T00:00:00Z
hypothesis_id: h_strict_self_verify_v1
levers: [control]
predicted_affected: [task_000069_41f1682c, task_001979_a1e24b6f, task_000409_ff1246d3]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=7/50; +1/-2 gained=task_001447_8bde38ef lost=task_000328_80fb4c9f,task_000456_91b022e3; gating disabled (tolerance < 0)
expected_global_gain: "Flips premature-commit failures in the no_tool_calls/done+reward=0 cluster where the verifier fails on a literal mismatch (exact format/count/behavioural verb) the agent never tested."
regression_risk: "Adds one extra one-shot verification turn on already-passing tasks (8); their answers already match so a stronger re-check should not flip pass->fail. No loop-control/budget/tool-registry change."
cost_shift: "Small, bounded per task that reaches the no-tool exit (one verification turn + a few Bash checks, one-shot). Negligible on budget_exceeded tasks."
rollback_trigger: "If R2 shows the passing cluster shrinking or the no_tool_calls+reward=0 count not improving, revert to stock CustomSelfVerifyProcessor."
-->

### Why

Assigned focus task_000069_41f1682c fails despite exiting cleanly
(`no_tool_calls`/`done`, reward 0). The task said to "redirect
(**append**)" the executable's stdout to `deployment.log`; the agent used
`>` (truncate). Its own idempotency test ran the script twice, saw one log
line, and *declared success* — but the verifier ran the script twice and
expected the log to grow to exactly 2 lines while `routes.txt` stayed at 2
lines (config idempotent, log append-only). The agent conflated "idempotent
persistent state" with "truncate the log". The stock `CustomSelfVerifyProcessor`
checklist asked "does your solution address every requirement", but let the
agent *assert* compliance in prose without ever testing the specific literal
behaviour. This is the dominant shape in the round's 42 failures: the large
`no_tool_calls`/`done`+`reward=0` cluster is mostly premature commits where the
verifier fails on an exact format/count/behavioural-verb mismatch the agent
never actually exercised.

### Changes

- `processors/strict_self_verify.py` — new `StrictSelfVerifyProcessor`
  (`MultiHookProcessor`); same one-shot keepalive mechanics + singleton group
  (`tb2_self_verify`) as the stock processor, but injects a checklist that
  forces (1) verbatim enumeration of the task's LITERAL requirements (exact
  formats, counts, behavioural verbs like "append"/"idempotent", thresholds)
  and (2) a proof command per requirement whose output is compared to the spec,
  explicitly rejecting a single happy-path run.
- `config.yaml` — replaced the
  `benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor` entry with a
  `file://` reference to the new class. No other pipeline change.

### Evidence

- `task_000069_41f1682c` result.json: `finished=no_tool_calls`, `reward=0`;
  final_pytest: "deployment.log should have exactly 2 lines after running
  twice, but has 1." Body (last assistant turn after `_tb2_self_verify` fired):
  "running it again ... doesn't append duplicate lines to the log file" while
  the script uses `... > /home/user/deployment.log` (truncate).
- `task_001979_a1e24b6f` result.json: `reward=0`; final_pytest: "At index 4
  diff: '33.0' != '33.00'." Body (last assistant turn after `_tb2_self_verify`
  at msg 15): "✅ Rolling statistics ... rounded to 2 decimal places" and "The
  expected output matches the actual output exactly" — a prose assertion, not a
  byte comparison.
- `task_000409_ff1246d3` result.json: `reward=0`; final_pytest: "Output
  mismatch at output line 6. Oracle produced: 41225,0.00,0.00,CAT / Agent
  produced: 99614,-100.00,-100.00,CAT" — committed on a happy-path run without
  comparing to the exact spec.

### Uncertainty

The change is Instruction-content delivered through a Control hook, so its
effect depends on the 4B model actually following a longer checklist. If the
model ignores the added steps, we get cost with no flips (revert per trigger).
Downside is bounded: one-shot, no loop-control change, and it cannot block a
genuine exit. Contract/dry_fire/canonicalize/literals all pass clean.

## Round 2 — no-op: assigned task is a Docker infra error

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T12:00:00Z
hypothesis_id: h_noop_infra_container_conflict_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None claimed. Assigned failure is infrastructure, not a harness capability gap; no config change is defensible."
regression_risk: "None — byte-identical copy of R1/config.yaml."
cost_shift: "Zero — no pipeline change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus `task_000010_644ab1c2` did not fail inside the run loop. Its
`result.json` is `status=error, reward=0, elapsed_s=0.9` with
`RuntimeError: docker run failed ... Conflict. The container name
"/tmax-task000010644ab1c2-1788195286" is already in use ...`
(traceback in `recipe/tmax_eval/docker_env.py::start_container`). No
`messages.json` exists — the agent never started. This is a container-name
collision that happens BEFORE any processor / tool / template / system prompt
runs, so no HarnessConfig edit can affect it.

This is systemic this round, not a one-off: 21/50 tasks are `status=error`
with the byte-identical `name_conflict` shape, all `elapsed_s <= 1.1s`. The
real harness-addressable population is ~29 tasks (21 ok/reward=0, 7 ok/reward=1,
1 agent_error). R1's own assigned focus `task_000069_41f1682c` is now ALSO one
of these infra errors, so R1's attribution is confounded until the runner is
fixed.

### Changes

- `config.yaml` — byte-for-byte copy of `R1/config.yaml` (explicit no-op).
  Canonicalize: `{"ok": true, "checked_templates": 0}`.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — documents the `docker_env.py`
  container-name-reuse defect and three concrete fix options (pre-flight
  `docker rm -f`, unique name suffix, or omit `--name`).

### Evidence

- `task_000010_644ab1c2` result.json: `status=error`, `elapsed_s=0.9`,
  error = docker name Conflict; no messages file present.
- Round-wide count: 21 `error` results, all `name_conflict`, max
  `elapsed_s=1.1`.

### Uncertainty

If the runner's container churn is fixed next round, these 21 tasks become
scorable and the true harness gap population (and R1's attribution) will
finally be visible. Nothing in the harness surface could have changed this
round's outcome for the assigned task.

## Round 2 — infra flake, no harness lever (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T12:00:00Z
hypothesis_id: h_container_name_collision_noop_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None from config; the assigned failure is an eval-runner Docker container-name collision that no HarnessConfig surface can touch. Documented for human fix (potential +21/129 recovery if runner is patched)."
regression_risk: "None — config is copied byte-for-byte from R1 baseline."
cost_shift: "Zero — no pipeline change."
rollback_trigger: "N/A (no-op). If a future round's assigned focus is again an infra 'status: error' task, no-op again and escalate the docker_env.py fix."
-->

### Why

Assigned focus `task_000106_23215092` did NOT fail inside the agent loop.
Its `result.json` is `status: error`, `reward: 0`, `elapsed_s: 0.6`, and the
trajectory dir contains *only* `result.json` — no `agent/`, no episode JSONL,
no tool calls, no system-prompt exchange. The Docker container never started:

  `docker: Error response from daemon: Conflict. The container name
   "/tmax-task00010623215092-1788195286" is already in use by container ...`

This is an eval-runner orchestration bug, not a harness deficiency and not a
model capability gap — the model never ran. The evolvable surface (processors,
system prompt, tool registry) all execute *inside* the container that here
never came up, so no `HarnessConfig` change can influence this task.

Scope: **21 of 129 tasks (16%)** in this round failed identically — same
`status: error`, `elapsed_s ~0.5-0.9s`, same "container name already in use"
Docker conflict (e.g. task_000010_644ab1c2, task_000069_41f1682c,
task_000106_23215092, task_000111_cbada64a, task_000118_3043e92d, +16 more).
Root cause is in read-only `recipe/tmax_eval/docker_env.py::start_container`:
the container name uses `int(time.time())` (1-second resolution) as its only
uniqueness suffix, so a same-second retry / collision with an un-`rm`'d prior
container hard-fails `docker run` before boot.

Note for attribution: `task_000069_41f1682c` was a predicted_affected target
of R1's StrictSelfVerifyProcessor. It did not run this round due to this infra
conflict, so R1's attribution for that task is confounded by infrastructure.

### Changes

- `config.yaml` — explicit byte-for-byte copy of the R1 baseline. No change.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — documents the container-name collision,
  root cause, and three candidate fixes (uuid suffix / pre-remove stale name /
  finally-block cleanup). Outside my write scope (recipe/** is read-only).

### Evidence

- `task_000106_23215092` result.json: `"status": "error"`, `"reward": 0`,
  `"elapsed_s": 0.6`, error = docker "container name ... already in use".
- `grep -l 'already in use by container' <traj>/task_*/result.json` → 21 hits;
  `grep -l '"status": "error"'` → 21 hits (identical set).
- `recipe/tmax_eval/docker_env.py:105`:
  `name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]`.

### Uncertainty

None on the diagnosis — the missing trajectory body and the daemon error
string are unambiguous. The only open question is whether the human patches
`docker_env.py`; if so, up to 21 tasks may re-enter the runnable set and
their true harness/model behaviour becomes visible for a future round. Making
any processor edit here would be pure noise and risk regressing the 108 tasks
that do run, so the disciplined action is the no-op plus escalation.

## Round 2 — no-op: assigned task is Docker infra flake

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T15:30:00Z
hypothesis_id: h_noop_docker_infra_r2c3
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None claimed — assigned focus is not a HarnessConfig-addressable failure; config copied byte-for-byte."
regression_risk: "None — byte-identical no-op (md5 209c681e37801ed32d453b9be51ebb04)."
cost_shift: "Zero — no pipeline change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus `task_000069_41f1682c` is **not** a harness capability gap.
In this round's trajectory set it failed with `status="error"`,
`reward=0`, `elapsed_s=0.5` and **no `messages.json`** — the agent phase
never ran. The traceback is a Docker container-name collision raised in
`recipe/tmax_eval/docker_env.py:124` (`start_container`) via
`run_eval.py:149`:
`docker: Error response from daemon: Conflict. The container name
"/tmax-task00006941f1682c-..." is already in use ...`. This is a
**cluster of 21/50 tasks** all erroring identically at <1.2s before any
processor, system prompt, or tool is instantiated. Nothing in the
evolvable `HarnessConfig` surface (processors / tool_registry / system
prompt) can influence Docker container startup, and the runner code that
raises it is read-only (hard invariant #2). Note: R1's journal diagnosed
task_000069 as a `>` vs `>>` self-verify gap, but that was drawn from a
*different* prior run where the agent actually executed — in the current
trajectory set the task never reached the agent at all.

### Changes

- `config.yaml` — copied byte-for-byte from `current_config`
  (R1/config.yaml). Explicit no-op. `canonicalize` → `{"ok": true}`.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — documents the runner-side Docker
  collision, the 21-task scope, and suggested `docker rm -f` / uuid-suffix
  fixes in `docker_env.py` (out of meta-agent write scope).

### Evidence

- `task_000069_41f1682c.result.json`: `status:"error"`, `reward:0`,
  `elapsed_s:0.5`, error = docker "Conflict. The container name ... is
  already in use". No `task_000069_41f1682c.messages.json` exists.
- Round status distribution: 21 `error`(docker)/reward=0, 21 `ok`/reward=0
  (real agent failures — other proposals' territory), 7 `ok`/reward=1,
  1 `agent_error`. The 21 docker-error tasks pollute the failure signal.

### Uncertainty

If the Docker collision is transient (leftover container from a crashed
parallel run) it may not recur next round, in which case the underlying
task may show its true agent-phase behaviour. Any real fix must land in
the read-only runner, not in `HarnessConfig`.

## Round 2 — escalating loop-break

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-28T00:00:00Z
hypothesis_id: h_escalating_loop_break_v1
levers: [control, configuration]
predicted_affected: [task_000028_7fe033ac, task_000109_09ddd96b, task_001706_24462a09, task_001716_c1f2ac56, task_000936_2a78f3ca]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Attacks the largest evaluable-failure cluster: budget_exceeded (10 tasks, all reward 0). Gives the 4B model a forcing function to break loops it already recognises but never escapes; generalises to any loop-and-burn task."
regression_risk: "Low. All 7 passing tasks have max exact-consecutive tool-call run == 3 (peak task_000876=3, only warns, never raises at 6); none approach name-only run 14. Only pipeline change is swapping LoopDetectionProcessor for its subclass."
cost_shift: "Strongly negative (savings): pathological loops that ran to 80 steps now exit at <=6 exact / <=14 semantic repeats, reclaiming ~60-70 steps/task. Passing tasks unchanged."
rollback_trigger: "R3 budget_exceeded count not shrinking, OR any previously-passing task flips to loop_detected -> revert to stock LoopDetectionProcessor threshold=40."
-->

### Why

Assigned focus task_000028_7fe033ac exits `budget_exceeded` at 80 steps
(reward 0): it fixes the C++ backend (server returns frame count 150 over the
unix socket) but cannot diagnose the persistent nginx 502, and spends ~40 steps
cycling raw-socket/HTTP probes (curl/wget/nc all absent -> /dev/tcp -> python
socket), saying "I'm stuck in a loop trying to test the connection" every turn
while re-issuing. This is the dominant shape: 10/43 failures are
`budget_exceeded`, ALL reward 0 (21 of the 43 are docker container-name
conflicts with exit_reason=error, not agent failures — so budget_exceeded is
~45% of the *evaluable* failures). Every one fires the stock [LoopDetection]
warning 8-25 times yet runs the full budget: Strategy-2 (name-only) is
warn-only and Strategy-1's hard raise (threshold=40) is unreachable in 80 steps.
The model recognises the loop but the harness gives it no forcing function to
break it — some (task_000109: `ls -la` verbatim x25; task_001706: a hung
`pkill` x-many) are pure exact-match loops that only self-break with no budget
left.

### Changes

- `processors/loop_escalation.py` — new `EscalatingLoopBreakProcessor`
  (subclass of `LoopDetectionProcessor`): overrides `on_after_tool` to (1)
  escalate the name-only nudge into a decisive "commit-or-pivot" directive at
  `escalate_at`, and (2) add a bounded name-only `LoopDetectedError` raise at
  `name_threshold`. Only appends to the tool result string (contract-safe).
- `config.yaml` — replaced the stock `LoopDetectionProcessor` entry with a
  `file://` reference to the subclass; lowered exact-match `threshold` 40->6
  (passing cluster max exact-consecutive run is 3), set `escalate_at=6`,
  `name_threshold=14`, and raised `window_size` 12->16 so the name-only tail
  can reach 14. No other pipeline change.

### Evidence

- task_000028_7fe033ac result.json: exit_reason=budget_exceeded, steps=80,
  reward=0. Body msgs 37-51: repeated "I'm stuck in a loop trying to test the
  connection" with 26 distinct probe commands; nginx 502 never resolved.
- task_000109_09ddd96b: `ls -la /home/user/` repeated VERBATIM ~25x
  ("I'm stuck in a loop calling the same command"); breaks out to write
  pipeline.go only at step ~78. Pure exact-match loop old threshold=40 misses.
- task_001706_24462a09: `timeout 1 bash -c 'pkill -9 -f processor'` (exit 137,
  hangs) repeated many times to budget_exceeded.
- Pareto probe: all 7 passing tasks measured max exact-consecutive tool-call
  run == 3 (peak task_000876), well under the new raise at 6 -> zero regression
  on the observed passing cluster.

### Uncertainty

Honest retroactive check is partial-yes: a hard stop alone does not flip reward
(budget_exceeded and loop_detected are both 0), and task_000028's build was
genuinely broken so no early-exit flips it. The flip path is the *escalated
directive* arriving at run-length 6 giving recoverable tasks (verbatim/semantic
loops where work was nearly done) budget to pivot to the constructive action
they otherwise reached too late. Change cannot make any affected task worse;
downside is bounded and cost is strictly saved. Revert per trigger if
budget_exceeded doesn't shrink or a passer regresses.

## Round 2 — forced-action truncation recovery

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T18:00:00Z
hypothesis_id: h_length_recovery_forceact_v5
levers: [control]
predicted_affected: [task_000015_89886d8d, task_001717_a9c46d8d, task_001706_24462a09, task_000936_2a78f3ca, task_001898_471c0535]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the budget_exceeded + high-continue-count cluster (>=5 tasks) by reclaiming the 30-60% of step budget currently burned in zero-progress max_tokens narration spirals, letting these tasks reach and complete real work."
regression_risk: "Fires only after 3 CONSECUTIVE finish_reason=length no-tool-call turns — a state no passing task reaches this round (all passing tasks have maxNoToolStreak<=1). Injected Bash is read-only (pwd/ls/find/ps, all `|| true`); cannot mutate workspace; clears the streak after one round-trip. Text ladder unchanged for streaks <3."
cost_shift: "Net DECREASE on the affected cluster: one bounded Bash round-trip replaces 3-13 full 4096-token narration generations plus their continue round-trips. Zero change on tasks that never spiral (injection path never entered)."
rollback_trigger: "If R3 shows the budget_exceeded/high-continue cluster not shrinking, OR any previously-passing task regresses to loop_detected/error near a forced snapshot injection, revert to the stock v4 length_recovery processor."
-->

### Why

Assigned focus task_000015_89886d8d fails with `exit_reason=budget_exceeded`:
~half its step budget was consumed in a degenerate max_tokens narration spiral
(msgs 2-13: user "cut off by the token limit" continue nudge → assistant emits
the byte-identical narration "I need to stop the repetitive loop and take a
different approach..." with NO tool call → hits the 4096-token cap → repeat ×5)
before the first real Bash call at msg 14. The task then never recovered budget.
This is the dominant budget-drain shape this round: task_001717 (16 such turns),
task_001706 (9), task_000936 (7), task_001898 (6) — all reward=0,
`budget_exceeded`. The R1 config already carries a v4
`LengthTruncationRecoveryProcessor` whose `on_after_model` collapse demonstrably
FIRES (every truncated no-tool turn's stored content is collapsed to exactly the
head+tail budget of 1964 chars) yet an audit across all 29 transcripts shows the
v4 nudge TEXT ("issue exactly ONE concrete Bash", "You have now hit the output
token limit") appears zero times — the 4B model narrates straight past the text
directive. A reminder the model narrates past is not a fix; what breaks the
momentum is fresh tool output the model did not author.

### Changes

- `processors/length_recovery_forceact.py` — new v5
  `LengthTruncationRecoveryProcessor` (`MultiHookProcessor`, same
  `tmax_length_recovery` singleton group so it REPLACES the v4 entry, not stacks).
  Keeps the v4 collapse + escalating text-nudge ladder, and adds a mechanical
  forced-action escalation: after `force_action_threshold` (=3) consecutive
  `finish_reason=length` no-tool-call turns, `on_after_model` injects a real
  generic Bash workspace-snapshot tool call (pwd/ls/find recent files/ps, all
  read-only, `|| true`). The run loop executes it (verified: runloop.py L436-497
  dispatch the post-processor `model_event.tool_calls`; injecting a tool call
  flips `_length_truncated` False so no further passive nudge is added), landing
  fresh ground truth in context to break the spiral. Fires at most once per
  streak (the injected tool call clears it). Also adds a secondary content-length
  trigger for backends that don't report `finish_reason=length`.
- `config.yaml` — replaced the
  `recipe.tmax_eval.processors.length_recovery.LengthTruncationRecoveryProcessor`
  entry with a `file://` reference to the new class; kwargs
  `repeat_threshold=2, force_action_threshold=3, head_chars=400, tail_chars=0,
  content_char_threshold=40000`. No other pipeline change.

### Evidence

- task_000015_89886d8d result.json: `finished=budget_exceeded`, reward=0, 80
  steps. messages msgs 2-13: 5 identical no-tool-call narration turns interleaved
  with the run loop's passive continue nudge before any real Bash call.
- task_001717_a9c46d8d: 16 truncation/continue turns; msgs 9,13,19,23,27,31,33
  each = collapsed no-tool-call content len=1964, no tool call; `budget_exceeded`.
- task_001706 (9), task_000936 (7), task_001898 (6): same shape, all
  `budget_exceeded`, reward=0.
- Audit: across all 29 `.messages.json`, the v4 recovery nudge strings appear 0
  times while 40+ passive "cut off by the token limit" messages appear — the
  text nudge is not breaking the loop.

### Uncertainty

The forced snapshot relies on the proven "verbose models switch back to acting
when a real tool result lands" mechanism (already used by the tb2_self_verify
lifecycle processor and the rep1-i3 v5 lineage). Risk: if the 4B model resumes
narrating even after the fresh snapshot, we spent one bounded Bash round-trip
per spiral for no flip — but that is strictly cheaper than the status quo of
6-16 wasted full generations, and the injection cannot fire on healthy runs
(needs 3 consecutive truncations, which no passing task reaches). Contract /
dry_fire / canonicalize / literals all pass clean.
