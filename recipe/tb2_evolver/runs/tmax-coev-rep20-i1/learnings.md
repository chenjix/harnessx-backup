# Evolve Journal — tmax-coev-rep20-i1

## Round 1 — cyclic loop hard-stop

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_cyclic_loop_guard_v1
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000206_a943669b, task_000010_644ab1c2, task_000264_ab8c7253, task_000536_9c16e8ef]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=2/50; +0/-3 lost=task_000344_e265c898,task_000912_770802f8,task_001781_529727cf; gating disabled (tolerance < 0)
expected_global_gain: "Hard-stops the dominant budget_exceeded@80-steps cluster (~35/49 R0 tasks) that stall in no-progress repeat loops; reclaims wall-clock/step budget and forces one replan before exit"
regression_risk: "A legitimate task issuing the same 1-4 command block 4x consecutively with no interleaving would be terminated early; kept low via whitespace-normalized fingerprints, 24-slot window, raise_reps=4"
cost_shift: "Strongly negative — stalled tasks cut from 80 steps to ~step 12-16 (roughly 4-5x cheaper on the stall cluster); no cost added to healthy tasks"
rollback_trigger: "R1 pass_rate < R0, OR any R0-passing task (task_000587, task_000748, task_000912, task_000344, task_001781) flips to exit_reason=loop_detected"
-->

### Why

The dominant R0 failure mode is `exit_reason=budget_exceeded` at exactly
80 steps — ~35 of 49 tasks. Body inspection of the assigned failing task
(`task_000028`) and four other representatives shows the same mechanism: a
weak model gets stuck in a **no-progress repeat loop** and burns the full
step/wall-clock budget (500-780s each). The loops vary in period:
task_000028 is a period-3 cycle (`rm/touch sock` → `run server` → `cat
log`, ~8x); task_000206 is a period-2 cycle (piped vs un-piped grep);
task_000010 and task_000264 are period-1 exact loops (identical command
16x). The existing `LoopDetectionProcessor` isn't even in the pipeline, and
its exact-consecutive strategy cannot detect period>1 cycles at all; the
soft `CustomEditToolProcessor` warning fired once on task_000028 and the
model ignored it verbatim while continuing to loop. Soft nudges are
ineffective for this model — a mechanical hard-stop is required.

### Changes

- `processors/cyclic_loop_guard.py` — new `CyclicLoopGuard`
  `MultiHookProcessor`: fingerprints each Bash command
  (whitespace-normalized), detects a repeating tail cycle of period 1..4,
  warns once at 3 reps, raises `LoopDetectedError` at 4 reps → clean
  `exit_reason=loop_detected`.
- `config.yaml` — register `CyclicLoopGuard` (max_period=4, warn_reps=3,
  raise_reps=4, window_size=24) after `LengthTruncationRecoveryProcessor`.

### Evidence

- `task_000028_7fe033ac` result.json: `exit_reason=budget_exceeded`,
  steps=80, elapsed=550.7. messages show the 3-command block
  (`rm -f /tmp/video_backend.sock && touch … && ls` / `/app/server >
  /app/server.log 2>&1 &` / `sleep 1 && cat /app/server.log`) repeated ~8x.
- `task_000206_a943669b` last 16 cmds: `grep "session_id=EVIL_ACTOR_007"
  … | grep "Source-IP:"` alternating with the un-piped grep — period-2.
- `task_000010_644ab1c2` last 16 cmds: identical `python3 -c "import
  socket…"` 16x — period-1.
- `task_000264_ab8c7253` last 16 cmds: identical `sqlite3 … EXPLAIN QUERY
  PLAN …` 16x — period-1.
- Offline unit test of the processor: period-3 raises at step 11, period-2
  at step 7, period-1 at step 3; 12 distinct commands never raise/warn;
  "3x then break" warns but does not raise (transient repetition survives).

### Uncertainty

The change reclaims budget and forces one replan warning; it does not fix
the underlying model reasoning bugs, so a stall task only *passes* if the
loop was masking an otherwise-recoverable state. The net-positive bet is on
(a) reclaimed benchmark wall-clock/steps and (b) the forced-replan second
chance. If R1 pass_rate does not improve and no cost benefit materializes,
or a passing task regresses to `loop_detected`, revert per rollback_trigger.

## Round 1 — output-stall recovery (proposal c1)

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_output_stall_recovery_v1
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000010_644ab1c2, task_000505_50b5162d, task_000684_1a33ef37, task_000958_4bb2b05d, task_001031_a8f0eb37, task_001090_c61c71f2, task_001498_df8254c9, task_001652_86e1d185, task_001673_86224c91, task_001818_b251e5ea, task_000578_cebe85a5, task_000536_9c16e8ef, task_001089_220cc46b, task_000760_76ba653c, task_000818_315382d9, task_000933_1f27096a, task_001321_658ce4a8, task_001701_95e3bbcb, task_000024_a0664029]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Attacks the dominant budget_exceeded cluster from the OUTPUT side: ~20/50 tasks show >=7 consecutive byte-identical tool results (task_000015 has 33). Warn@3 + hard@6 surfaces 'no new information' in-context so the model changes approach or finishes, plausibly flipping the subset whose stall was the only blocker."
regression_risk: "A passing task that legitimately repeats a command with identical output >=3x gets an ignorable advisory appended to the result — no forcing, no termination. Only observed such case is passing task_000344_e265c898 (result run=7, read-only spin after reward-earning work landed); warn-only design keeps it passing."
cost_shift: "Net negative (cheaper): recovered stalls terminate well below 80 steps; the appended note is tiny vs a full 80-step loop."
rollback_trigger: "If R2 pass_rate is flat/down AND no budget_exceeded task in predicted_affected flips, or any previously-passing task regresses, revert."
-->

### Why

The single dominant R0 failure is `budget_exceeded` at 80 steps (~35/50 tasks).
Assigned task `task_000015_89886d8d` is the archetype: the agent re-ran the exact
same `tesseract /app/routing_schema.png /raw --psm 6` command ~33 times, each
returning the identical `Invalid resolution 0 dpi. Using 70 instead` warning and
no usable text, while narrating "Let me try with a different approach" every turn
— and never wrote the required `/home/user/migrate.py` or `test_parser.py`. The
distinguishing, non-obvious signal is that the tool RESULT is byte-identical turn
after turn — the agent cannot perceive that its new output equals its last output.
This is broader than input-repetition: task_000024_a0664029 varies its command but
the result stream is still identical (run=27), which a pure input-fingerprint loop
detector would miss. A sibling proposal in this batch owns the input-side
terminate-based cyclic loop guard (h_cyclic_loop_guard_v1); this proposal is the
complementary, non-colliding output-side, warn-only mechanism.

### Changes

- `processors/output_stall_recovery.py` — new `OutputStallRecoveryProcessor`
  (`MultiHookProcessor`). `on_after_tool` hashes each non-empty tool result; on
  `warn_threshold` (3) consecutive identical results it appends a corrective note
  to the result, escalating to a harder note at `hard_threshold` (6). Warn-only —
  never raises. Compaction-aware reset in `on_step_start`.
- `config.yaml` — registered the processor last in the pipeline via absolute
  `file://...::OutputStallRecoveryProcessor` (warn 3, hard 6, min_result_chars 1,
  compaction_drop 5). No other pipeline change.

### Evidence

- `task_000015_89886d8d` result.json: `exit_reason=budget_exceeded`, `steps=80`;
  messages.json: identical `tesseract` result string repeated ~33x (result run=33);
  final_pytest fails "Migration script not found at /home/user/migrate.py".
- Cluster scan of all 50 trajectories: `max_identical_result_run >= 7` on ~20
  tasks, == full visible window (33) on ~14.
- `task_000024_a0664029`: input command varies (input run=27) yet result run=27
  — output detection fires where input fingerprinting would not.
- `task_000344_e265c898` (PASSING, reward=1, result run=7): warn-only design
  appends an ignorable note and leaves the pass intact.

### Uncertainty

The warn note relies on the model reading and acting on an in-result advisory. If
the model ignores it, the freed-budget benefit only materializes when it also
self-terminates — worst case is no change vs R0 (still budget_exceeded), not a
regression, since we never force or terminate. If R2 shows zero flips in the
predicted cluster, the lever is under-powered and should be combined with (not
replaced by) the input-side terminate guard.

## Round 2 — infra container-name collision (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_docker_name_collision_noop_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None from config — assigned focus is outside HarnessConfig reach; documented required orchestration fix in NEEDS_FROM_HUMAN.md"
regression_risk: "None — byte-identical config copy"
cost_shift: "None"
rollback_trigger: "N/A (no-op)"
-->

### Why

Assigned focus `task_000015_89886d8d` did NOT fail for the reason the R1
journal assumed (OCR/output-stall loop). In THIS trajectory set it failed
with `status=error`, `elapsed_s=0.2`, `reward=0` — a Docker
container-name **Conflict** thrown by
`recipe/tmax_eval/docker_env.py::start_container` (called from
`run_eval.py::run_one` line 149) *before* the agent loop or any
HarnessConfig processor runs. This is systemic: **23 of 50 tasks** in this
set died with the identical `docker run failed ... container name ...
already in use` signature at `elapsed_s ≈ 0.2`, including several tasks
(task_000028, task_000264, task_000206, task_000024, task_000536,
task_000015) that R1's loop-guard proposals targeted — those never ran, so
those levers could not fire.

Root cause: `start_container` names containers
`tmax-{task_id[:20]}-{int(time.time())}` (second-resolution) and does not
`docker rm -f` a pre-existing/stale container of that name before
`docker run --name`. Same-second siblings + leftover containers from
killed prior runs collide (observed identical suffix `-1788094866` on
task_000015 and task_000684).

### Changes

- `config.yaml` — **byte-for-byte copy** of `current_config`
  (R1/config.yaml). Explicit no-op. `HarnessConfig` (processors / tools /
  templates / system prompt) has no hook that runs before container
  creation, and `recipe/tmax_eval/` is read-only for this meta-agent, so
  no config change can touch this code path. A config edit here would be
  theater.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — full diagnosis + the concrete
  orchestration fix required (`docker rm -f` before run, collision-free
  name suffix, retry-once-on-Conflict).

### Evidence

- `task_000015_89886d8d.result.json`: `"status":"error"`,
  `"elapsed_s":0.2`, `"error":"...docker run failed... container name
  \"/tmax-task00001589886d8d-1788094866\" is already in use..."`.
- Sweep of all 50 `*.result.json`: 23 `status=error`, every one a
  `docker run failed ... already in use` conflict at `elapsed_s≈0.2`;
  23 `status=ok reward=0`, 2 `status=ok reward=1`, 2 `agent_error`.
- `docker_env.py:105`: `name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"` — second-resolution, no pre-remove.
- Canonicalize on the shipped no-op config: `{"ok": true, "checked_templates": 0}`.

### Uncertainty

None at the config layer — the failure provably occurs before config
consumption. The only way to lift this ~46% false-negative floor is the
orchestration fix in NEEDS_FROM_HUMAN.md; until then, focus assignments
landing on the 23 error tasks are un-actionable for the meta-agent.

## Round 2 — no-op: assigned focus is a docker infra flake

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_noop_docker_infra_r2c2
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — no config change shipped; assigned focus is an infra/orchestration failure outside any HarnessConfig lever."
regression_risk: "None — config.yaml + system_prompt.txt copied byte-for-byte from R1 (sha 3883d5c9...); zero behavioral delta."
cost_shift: "Zero."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus `task_000024_a0664029` is NOT a harness capability gap.
Its R0 trajectory result.json shows `status: error`, `elapsed_s: 0.1`,
and `RuntimeError: docker run failed ... container name
"/tmax-task000024a0664029-<epoch>" is already in use`, raised in
read-only `recipe/tmax_eval/docker_env.py:124` (`start_container`). The
container never launched, so the agent run loop never ran a single step.

A full batch scan is decisive: **23 of 50 tasks** fail with the
identical docker container-name collision at ~0.1s (status counts:
ok=25, error=23, agent_error=2). Root cause is `start_container`
naming a container `f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"`
— a second-granularity timestamp plus a 20-char task-id truncation that
collides with leftover containers from crashed/parallel runs.

No HarnessConfig lever can touch this: every processor/tool/template/
system-prompt hook binds only *after* the container exists, and the
offending code is in read-only `recipe/**` (hard invariant #2). This is
an orchestration/infra flake, not a harness deficiency and not a model
capability gap — so the disciplined move is an explicit no-op rather
than poaching a sibling proposal's control-lever loop/stall territory
(which also cannot be validated this round because ~46% of rows are
uninterpretable t=0 failures).

### Changes

- `config.yaml` — byte-for-byte copy of R1 config (no change).
- `system_prompt.txt` — byte-for-byte copy of R1 sidecar (preserves
  SiblingSystemPromptBuilder resolution; no change).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — root cause + suggested
  read-only fix for docker_env.start_container container naming.

### Evidence

- `task_000024_a0664029` result.json: `status=error`, `elapsed_s=0.1`,
  error `docker run failed ... container name ... already in use`;
  traceback at `recipe/tmax_eval/docker_env.py:124`.
- Batch scan of 50 result.json: 23 tasks error with the identical
  docker container-name Conflict at ~0.1s (task_000024, task_000684,
  task_000140, task_000505, task_001701, task_000760, task_000015,
  task_000264, task_000028, task_000206, task_000536, task_001781, ...).
- `recipe/tmax_eval/docker_env.py:105`: `name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]` — collision-prone naming.

### Uncertainty

If the human fixes the container-naming collision, these 23 rows become
interpretable and this batch's real harness signal (R1's loop/stall
clusters) can be re-attributed. Until then, pass-rate deltas on any
task in the collision set are noise, not evidence.

## Round 2 — no-op: assigned failure is docker infra flake (proposal c3)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_noop_docker_infra_flake_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — no harness change shipped. Assigned failure is not harness-editable."
regression_risk: "None — config is byte-identical to R1/config.yaml (md5 4394b9f...)."
cost_shift: "Zero — no pipeline change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus `task_000028_7fe033ac` failed with `status=error`,
`reward=0`, `elapsed_s=0.2` — a Docker container-name collision at launch:
`docker: ... Conflict. The container name "/tmax-task0000287fe033ac-1788094866"
is already in use ...`. The traceback is in `recipe/tmax_eval/docker_env.py:124`
(`start_container`), invoked from `run_eval.py:149`. The task never ran: the
failure precedes the first agent step, the first LLM call, and every processor
in the pipeline. No system-prompt / processor / tool edit — the only evolvable
surface — can prevent a container-name collision, which is decided in
`docker_env.py` (read-only for the meta-agent).

This is systemic, not isolated: 23 of 50 tasks in this trajectory set errored
identically, ALL sharing the same timestamp suffix `-1788094866`
(`docker_env.py:105` names containers `f"tmax-{id}-{int(time.time())}"`). A
frozen/mocked clock or orphaned containers from a prior interrupted run cause
the collision. This is a harness-runner / orchestration infra bug.

Per the brief, when the assigned focus is unsupported by a harness change I
make the smallest defensible edit rather than drifting onto another proposal's
territory (the genuinely-executed long-running reward=0 tasks are the domain of
R1's control-lever loop/output-stall proposals). Therefore: explicit no-op.

### Changes

- `config.yaml` — byte-for-byte copy of R1/config.yaml (md5 4394b9f9...).
- `system_prompt.txt` — byte-for-byte copy of R1 sibling (md5 914fb4a8...),
  so `SiblingSystemPromptBuilder` resolves the same prompt next to the config.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — flags the docker-name-collision infra
  bug (recommend uuid4 suffix and/or `docker rm -f` stale-name reap) affecting
  23/50 tasks; requires editing `docker_env.py`, outside meta write scope.

### Evidence

- `task_000028_7fe033ac.result.json`: `status="error"`, `elapsed_s=0.2`,
  `error="RuntimeError: docker run failed ... container name
  \"/tmax-task0000287fe033ac-1788094866\" is already in use ..."`.
- `summary.json` + per-task scan: 23/50 tasks `status=error` with the identical
  `docker name conflict` signature and identical `-1788094866` suffix.
- `docker_env.py:105`: `name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]`.

### Uncertainty

The no-op leaves the R1 pipeline in place; the 23 infra-errored tasks cannot
produce signal until `docker_env.py` naming is fixed by a human. If a future
round's trajectory set no longer shows the `-1788094866` collision, task_000028
should be re-diagnosed against a real (non-zero-elapsed) trajectory.

## Round 2 — stdlib-shadow diagnostic

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_stdlib_shadow_guard_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the self-inflicted-interpreter-breakage class: agent writes a .py file whose name collides with a stdlib module (operator.py, socket.py, ...) then runs python3 from that dir, shadowing stdlib -> cascading partially-initialized/circular-import errors that break EVERY python3 call. A weak model misdiagnoses this 100% of the time (blames 'Python version') and loops until termination. The warn-only after-tool hook injects the root cause + concrete recovery (cd /tmp && python3 <file>, PYTHONSAFEPATH=1) at the moment the signature appears."
regression_risk: "Near-zero. Hook only appends text, never blocks/terminates, and fires only when BOTH the partial-init/circular-import signature AND a user-writable (non-/usr/lib) traceback frame are present — a shape absent from healthy runs. Capped at max_injections=3/task. No passing batch task shows this signature; offline unit test confirms plain ModuleNotFoundError does NOT trigger it."
cost_shift: "Negative on affected tasks (a 23-step misdiagnosis loop collapses once recovered); neutral elsewhere (guard never fires on healthy runs)."
rollback_trigger: "R3 pass_rate < R2, OR task_000010 still fails with exit_reason=loop_detected AND the injected diagnostic is present in messages (agent saw it and still could not recover -> capability gap deeper than perception), OR any previously-passing task regresses."
-->

### Why

Assigned task `task_000010_644ab1c2` (reward=0, exit_reason=loop_detected,
steps=23). Task mandates a script at `/home/user/operator.py`. The agent
wrote it and ran `python3 /home/user/operator.py` from `/home/user`. CPython
puts the script dir first on `sys.path`, so the file shadowed the stdlib
`operator` module; `collections/__init__.py`'s `from operator import eq`
resolved to the user script -> `cannot import name '...' from partially
initialized module 'collections' ... circular import`. This broke every
`python3` invocation from that dir (even `python3 -c "import socket"`). The
model narrated "this is a Python 3.10 compatibility issue" and re-ran variants
16+ times until the loop guard terminated. It never suspected its own
filename. The existing R1 LoopDetectionProcessor STOPS the thrash but offers
no recovery path — the model needs to be told *what* is wrong, keyed to the
actual failing file parsed from the traceback.

### Changes

- `processors/stdlib_shadow_guard.py` — new `StdlibShadowGuard`
  (`MultiHookProcessor`). `on_after_tool` scans Bash result+error for a
  partial-init/circular-import ImportError whose traceback contains a
  user-writable `.py` frame (not under /usr/lib, site-packages, etc.). On
  match, appends a targeted diagnostic naming the file and the recovery (run
  from a neutral cwd, PYTHONSAFEPATH=1, or rename if the name isn't mandated).
  Warn-only, capped at `max_injections=3`, reset on task_start.
- `config.yaml` — registered after `CustomSelfVerifyProcessor` and before
  `LoopDetectionProcessor` so the diagnostic lands before the loop guard's
  warnings accumulate. (`max_injections=3`.)

### Evidence

- `task_000010_644ab1c2.result.json`: exit_reason=loop_detected, steps=23;
  final_pytest tail = `cannot import name 'namedtuple' from partially
  initialized module 'collections' ... circular import` with a
  `/home/user/operator.py` frame.
- messages.json line 122: first shadow error, frame `File
  "/home/user/operator.py", line 14`. Lines 182/222/282/322/342: same
  signature on varied commands; agent repeatedly blames "Python version".
- Offline unit test: shadow text fires + names `/home/user/operator.py`;
  plain `ModuleNotFoundError: No module named yaml` does NOT fire; injection
  cap holds at 3.
- Validators: canonicalize/dry_fire/contract/literals all `ok:true`, 0 bugs.

### Uncertainty

The fix assumes the agent, once told the root cause and given `cd /tmp &&
python3 ...`, will apply it — a perception fix, not a reasoning fix. If the
model reads the diagnostic and still loops, the gap is deeper than perception
and the hook should escalate (e.g. auto-rewrite the command) or be reverted.
Single observed task in this batch, but the mechanism is a textbook,
name-agnostic Python footgun that recurs on any stdlib-colliding script name.

## Round 2 — substantive-exit-verify gate (proposal c4)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_substantive_verify_guard_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_000667_2d762a00]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the premature-done / hollow-verification cluster: reward=0 tasks that exit with finished=no_tool_calls after the base _tb2_self_verify checklist fired but was answered with only a trivial ls/cat/du peek. Two-plus tasks (118, 667) share this exact rubber-stamped-checklist mechanism; the gate generalises to any task whose acceptance criterion is transient/behavioural or a specific file property."
regression_risk: "Low — fires at most once, only after the base checklist fired, and only when zero substantive verification commands ran since. Tasks that verify properly (incl. passing task_000587, task_000748 which re-run their solutions) never see it. Worst case: one extra turn plus one Bash call, then exit is allowed unconditionally. Budget/loop clusters never reach exit-intent."
cost_shift: "Near-neutral; at most one extra model round-trip plus one Bash call on the subset reaching exit with hollow verification. No effect on the dominant 80-step budget/loop clusters."
rollback_trigger: "R3 pass_rate below R2, OR any R2-passing task (task_000587, task_000748) regresses to reward=0, OR the guard fires on a task that had already verified substantively (classifier too strict — check trajectories for spurious sv2 keepalive calls)."
-->

### Why

Assigned focus task_000118_3043e92d failed with exit_reason=done,
finished=no_tool_calls, steps=12 — a completely different shape from R1's
budget/loop clusters. The agent wrote a deployment-monitor daemon whose
find_running_workers() was broken (it Popen-launched a NEW worker and iterated
the Popen object instead of listing real PIDs), so its SIGSTOP/SIGCONT were
no-ops and peak log size hit 200 MB vs a 45 MB grader threshold. The buggy
Python is a model capability gap. The harness-addressable part: the agent
declared success after verifying only the quiescent final state of a dynamic
system (ls/du on logs showed 0 bytes / 4 KB, because workers had exited and the
final truncate cleared them) — never the transient peak the grader measures.
The existing CustomSelfVerifyProcessor DID fire, but the weak model
rubber-stamped it: its post-checklist turn was ls -lh deployment_monitor.py,
then exit. task_000667 shows the identical mechanism — after the checklist it
ls-ed an unrelated output and exited, never re-reading the setup.py whose
content the requirement constrains (grader: "still references fast_math.cpp").
The one-shot prose checklist is skimmed; a mechanical second gate is needed.

### Changes

- processors/substantive_verify_guard.py — new SubstantiveVerifyGuard
  MultiHookProcessor (_order=95, after CustomSelfVerifyProcessor at 90).
  Tracks whether the base _tb2_self_verify checklist fired and whether any
  substantive Bash command ran since (interpreter / test / build /
  service-probe / content-search / script-by-path — vs a bare ls/cat/du/stat/
  echo peek). On the next exit-without-tool-calls after the checklist, if
  verification was hollow, it injects ONE sharper prompt (via a keepalive tool
  call, same pattern as the base processor) demanding the agent re-run the real
  acceptance scenario and measure the specific constrained property. Fires at
  most once per task; never terminates the run; silent when verification was
  already substantive.
- config.yaml — register SubstantiveVerifyGuard after CustomSelfVerifyProcessor,
  before LoopDetectionProcessor.

### Evidence

- task_000118 messages.json: _tb2_self_verify (sv-f092565a) then single
  ls -lh /home/user/deployment_monitor.py then no-tool-call summary exit;
  prior "verification" was ls -la logs/ (0-byte) plus du -sh logs/ (4.0K).
  final_pytest: Peak log directory size was 209715200 bytes, exceeds 45000000.
- task_000667 messages.json: _tb2_self_verify then ls -lh mre_output.txt
  (unrelated) then exit; setup.py never re-read. final_pytest: "The bug in
  setup.py was not fixed. It still references fast_math.cpp."
- Offline classifier test: ls/cat/du/stat/echo peeks classified NOT
  substantive (guard fires); python3 monitor.py, ./run.sh, grep cpp setup.py,
  pytest -q, cat setup.py | grep cpp classified substantive (guard silent).
  Correctly classifies both failing tasks' turns as hollow.
- Validators: canonicalize ok (0 templates); dry_fire likely_bugs=0;
  contract violations=0.

### Uncertainty

The gate removes the false-success short-circuit and forces one substantive
re-check, giving the model the observation it needs to notice its solution is
wrong — but it does not guarantee the model can then fix the underlying bug
(broken PID discovery / wrong-direction config edit are capability gaps). The
net-positive bet is that (a) some fraction of hollow-verify failures are
fixable once observed, and (b) regression risk is near-zero because the gate is
one-shot, post-checklist, and silent whenever verification was already real.
If R3 shows zero flips in the predicted cluster and no cost benefit, retire the
lever rather than hardening it.
