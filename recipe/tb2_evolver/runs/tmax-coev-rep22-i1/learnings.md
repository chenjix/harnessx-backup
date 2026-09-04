## Round 1 — semantic repetition breaker

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_semantic_repetition_breaker_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_000958_4bb2b05d, task_001032_1adaccb9, task_001321_658ce4a8, task_000015_89886d8d, task_001031_a8f0eb37]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=26/50; +2/-6 gained=task_001090_c61c71f2,task_001321_658ce4a8 lost=task_000684_1a33ef37,task_000912_770802f8,task_001089_220cc46b,task_001515_eed714e6; gating disabled (tolerance < 0)
expected_global_gain: "Breaks non-converging diagnostic loops that burn 40-80 steps on high-step failures; reclaims budget and plausibly flips 1-3 tasks where real work was reachable once the loop is broken"
regression_risk: "Conservative thresholds; offline replay raised on 0/30 passing tasks, warned 1. Worst case: a doomed task exits a few turns earlier (same reward 0)"
cost_shift: "Net negative — early termination of 57-80 step loops down to ~10-29 steps; nudge adds at most one short message on a few tasks"
rollback_trigger: "If any currently-passing task regresses to exit_reason=loop_detected, or pass_rate drops, revert"
-->

### Why

Assigned task `task_000118_3043e92d` (write a disk-quota monitor daemon)
failed with the log dir peaking at 200 MB vs the 45 MB threshold — the
monitor never truncated. But the graded artifact is the final script (the
verifier starts the monitor itself), and the agent never realised that: the
whole visible trajectory is a non-converging diagnostic loop. It repeatedly
runs `python3 monitor.py &` then `ps aux | grep`, sees defunct zombies,
concludes "the monitor is not showing up in the process list", and repeats
~15x with slightly-varied args, twice hitting the token limit, ending
no_tool_calls having never run the real end-to-end test or fixed the
script's timing bug. The same shape recurs across at least five other
high-step failures (`task_000958`, `task_001032`, `task_001321`,
`task_000015`, `task_001031`): passing tasks rarely exceed ~28 steps, while
these burn 57-80. Existing guards miss it — LengthTruncationRecovery only
fires on finish_reason=length, and LoopDetectionProcessor (not even in the
R0 config) keys on exact/consecutive tool-arg fingerprints, which vary here.

### Changes

- `processors/semantic_repetition_breaker.py` — new `SemanticRepetitionBreaker`
  MultiHookProcessor. `on_after_model` measures Jaccard token-overlap between
  the current assistant narration and recent turns; warns (injects one
  corrective redirect via `on_before_model`, net +1) at 3 near-dupes and
  raises `LoopDetectedError` at 4 (window=8, sim=0.80), so the run exits
  cleanly and recovers the last-good filesystem instead of exhausting budget.
- `config.yaml` — register the processor (file:// abs path) after
  LengthTruncationRecoveryProcessor and before CompactionProcessor.

### Evidence

- `task_000118_3043e92d` result: reward=0, steps=59, finished=no_tool_calls;
  final_pytest peak size exceeded threshold. Msgs 55/57/.../71 repeat verbatim
  "The monitor script is not showing up in the process list. Let me try..."
  and "I see there are zombie processes"; two turns truncate at the token
  limit mid-repetition.
- `task_001321_658ce4a8` (budget_exceeded, 80 steps): 14 verbatim repeats of
  "The error is still appearing. This is strange..." plus the existing
  "I've been stuck in a loop" nudge firing and being ignored.
- `task_000015_89886d8d` (57 steps): 6 repeats of "The tests are failing
  because the URL decoder..." + 5 of "The user is telling me to stop the
  repetition..." — nudge fired, agent could not escape.
- `task_000958` (4 repeats), `task_001032` (7 repeats), `task_001031`
  (5 repeats) — same non-converging narration shape.
- Offline replay of the detector over R0 trajectories (window=8, sim=0.80,
  warn=3, raise=4): 5/6 failing loop-tasks hard-raise at turns 10-29;
  0/30 passing tasks raise; 1 passing task (task_000578) warns once.

### Uncertainty

The raise on `task_000118` does not fire (its narration varies just enough);
it relies on the repeated early warn nudge redirecting the agent to verify
the file / fix the logic rather than re-check process state. If the model
ignores the nudge like it ignored the existing loop nudges, the task stays
failed — but budget is still reclaimed for the rest of the round. Watch for
any passing task flipping to exit_reason=loop_detected (false positive);
none appeared in offline replay.

## Round 2 — verifier dependency readiness

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_verifier_dep_readiness_v1
levers: [instruction]
predicted_affected: [task_000028_7fe033ac, task_000958_4bb2b05d]
cited_candidates: [C-002]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the HTTP/network-service cluster where the agent's work is correct but the verifier's Python test module fails to collect on a missing third-party import (requests); generalizes to any task whose grader imports a common lib not preinstalled"
regression_risk: "Low — best-effort idempotent install-if-missing step run only after objectives are done; no-op when offline; cannot break tasks whose verifier had no missing deps. Worst case a couple extra Bash steps"
cost_shift: "Mildly positive — 1-3 short extra Bash calls near end of service tasks; negligible tokens, no extra model round-trips beyond the agent loop"
rollback_trigger: "If any previously-passing task regresses (install command errors / agent loops on it) or global pass_rate drops, revert the sibling prompt to the R1 version"
-->

### Why

Assigned task `task_000028_7fe033ac` (fix nginx→C++ UNIX-socket video
frame-count service) failed with reward=0 despite the agent completing
the work correctly: its own probe returned `HTTP/1.1 200 OK ... 150`,
both services were left running, socket perms fixed. The failure is
entirely in the verifier phase — `final_pytest` crashed at pytest
*collection* with `ModuleNotFoundError: No module named 'requests'`
because the injected `test_final_state.py` does `import requests` and
that library is not preinstalled in the container's Python. Per the
tb2-playbook, verifier test files appear only after the agent exits, so
the agent never sees the import and never thinks to make the runtime
ready for it. The same collection crash recurs on
`task_000958_4bb2b05d` (also `import requests`). This is a harness
knowledge gap, not a task-logic gap.

### Changes

- `system_prompt.txt` (sibling read by SiblingSystemPromptBuilder) —
  append a general "verifier readiness" rule: after objectives are
  done, if the task involves a service/endpoint/data-format a grader
  would exercise programmatically, confirm the container's Python can
  import the common libraries such checks depend on (e.g. an HTTP
  client) and install any that are missing (best-effort, skip if
  offline). No task-specific literals, no copy-paste code.
- `config.yaml` — byte-identical copy of R1 config; the intervention
  is delivered entirely through the new sibling prompt.

### Evidence

- `task_000028_7fe033ac` result.json: `final_pytest.rc=2`,
  `/tmp/test_final_state.py:6: in <module> import requests` →
  `E ModuleNotFoundError: No module named 'requests'` →
  `Interrupted: 1 error during collection`. Agent trajectory tool
  result: `HTTP/1.1 200 OK ... Content-Length: 3 ... 150` (service
  correct).
- `task_000958_4bb2b05d` result.json: same collection crash at
  `test_final_state.py:4: in <module> import requests`.
- Installs reach PyPI at runtime in these containers:
  `task_000956_7e92337f` shows `Installing collected packages:
  python-Levenshtein\nSuccessful...`; `task_001031_a8f0eb37` shows a
  biopython wheel downloading — so `pip install requests` is viable.

### Uncertainty

The model may ignore the rule (prior loop-nudges were ignored). If so,
tasks stay failed at the same reward — no regression. `task_000028` is
the clean expected flip (work already correct); `task_000958` also hit
budget_exceeded, so its underlying work may be incomplete and the flip
is less certain. Watch for any passing task regressing due to a noisy
install step; revert the prompt if so.

## Round 2 — exact-duplicate loop guard

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_exact_loop_detection_v1
levers: [configuration]
predicted_affected: [task_000015_89886d8d, task_001031_a8f0eb37]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the exact-duplicate tool-call loop cluster (2+ tasks): breaks byte-identical Bash-call loops early via warn at 3 / raise at 5, reclaiming 40-75 steps of budget and giving a real path to writing deliverables"
regression_risk: "Strategy-1 raise needs 5 consecutive byte-identical calls; legitimate exploration varies args and resets the run counter, so false-positive termination is near-zero. Strategy-2 name-only is warn-only. Worst case: a task genuinely polling an identical command 5+ times in a row exits early (same reward 0)"
cost_shift: "Net negative - terminates 80-step / 42-step non-converging loops at about step 5; adds at most one short warning string on a tool result"
rollback_trigger: "If any currently-passing task regresses to exit_reason=loop_detected, or pass_rate drops, revert"
-->

### Why

Assigned task `task_000015_89886d8d` (parse a routing schema from an OCR
image, write `/home/user/migrate.py` + `/home/user/test_parser.py`) failed
with exit_reason=budget_exceeded at 80 steps having produced neither required
file. The trajectory is a pure exact-repetition loop: OCR of
`/app/routing_schema.png` returns a stable garbled string, and the agent emits
the byte-identical OCR Bash heredoc 33 times in a row (distinct args = 1),
getting the identical garbled result each time, narrating verbatim "The OCR is
consistently garbled. Let me try a different approach...". It had already
inferred a plausible schema in its first narration but never stopped to write
the code. The R1 config in play has NO exact-fingerprint loop guard (R1's
accepted SemanticRepetitionBreaker is not present in it). The same exact-loop
shape recurs in `task_001031_a8f0eb37` (16 consecutive byte-identical calls,
exit_reason=error). This is the cheapest, highest-precision loop shape, and
the builtin LoopDetectionProcessor (two-phase warn/raise on exact fingerprints,
compaction-aware) is the right primitive.

### Changes

- `config.yaml` - register builtin
  `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
  (window_size=12, warn_threshold=3, threshold=5, name_warn_threshold=8,
  compaction_drop_threshold=5) after PostCompactionRefreshProcessor. No
  authored files - pure Configuration lever (wire in an existing component).

### Evidence

- `task_000015_89886d8d` result.json: reward=0, steps=80,
  exit_reason=budget_exceeded; final_pytest fails "Migration script not found
  at /home/user/migrate.py" and "Test script not found at
  /home/user/test_parser.py". messages.json: 33 assistant tool calls, all 33
  byte-identical (longest consecutive identical run = 33); every tool result
  is the same garbled "Legacy toV2 Schema Mapping 1 eatalogyitemyitem_id> ...".
- `task_001031_a8f0eb37` result.json: reward=0, steps=42, exit_reason=error.
  messages.json: 42 calls, longest consecutive byte-identical run = 16.
- Strategy 1 (exact fingerprint) raises at 5 consecutive identical calls, so
  both loops terminate at about step 5 instead of 80/42; warn at 3 injects a
  "try something fundamentally different" redirect early, with budget and the
  already-inferred schema still in hand.

### Uncertainty

The warn nudge may be ignored like R1's prior nudges - then the task still
fails, but budget is reclaimed for the round and a clean loop_detected exit
recovers the last-good filesystem. The raise is on the tool-call fingerprint,
so a legitimate identical-poll pattern (rare) could trip it; warn fires first
at 3 giving the agent a chance to vary. Watch for any passing task flipping to
exit_reason=loop_detected (false positive) - none of this shape appears in the
passing clusters.

## Round 2 — teardown exit-hygiene nudge

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_lifecycle_self_verify_v2
levers: [control]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Teardown/cleanup class (system_administration lifecycle tasks graded on empty pgrep at exit); guidance is domain-agnostic and generalizes to any start-then-stop pipeline task"
regression_risk: "Near-zero: edit only extends text inside the already-firing one-shot cleanup branch; adds no new trigger, no new hook, no exit block. Keep-alive tasks read the unchanged keep branch. Measured 13 passing keep-alive tasks would NOT be affected because the change adds no trigger."
cost_shift: "Negligible: +~60 tokens on the single self-verify injection that already fires once per task; zero extra turns"
rollback_trigger: "If any currently-passing keep-alive service task flips to failing because the agent killed a daemon it should have kept, revert to R1 lifecycle_self_verify."
-->

### Why

Assigned task `task_000140_01c78b42` (fix a Go VM-provisioning service +
supervisor + build a start-then-stop CI/CD pipeline). The verifier
(`test_no_lingering_service_processes`) asserts `pgrep -f vm_service` is empty
after the agent exits. The agent's *code* was correct (all three files right,
vm_setup.log got the expected line) but its *exit hygiene* failed: verifier
found lingering PIDs 342, 595, 849, 1075, 1274. This lineage's own R1 lifecycle
self-verify nudge fired (msg 213) and its cleanup branch already told the agent
"pgrep must return nothing", but the agent "verified" by **re-running its
start-then-stop pipeline several times** — each run does `./vm_service &` then
kills only the latest saved PID, so orphans from earlier runs accumulated, and
its final tool action started a fresh service before it exited. Right code,
wrong final-state hygiene; the nudge did not name the specific re-run-as-
verification trap.

I rejected a blunt mechanical guard (re-arm nudge / block exit on background-
start commands): measured 13 currently-passing tasks issue a background start
*after* the self-verify nudge and legitimately keep the service alive
(000338, 000740, 000818, 000965, 001090, 001264, 001498, 001591, 001652,
001673, 001697, 001706, 001761). A trigger that can't read keep-alive vs
teardown intent from Bash alone would nudge those passers to kill live services
— net-negative Pareto. So the fix is scoped to add zero new triggers.

### Changes

- `processors/lifecycle_self_verify.py` — extend ONLY the cleanup/teardown
  branch of the one-shot `_LIFECYCLE_ADDENDUM` with a general warning: (a)
  re-running a start-then-stop pipeline is not a clean shutdown verification —
  each run restarts the service, so the last action must be a teardown you then
  confirm; (b) killing one saved PID can miss orphans; after any teardown run
  `pgrep -f <name>` as the final check and kill every listed PID until empty.
  General strategy only — no task ids, ports, paths, or binary names. Keep
  branch and resource-bound branch unchanged.
- `config.yaml` — repoint the `LifecycleSelfVerifyProcessor` `file://` target
  from the R1/c4 copy to this round's `_meta_v2/R2/c4/processors/` copy.
- `system_prompt.txt` — sibling copied byte-for-byte (unchanged content).

### Evidence

- `task_000140_01c78b42` result: reward=0, finished=no_tool_calls, 22 steps;
  final_pytest `test_no_lingering_service_processes` AssertionError "Lingering
  vm_service processes found: ['342','595','849','1075','1274']".
- Body: msg 213 `_tb2_self_verify` fired; msg 279 `pgrep -f vm_service` → "342
  383"; msgs 319/339/379/479 re-run `test_pipeline.sh` (each `./vm_service &`);
  msg 511 final assistant message with no tool call, services still up.
- Pareto measurement (offline over the 50-task batch): 13 passing tasks issue a
  background-start command after the self-verify nudge and remain passing —
  the reason a blunt teardown guard is rejected and this edit adds no trigger.

### Uncertainty

The nudge already fired once and was ignored, so a flip is not guaranteed —
this names the specific trap the prior wording missed. If the model still
ignores explicit, trap-named cleanup guidance, the residue is a pure model
capability gap (exit-hygiene discipline) with no safe harness fix, and the
task stays failed at zero regression cost.

## Round 2 — constraint-reconciliation self-verify

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_constraint_reconciliation_verify_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_001032_1adaccb9]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Sharpens the one-shot exit self-verify for the constraint-bound subset of the large done-but-failed cluster (agents that declare SUCCESS while their own tool output showed an over-limit measurement); plausibly flips 1-2 tasks where budget and a reachable fix remained"
regression_risk: "Low — strictly additive text on the already-firing one-shot self-verify nudge; fires at most once, blocks/rejects no tool call, no measurement auto-detector so no currently-passing task (e.g. task_001090) is gated"
cost_shift: "Negligible; self-verify already fires on these tasks. Adds a few hundred tokens to one message; may reduce cost by preventing premature done exits"
rollback_trigger: "If any currently-passing done-task regresses to reward=0, or pass_rate drops with no flip on task_000118/task_001032, revert to LifecycleSelfVerifyProcessor"
-->

### Why

Assigned focus `task_000118_3043e92d` (disk-quota monitor daemon). In the R0
trajectory the run is short and clean (steps=19, exit_reason=done) — the R1
semantic-repetition angle is no longer the blocker. The decisive error: the
agent ran its solution end-to-end, printed a directory listing showing the logs
dir at 200 MB (over the 45-50 MB limit), then concluded "all 20 workers
completed ... the monitor triggered protection" and exited "✅ complete". It
reasoned backwards from workload completion to constraint-compliance and ignored
the over-limit number it had itself printed. The existing R1/c4 lifecycle
self-verify fired but the agent satisfied it by re-grepping its own source for
keywords rather than re-running and re-measuring. This "confidently declares
SUCCESS but scored 0" exit shape recurs across the whole done-but-failed cluster;
the constraint-reconciliation variant (stated numeric limit + self-printed
over-limit measurement) is the tightest defensible sub-cluster. A residual model
capability gap remains (the truncate/SIGSTOP logic is genuinely buggy); that is
not patched here per SOUL.md.

### Changes

- `processors/constraint_self_verify.py` — new `ConstraintVerifyProcessor`
  extending the stock `CustomSelfVerifyProcessor`. Keeps the R1/c4 lifecycle
  checklist item (6) verbatim and appends a general item (7): workload
  completion is not evidence a numeric limit held; an already-observed
  over-limit measurement means the solution failed; the only valid confirmation
  is to reproduce the graded scenario from a clean state and read the peak
  against the limit (grepping source is not a measurement). No task-specific
  literals, paths, or constants.
- `config.yaml` — replace the `LifecycleSelfVerifyProcessor` entry with
  `ConstraintVerifyProcessor` (same `_singleton_group="tb2_self_verify"`, so it
  supersedes rather than double-fires).

### Evidence

- `task_000118_3043e92d` result: reward=0, steps=19, exit_reason=done.
- msg 18: tool output `total 204812` + 20× `10485760`-byte files (200 MB logs).
- msg 19: assistant — "all 20 workers completed successfully ... the monitor
  triggered the protection mechanism" / "working correctly".
- msg 32-37: `_tb2_self_verify` fired; agent re-grepped source
  (`✓ Threshold check (40 MB)`) and exited "✅ complete" without re-running.
- `task_001032_1adaccb9`: reward=0, states a numeric limit, prints size
  measurements, ends with a completion claim — same reconciliation miss shape.

### Uncertainty

The item is advisory text; a model that ignored item (6) may ignore item (7).
But it directly names the exact wrong inference the agent made ("completed ≠
constraint held" and "an over-limit number you printed = failure"), which the
prior addendum did not. Residual: the underlying truncation bug may still not
get fixed even if the agent re-measures — in that case the task stays failed at
near-zero regression cost. Watch for any passing done-task flipping to reward=0.

## Round 2 — killed-output diagnostic

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_killed_output_diagnostic_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_001321_658ce4a8, task_000958_4bb2b05d, task_001031_a8f0eb37]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the opaque SIGKILL/timeout -> verbatim retry loop failure mode on long-running-service / daemon / port-forward tasks; turns the uninformative (exit 137, no output captured) wall into a recoverable, actionable signal and caps wasted turns even when the task stays unsolved"
regression_risk: "Low. on_after_tool fast-path returns unless the result contains BOTH the no-output marker AND a kill exit code (137/143/124/152) — a shape absent from clean output, so short-step passing tasks are untouched. Nudges capped at 4 per task, only escalate on consecutive kills; before-model injection is contract-safe"
cost_shift: "Net negative to neutral: zero added tokens on the common path; replaces ~26 wasted verbatim turns on killed-loop tasks with an early redirect/recovery"
rollback_trigger: "Revert if pass_rate drops, if a previously-passing task begins emitting the nudge and then fails, or if the killed-output shape false-positives on non-service tasks"
-->

### Why

Assigned task `task_000010_644ab1c2` (write a Python k8s operator that backs up
manifests, sets up a 9090->8080 port-forward, and drives an interactive CLI)
failed budget_exceeded at 80 steps with reward 0. The trajectory is a
non-converging loop: the agent runs the byte-identical command
`python3 /home/user/port_forward.py &; sleep 2; <port check>` ~13 times and each
time receives the single opaque line `(exit 137, no output captured)`. Exit 137
= 128 + SIGKILL(9): the sandbox wraps every command as
`setsid bash -c <cmd> & ...; wait $_hx_pid` with a 30s per-command timeout
(`benchmarks/terminal_bench_2/harbor_sandbox.py` lines 84-114), and the agent's
launch-then-test-in-one-command pattern blocks on `wait`, gets SIGKILLed at 30s,
and returns nothing. The model reads the empty result as "port not listening
yet" and reruns verbatim until budget is exhausted. This is a harness
deficiency, not a knowledge gap: the sandbox strips the one fact the agent needs
(the command was killed). The same opaque-kill-then-retry shape underlies the
R1-flagged high-step budget_exceeded loops (task_001321, task_000958,
task_001031).

### Changes

- `processors/killed_output_diagnostic.py` — new `KilledOutputDiagnosticProcessor`
  (MultiHookProcessor). `on_after_tool` classifies each Bash result: if it
  contains the no-output marker AND a kill exit code (137/143/124/152), it flags
  a pending nudge and tracks the consecutive-kill streak; a non-killed result
  resets the streak. `on_before_model` injects a one-shot (escalating at >=2
  consecutive) actionable diagnostic — explains SIGKILL/timeout semantics and a
  general strategy for launching persistent services detached with output
  capture (nohup redirect + disown) and probing in a separate command.
  Capped at max_nudges=4 per task; contract-safe trailing-user merge.
- `config.yaml` — register the processor (file:// abs path) after
  LengthTruncationRecoveryProcessor (order 6) and before CompactionProcessor.

### Evidence

- `task_000010_644ab1c2` result.json: reward=0, steps=80, exit_reason=budget_exceeded.
- messages.json msg 4 command starts a port_forward with a bare `&`, sleeps,
  then checks port 9090; msg 5 result = `(exit 137, no output captured)`.
- msg 43 command byte-identical to msg 4; msg 44 result identical. msg 56 again.
  ~13 identical `(exit 137, no output captured)` results across msgs 5..57.
- Root cause: `harbor_sandbox.py` lines 110-111 emit `(exit {rc}, no output
  captured)` after the wrapped command is SIGKILLed at the 30s timeout.

### Uncertainty

The nudge relies on the model acting on the redirect (launch detached + read
log) rather than ignoring it as it ignored the passive re-runs. If the model
still cannot make the service start non-blocking, the task may stay failed — but
the escalating REPEAT nudge and the cap prevent the ~26-turn verbatim loop,
reclaiming budget for the rest of the round. Watch for any passing task emitting
the injected nudge (would indicate a false-positive on the killed-output shape).
