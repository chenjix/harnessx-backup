# Evolve Journal — tmax-coev-rep21-i1

## Round 1 — lingering process guard

<!-- journal:frontmatter
round: 1
timestamp: 2026-04-27T00:00:00Z
hypothesis_id: h_lingering_process_guard_v1
levers: [control]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=17/50; +4/-12 gained=task_000740_59416444,task_001032_1adaccb9,task_001089_220cc46b,task_001781_529727cf lost=task_000329_a3ac56b0,task_000344_e265c898,task_000396_e56917e2,task_000536_9c16e8ef; gating disabled (tolerance < 0)
expected_global_gain: "Flips task_000140 and protects the system_administration cluster (0/5 in R0) whose verifiers check for lingering/duplicate service processes from re-run orphans"
regression_risk: "Extra keepalive turn near exit could interact with CustomSelfVerifyProcessor; mitigated by ordering (92 > 90) and fire-once-only + duplicates-only gating"
cost_shift: "Negligible: one ps call + one short message + one model turn, only on the rare duplicate-orphan path"
rollback_trigger: "If R2 pass_rate is flat/down AND any previously-passing background-service task regresses (T->F), revert"
-->

### Why

`task_000140_01c78b42` (system_administration) fails on
`test_no_lingering_service_processes`, which asserts no `vm_service`
processes remain after the agent exits (final pytest lists lingering PIDs
`['347','572','849','1061']`). The agent actually solved the task — main.go,
start_service.sh, and test_pipeline.sh were all correct — but it re-ran
`test_pipeline.sh` multiple times to verify it (the log ends with the
`PROVISIONED_VM_FOR: admin_alice` line written twice). Each run started a
fresh `./vm_service &`; the pipeline's shutdown only killed the single PID in
`service.pid` (the last one), orphaning the rest. The agent had no visibility
into the cumulative side effects of its own iterative testing — every Bash
call is stateless from its perspective. This is a harness deficiency
(runtime awareness gap), not a capability gap.

### Changes

- `processors/lingering_process_guard.py` — new `LingeringProcessGuard`
  MultiHookProcessor. Passively tracks background launches (`&`/`nohup`/
  `setsid`/`disown`); at exit intent, if background work was launched, reads
  the live `ps` table and, only when it finds >=2 instances of the same
  command signature (re-run orphan fingerprint), injects one informational
  warning listing the duplicate PIDs and asks the agent to clean up
  deliberately. Never kills anything. Ordered 92 (after self-verify's 90).
- `config.yaml` — register `LingeringProcessGuard` at the end of the
  processor pipeline via absolute `file://` path.
- `system_prompt.txt` — copied byte-for-byte from R0 (unchanged).

### Evidence

- `task_000140_01c78b42` final_pytest: `AssertionError: Lingering vm_service
  processes found: ['347','572','849','1061']`; `agent.finished=no_tool_calls`.
- `task_000140_01c78b42` messages step ~10 (`chatcmpl-tool-bd6c9799`): runs
  `bash /home/user/test_pipeline.sh` (exit 127); `vm_setup.log` = 1 line.
- `task_000140_01c78b42` messages step ~14 (`chatcmpl-tool-ac013bcf`): edits
  pipeline and runs it AGAIN; `vm_setup.log` = 2 lines → a second service
  instance started, only the last PID killed.
- Batch context: background launches are structural — >=2 launches on ~36 of
  50 tasks (7 on task_000140), so accidental re-run orphans are a recurring
  hazard, not a one-off. `task_001090_c61c71f2` also asserts on process state
  but its bug is exec-vs-bash-wrapper (a distinct knowledge gap), so it is
  NOT claimed as a flip here.

### Uncertainty

The guard depends on the agent acting on the injected warning within its
remaining turn budget; a very-late exit could leave no room to `kill`. It is
strictly informational to avoid regressing tasks that must keep a single
service alive (system prompt explicitly instructs that), and it fires only on
duplicates, so false positives on legitimate single-service tasks are
unit-test-confirmed to be zero. If R2 shows a background-service task
flipping T->F, the ordering/keepalive interaction with self-verify is the
first suspect — revert.

## Round 2 — infra container conflict, no-op

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T15:30:00Z
hypothesis_id: h_no_harness_fix_container_conflict_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — the assigned failure is a pre-harness docker startup error, not addressable by config; no-op protects the 30 currently-running tasks from a fabricated change."
regression_risk: "None — byte-for-byte copy of current_config."
cost_shift: "Zero — no change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus `task_000140_01c78b42` fails with `status: error`,
`elapsed_s: 0.7`, `agent: null`, `final_pytest: null`, and NO
`messages.json` — the agent phase never ran. The error is a Docker
container-name conflict at `docker run` time:
`Conflict. The container name "/tmax-task00014001c78b42-<ts>" is
already in use ...`. A stale container from a prior run was not reaped
before re-launch. This happens in `recipe/tmax_eval/docker_env.py`
(read-only), strictly *before* any HarnessConfig processor / tool /
system prompt loads. It is the SAME error on 19/50 tasks this round
(000028, 000140, 000264, 000329, 000338, 000505, 000536, 000748,
000760, 000863, 000912, 000956, 001088, 001264, 001653, 001697,
001701, 001706, 001761) — systemic infra, not a per-task capability
or harness-mechanism gap.

### Changes

- `config.yaml` — byte-for-byte copy of R1 `current_config` (explicit
  no-op). Canonicalizes: `{"ok": true, "checked_templates": 0}`.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — flags the runner
  stale-container-reap bug and its 19 affected tasks for a human fix
  outside the evolvable surface.

### Evidence

- `task_000140_01c78b42.result.json`: `"status": "error"`,
  `"elapsed_s": 0.7`, `"agent": null`, `"error": "RuntimeError: docker
  run failed ... Conflict. The container name ... is already in use"`.
- No `task_000140_01c78b42.messages.json` exists — agent never
  executed, so no processor/prompt hook could have fired.
- 19/50 result.json files share the identical `docker run failed ...
  Conflict` error string — confirms systemic, not task-local.

### Uncertainty

If a future round's runner reaps stale containers, these 19 tasks will
begin actually executing the agent and their *real* pass/fail behaviour
(and any genuine harness gaps) will become visible for the first time.
Until then, no config lever can move `task_000140`.

## Round 2 — docker infra flake, no-op

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T15:30:00Z
hypothesis_id: h_docker_conflict_noop_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — assigned failure is infra, not config-addressable; no-op protects the 30 tasks that ran"
regression_risk: "None — config + system_prompt copied byte-for-byte from R1"
cost_shift: "Zero"
rollback_trigger: "N/A (no-op)"
-->

### Why

Assigned focus `task_000028_7fe033ac` did NOT fail on a model or harness
capability gap. Its `result.json` is `status=error`, `elapsed_s=0.8`,
`agent=null`, `final_pytest=null`, with error `docker run failed ... Conflict.
The container name "/tmax-task0000287fe033ac-1788159877" is already in use`.
The agent phase never started. `config.yaml` governs only the in-agent
processor/prompt surface; nothing it can express touches a container that
dies before the run loop boots. This is a docker orchestration flake, not a
harness deficiency. It is systemic: **19 of 50 tasks (38%)** errored with the
identical container-name Conflict this round (full list in
`_meta_scratch/NEEDS_FROM_HUMAN.md`). Notably `task_000140_01c78b42` — R1's
sole predicted-affected task — is among the 19, so R1's attribution for the
lingering-process guard is unreliable: that task never ran the agent this
round.

### Changes

- `config.yaml` — copied byte-for-byte from R1 (md5 f3266f41...). Explicit no-op.
- `system_prompt.txt` — copied byte-for-byte from R1 (md5 914fb4a8...).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — diagnosis + suggested human-side fixes
  (all in read-only `recipe/tmax_eval/docker_env.py::start_container`: pre-run
  `docker rm -f`, uuid suffix instead of `int(time.time())`, between-run
  sweep, or retry-on-Conflict).

### Evidence

- `task_000028_7fe033ac` result.json: `status="error"`, `elapsed_s=0.8`,
  `agent=null`, `error="... Conflict. The container name ... already in use"`.
- Root cause: `docker_env.py::start_container` names containers
  `f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]` — collides
  when a prior attempt's container wasn't `docker rm`'d or two attempts land in
  the same integer second.
- Batch: 19/50 error tasks share the identical Conflict signature (ok=30,
  error=19, agent_error=1).

### Uncertainty

If the human applies a docker-cleanup fix, several of these 19 "failures" may
resolve to genuine pass/fail and the real config gaps become visible. Until
then, ~38% of this benchmark's failures are infra noise that corrupts every
hypothesis's attribution signal — including R1's. No config change is
warranted this round.

## Round 2 — command-keyed loop breaker (c3)

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T15:30:00Z
hypothesis_id: h_repeated_toolcall_recovery_v1
levers: [control]
predicted_affected: [task_000118_3043e92d]
cited_candidates: [C-002]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the drift-immune subset of the stuck-loop cluster: loops on polling commands (ps/du/top/date/tail) whose output ticks a PID/RSS/timestamp so the existing result-keyed guard never fires; returns wasted step budget so the agent can iterate"
regression_risk: "False-positive nudge if a task legitimately re-runs the same command 3 times in a row; mitigated by threshold=3 (any interleaved different command resets), advisory-only injection, contract-clean"
cost_shift: "Net negative-to-neutral: saves ~15-20 wasted model turns on loop-path tasks; adds nothing on the common path; worst case one short message on a rare legit-repeat-poll task"
rollback_trigger: "If R3 pass_rate flat/down AND a previously-passing task regresses T-to-F with evidence the nudge interrupted a legitimate repeated poll, revert"
-->

### Why

Assigned focus task_000118_3043e92d (system_administration) DID run the agent
(this one is not an infra-conflict no-op). It fails final_pytest with peak log
dir 203915264 bytes over the 45000000 threshold, but the harness deficiency is
upstream: the agent spent about 20 consecutive turns emitting the byte-for-byte
identical Bash call (ps aux piped to grep for worker_sim/python3) with identical
narration and never fixed the monitor. The existing
RepeatedCommandRecoveryProcessor did NOT catch this because it keys its
fingerprint on the RESULT text, and the ps result drifted every call (monitor
RSS column ticked 10288 up through 10380, STAT flipped between S and R). It saw
about 20 distinct fingerprints, run-length stayed at 1, and it stayed silent.
The loop only broke when PostCompaction fired, by which point most of the step
budget was gone. This is the drift-immune subset of the stuck-loop cluster the
R1 journal flagged as systemic (15/25 r0 failures had 4-plus consecutive
identical calls).

### Changes

- processors/repeat_toolcall_recovery.py: new RepeatedToolCallRecoveryProcessor.
  Fingerprints each call by (tool_name, tool_input) at on_before_tool (the cause
  side), independent of result drift. Counts consecutive identical commands; at
  repeat_threshold=3 injects one generic corrective user message (escalating
  form after 2 more) telling the agent the repeated command is not advancing the
  task and to take a materially different action. Message-injection only; never
  blocks or kills; merges onto a trailing user message (contract-clean). Order 7,
  adjacent to the existing result-keyed guard, before compaction.
- config.yaml: copied from R1, added the new processor via absolute file:// path
  right after RepeatedCommandRecoveryProcessor.
- system_prompt.txt: copied byte-for-byte from R1 (unchanged).

### Evidence

- task_000118_3043e92d.messages.json steps ~1-28: identical assistant message
  plus identical ps-grep call about 20 times; each tool result differs only in
  the RSS column (10288..10380) and STAT flag, so the result-keyed guard
  structurally cannot fire.
- task_000118_3043e92d.result.json: agent.steps=56, finished=no_tool_calls,
  final_pytest peak 203915264 over 45000000; the agent never re-ran the
  deployment end-to-end to notice the overshoot.
- Retroactive: keying on (tool_name, tool_input) reaches run-length 3 at the
  3rd identical call, so the nudge fires after about 3 turns instead of 20-plus.

### Uncertainty

Fixing the loop returns budget but does not guarantee the model then writes a
correct monitor (the 40MB-truncate-then-resume design still overshoots the 45MB
peak on 20 workers times 10MB; that residual is a capability gap, not a harness
one). The bet is that reclaimed iteration turns raise the odds the agent
notices and fixes the overshoot, and more importantly that the guard
generalizes across the polling-loop cluster benchmark-wide. If R3 shows a task
regressing because a legitimate repeated poll got interrupted, that is the first
suspect, revert.

## Round 2 — mechanical loop breaker (c1)

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T16:00:00Z
hypothesis_id: h_loop_breaker_hard_stop_v1
levers: [control]
predicted_affected: [task_000015_89886d8d, task_001818_b251e5ea, task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips a slice of the dominant failure cluster: 7-plus reward=0 tasks where the small model perseverates on a byte-identical failing command (max identical-result repeat 9-34x) and burns its whole budget without writing deliverables. Distinct from c3 (soft nudge on polling drift); this is a hard mechanical refusal on stable identical failures."
regression_risk: "A legitimate poll/retry loop with byte-identical output across 6-plus attempts could be refused; mitigated by high threshold (6), refusing only the exact offending signature, one-shot-per-signature, and state-changing polls producing changing output so never accumulate"
cost_shift: "Net reduction - cutting a 10-34x identical-command loop early saves the repeated executions plus re-primed narration tokens; negligible per-call overhead otherwise"
rollback_trigger: "If R3 pass_rate is flat/down AND any previously-passing task relying on a repeated identical command (idempotent re-run / fixed-output poll) regresses T-to-F, revert"
-->

### Why

Assigned focus task_000015_89886d8d ran the agent for 39 steps (454s) and
failed: it had to OCR the routing-schema PNG with tesseract, issued a
wrong-syntax invocation (tesseract takes the output base as a positional arg
and has no -o flag), got read_params_file Can't open every time, and
re-issued the same call about 13 times (raw, then python3 -c, then heredoc)
interleaved with finish_reason=length truncations, burning all steps without
writing the two required files. This is the dominant failure shape across the
round: a per-task scan shows 7-plus reward=0 tasks with a single normalised
tool-result repeated 9-34 times (task_001818 34x heredoc, task_000118 26x
ps-grep, task_000010 10x heredoc) while passing tasks max out at <=3. The
pipeline already has two SOFT loop guards and both fire (their nudges appear
2-19 times in-context) but the 4B model ignores text and keeps looping. Two
mechanical gaps: interleaved length turns reset the strictly-consecutive
counter, and a text nudge has no teeth because the failing command still
executes. This is a distinct shape/mechanism from batch-sibling c3
(h_repeated_toolcall_recovery_v1), which injects a soft nudge for
polling-drift loops; c1 mechanically refuses stable identical-failure loops.

### Changes

- processors/loop_breaker.py: new LoopBreakerProcessor (Control). Tracks a
  rolling interleave-tolerant window of normalised (command, result)
  fingerprints (digits/hex/whitespace scrubbed). Once a signature produced the
  identical result block_threshold (6) times, the NEXT attempt at that exact
  signature is REFUSED (approved=false) and replaced with a synthetic result
  echoing the recurring output and demanding a materially different command or
  writing the required deliverable. Ordered 16 (after soft guards 5/7 and
  BgInstallGuard 15).
- config.yaml: register LoopBreakerProcessor via absolute file:// path,
  block_threshold=6 window=24.
- system_prompt.txt: copied byte-for-byte from R1.

### Evidence

- task_000015_89886d8d result.json: reward=0, final_pytest fails Migration
  script not found and Test script not found; finished=no_tool_calls after 39
  steps.
- task_000015 messages steps 5-37: 17 assistant turns re-issuing the same
  tesseract call, all results read_params_file Can't open; steps
  41/51/55/59/65/67/71/73 are finish_reason=length turns interleaving and
  resetting consecutive counting.
- task_001818_b251e5ea: top repeated command heredoc into main.rs count 33; 4
  soft nudges present and ignored.
- task_000010_644ab1c2: top repeated command heredoc into operator.py count
  10; 19 nudge/limit messages present and ignored.

### Uncertainty

The refusal only helps if the blocked model then tries a genuinely different
command rather than a trivial re-fail; it returns ~30 steps of budget on
task_000015 (necessary, not sufficient - finding the correct tesseract syntax
is a residual capability question). A task legitimately depending on a
repeated identical fixed-output command could be blocked (rare; state-changing
polls produce changing output and are safe) - first suspect on any
background-poll T-to-F regression. Static validators (canonicalize, dry_fire,
contract, literals) all pass clean.

## Round 2 — stdlib module-shadow diagnostic

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T00:00:00Z
hypothesis_id: h_stdlib_shadow_diagnostic_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips task_000010 and closes a recurring CPython sys.path[0] stdlib-name-shadow footgun class for any task whose required deliverable filename collides with a stdlib module (operator.py/token.py/types.py/queue.py/select.py/...)"
regression_risk: "A task that legitimately prints a partial-init/circular-import traceback (deliberately testing import errors) could get an unwanted nudge; guarded by dual signature + user-file-frame requirement and max_fires=2, informational-only"
cost_shift: "Net negative on the shadow path (saves 60+ step thrash that burns the whole budget); zero on non-matching tasks (never fires)"
rollback_trigger: "If R3 pass_rate is flat/down AND a previously-passing import-error task regresses T->F, revert (false-positive guard is first suspect)"
-->

### Why

task_000010_644ab1c2 (system_administration) requires a Python script at the
exact path /home/user/operator.py. 'operator' is a stdlib module. Running
python3 /home/user/operator.py (and later the verifier's pytest) from
/home/user prepends that dir to sys.path[0]; the stdlib import chain
collections -> from operator import eq then resolves to the USER file, giving a
fatal self-referential traceback (partially initialized module ... circular
import; final tail 'Could not import runpy module ... SyntaxError'). The agent
had no visibility into this sys.path[0] mechanic, misread it as a bug in its own
script, and thrashed 60+ steps renaming/symlinking/rewriting the required file
- ultimately corrupting operator.py into a bin/bash wrapper, the exact state
that fails the verifier. exit_reason=budget_exceeded (80 steps). Existing
RepeatedCommandRecovery (thr 3) and CustomEditTool (thr 7) guards fired and were
acknowledged but could not supply the specific structural insight to escape.

### Changes

- processors/stdlib_shadow_guard.py - new StdlibShadowDiagnosticProcessor
  (Control). Detects the stdlib-shadow traceback signature in tool output (dual
  gate: a shadow signature - partial-init/circular-import/runpy/site-import
  failure - AND a user-file traceback frame outside /usr/lib/python*, site- /
  dist-packages) and injects ONE task-agnostic diagnostic before the next model
  call explaining the sys.path[0] mechanic + escape routes (different cwd,
  python3 -P / PYTHONSAFEPATH=1, sys.path edit) WITHOUT renaming the required
  file. Ordered 7 (before compaction); max_fires=2; informational-only.
- config.yaml - register StdlibShadowDiagnosticProcessor via absolute file://
  path after RepeatedCommandRecoveryProcessor.
- system_prompt.txt - copied byte-for-byte from R1 (unchanged).

### Evidence

- task_000010 result.json: exit_reason=budget_exceeded (80 steps); final_pytest
  tail 'Could not import runpy module ... File /home/user/operator.py, line 2
  ... SyntaxError: invalid syntax'.
- messages.json step 2->3: python3 /home/user/operator.py -> circular-import
  traceback bottoming out in /home/user/operator.py.
- step 4: misdiagnosis mv operator.py k8s_operator.py && ln -sf ...
- steps 9-37: repeated rm -f ... && cat > /home/user/operator.py << EOF;
  EditDetection fires step 18/36; agent continues.
- steps 58-68: settles on cat > operator.py with a bin/bash wrapper (the corrupt
  state) repeated to budget.
- Detector unit-checked: fires True on both real tracebacks; False on ordinary
  NameError and normal output.

### Uncertainty

The fix depends on the agent acting on the diagnostic within remaining budget;
because it fires at the FIRST shadow traceback (early, step ~3) there is ample
room. It is informational-only, so it cannot corrupt state or kill a service.
Dual-gate detection makes false positives on ordinary user tracebacks unlikely
(unit-confirmed zero). If R3 shows an import-error task flipping T->F, the
false-positive guard is the first suspect - revert.
