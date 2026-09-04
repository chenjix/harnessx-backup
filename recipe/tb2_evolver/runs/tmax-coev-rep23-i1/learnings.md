# Evolve Journal — tmax-coev-rep23-i1

## Round 1 — verifier requests import

<!-- journal:frontmatter
round: 1
timestamp: 2026-09-01T18:00:00Z
hypothesis_id: h_verifier_client_ready_v1
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000958_4bb2b05d]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=28/50; +1/-2 gained=task_000344_e265c898 lost=task_000740_59416444,task_001673_86224c91; score 0.5600 >= incumbent(mean) 0.5800 - tol 0.0400
expected_global_gain: "Flips the network-service failures whose Python verifier crashes at `import requests`; generalizes to any TB2 HTTP-service task with a requests-based test module"
regression_risk: "Low — one-shot appended user message gated on network-service + verifier regex; never blocks a tool call, so cannot break a passing task. Worst case: ~250 wasted tokens on a service task that already has requests"
cost_shift: "+~250 tokens context + one short idempotent install on matched service tasks only; non-service tasks untouched"
rollback_trigger: "If R2 shows no flip on task_000028/task_000958 AND any regression on previously-passing service tasks, revert"
-->

### Why

Assigned focus task_000028_7fe033ac: the agent fully solved the task — fixed
nginx.conf to `/tmp/video_backend.sock`, rewrote and compiled server.cpp, ran it
in background, wrote logrotate.conf, started nginx, and its final probe returned
`HTTP/1.1 200 OK ... 150` through the proxy. reward=0 anyway. The sole cause: the
verifier's `test_final_state.py` does `import requests`, which is not installed
in the container, so pytest fails at collection with
`ModuleNotFoundError: No module named 'requests'`. This is a structural TB2
gap: the verifier's test module is injected after the agent session ends and the
agent never sees it, so it has no way to learn the verifier needs `requests`.
Grep over all 21 failing tasks: exactly task_000028 and task_000958 carry this
`No module named 'requests'` collection failure — a small but genuine
network-service cluster with an identical root cause.

### Changes

- `processors/verifier_client_ready.py` — new `VerifierClientReadyProcessor`
  (MultiHookProcessor). On `on_task_start` it arms only if the task description
  matches a network-service regex AND a verifier regex; on the first
  `on_step_start` it appends one user message telling the agent that TB2 network
  verifiers commonly `import requests` and to make the Python HTTP client
  importable before finishing (idempotent, offline-friendly install snippet).
  Fires at most once per task.
- `config.yaml` — register the processor via absolute `file://` path at order 7
  (after env/time setup, before length/compaction control).

### Evidence

- `task_000028_7fe033ac` result.json final_pytest tail: `test_final_state.py:6:
  import requests` → `E ModuleNotFoundError: No module named 'requests'` →
  `Interrupted: 1 error during collection`. messages.json final probe:
  `HTTP/1.1 200 OK ... Content-Length: 150 ... 150` — service was correct.
- `task_000958_4bb2b05d` result.json final_pytest tail: same `import requests` /
  `ModuleNotFoundError` at `test_final_state.py:4`. Agent had built and
  curl-tested the C++ microservice on `127.0.0.1:9090`.
- Description match: both contain HTTP/reverse-proxy/`127.0.0.1:PORT` vocabulary
  and an explicit "automated verifier will make HTTP requests"/checker mention,
  so `_SERVICE_RE` + `_VERIFIER_RE` both fire.

### Uncertainty

Open question: whether `pip install requests` / `apt-get install -y
python3-requests` succeeds under the default network-blocked agent phase. The
snippet tries `import` first (no-op if present), then pip, then apt against
pre-cached package lists (the base image pre-caches apt indices per the system
prompt). If none of these channels can produce `requests` offline, the reminder
cannot flip these tasks — that would show as `still_F` on both predicted tasks
with no regression, and is the signal to escalate (e.g. request the base image
pre-stage `requests`) via NEEDS_FROM_HUMAN next round rather than re-ship this
shape.

## Round 1 (c3) — heredoc-aware edit detector

<!-- journal:frontmatter
round: 1
timestamp: 2026-09-02T19:30:00Z
hypothesis_id: h_heredoc_edit_detector_v1
levers: [control]
predicted_affected: [task_000118_3043e92d, task_001937_ac874115]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Removes a false-positive [EditDetection] signal class that fires on any TB2 task authoring multi-line files via heredoc whose body contains `>` (Python/awk comparisons, pipes). Bash-only TB2 makes heredoc the dominant file-write idiom, so this touches the whole code-authoring cluster, not one task."
regression_risk: "Very low — on non-heredoc commands the extractor is byte-identical to the stock parser (same 3 regexes, same skip filter), so genuine over-edit detection on repeated >/sed -i/tee writes is unchanged. Only heredoc bodies stop producing write targets, which is strictly correct."
cost_shift: "Neutral-to-slightly-negative. Fewer phantom warnings → fewer confused delete/recreate recovery turns → fewer tokens on tasks that thrashed on spurious over-edit alerts. Per-step cost is one linear body strip."
rollback_trigger: "If R2 shows global pass_rate down OR the code-authoring cluster regresses (a previously-passing heredoc task flips to F), revert to benchmarks.terminal_bench_2.harness.CustomEditToolProcessor."
-->

### Why

Assigned focus task_000118_3043e92d (system_administration): the true reward
failure is a logic/timing bug — the agent's `deployment_monitor.py` never
truncated logs during the grader's run, so peak log-dir size hit 209,715,200
bytes vs the 45,000,000 threshold. But the harness actively made this harder:
the stock `CustomEditToolProcessor` scans the ENTIRE Bash command for `>`
write redirects, heredoc body included. The agent wrote its monitor via
`cat > deployment_monitor.py << 'EOF' ... EOF` whose body contains
`SIZE_THRESHOLD = ...` and `if size > max_size:`. The `>` inside the body was
misread as a redirect, so each write produced phantom "written files"
(`SIZE_THRESHOLD:`, `=`, `max_size:`) alongside the real file. All of them
accumulated in the per-file edit counter and fired THREE simultaneous
`[EditDetection]` over-edit warnings. The model's own summary shows it read
these as "The system is detecting excessive edits" and reacted by deleting and
recreating the file — an unproductive loop that consumed steps it needed for
the real timing bug. This is a genuine harness deficiency (a mechanical parser
false-positive injected into tool results), not a model knowledge gap, so it is
fixable in the harness regardless of whether it alone flips the task.

### Changes

- `processors/heredoc_aware_edit_detector.py` — new
  `HeredocAwareEditToolProcessor` (MultiHookProcessor). Drop-in replacement for
  the stock `CustomEditToolProcessor`: same singleton group
  (`bash_edit_detector`), same `_order` 30, same `threshold` knob, same hook
  surface and warning text. Only difference: `_strip_heredoc_bodies` removes
  every heredoc body (keeping the opener line so the redirect target is still
  counted) before running the stock redirect/sed/tee regexes, so body `>`
  characters are treated as data.
- `config.yaml` — replace `benchmarks.terminal_bench_2.harness.CustomEditToolProcessor`
  with the new processor via absolute `file://` path, `threshold: 7`.

### Evidence

- `task_000118_3043e92d.result.json` final_pytest:
  `AssertionError: Peak log directory size was 209715200 bytes, which exceeds
  the threshold of 45000000 bytes` — the real reward failure (timing bug).
- `task_000118_3043e92d.messages.json`: 4 tool results carry
  ``[EditDetection] File `/home/user/deployment_monitor.py` ...`` PLUS phantom
  ``File `SIZE_THRESHOLD:` ...`` and ``File `=` ...``. The model's later
  summary: "The system is detecting excessive edits ... decided to delete the
  file and create it fresh" — the phantom warnings drove a delete/recreate loop.
- Offline reproduction: on the exact task_000118 heredoc the stock extractor
  yields `['/home/user/deployment_monitor.py', 'max_size:']`; the new extractor
  yields only `['/home/user/deployment_monitor.py']`. Non-heredoc commands
  (`echo > f`, `sed -i`, `cat >> f`, `tee f`) extract identically to stock.
- Validators: canonicalize ok; dry_fire likely_bugs=0; contract violations=0
  (processor only appends to tool-result strings, never mutates event.messages);
  literals findings=0.

### Uncertainty

The phantom-warning removal clears the harness-induced derailer, but the
underlying monitor-timing bug is a model reasoning gap the harness must not
encode. So task_000118 may still fail on the real bug (would show `still_F`
with no regression) even though the confusion source is gone. Regression risk
is near-zero given byte-identical non-heredoc behaviour. If R2 shows any
previously-passing heredoc task flip to F, revert.

## Round 2 — identical-call loop guard

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-01T19:00:00Z
hypothesis_id: h_loop_detection_pipeline_v1
levers: [configuration]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the identical-Bash-call repetition-loop -> budget_exceeded failure class benchmark-wide by registering the already-implemented LoopDetectionProcessor; early warning returns recovery budget, backstop raise caps runaway compute"
regression_risk: "Low — warn-first (append text to tool result); raise only after 8 consecutive byte-identical (call, output) pairs; any interleaved different call resets the counter; compaction-aware so post-compaction drops clear stale fingerprints. No passing cluster relies on repeating one command 8x"
cost_shift: "Net negative — early warning shortens loops; backstop caps stuck tasks at ~8 steps vs full 80-step budget; per-step overhead is one sha256 of the tool-call summary"
rollback_trigger: "If R3 shows task_000010 still fails AND any previously-passing task regresses to loop_detected (false-positive termination of legitimate repeated work), revert"
-->

### Why

Assigned focus task_000010_644ab1c2 (system_administration, k8s operator
script) failed with exit_reason=budget_exceeded at 80 steps. messages.json
steps 2–59 are the *exact same* assistant turn ("The zombie processes are
still there. Let me try a different approach…") emitting the *identical* Bash
command (`ps aux | grep -E "python|socat" ... xargs -r kill -9 ...; sleep 2;
...`) and receiving *identical* tool output (defunct PIDs 115/130/139) ~27
times in a row. The model even narrates "I've been repeating the same command
many times" (step 56) and "I've been stuck in a loop" (step 58) — it
recognises the loop but the passive run-loop continuation never forces it out.
Only at step 60 does it abandon the loop, write the operator script, run it,
create the backup and port-forward — but ~20 steps of budget remain and the
run ends with only one of two manifests applied. This is a benchmark-wide
failure class: an identical-tool-call-with-identical-output loop that neither
LengthTruncationRecovery (fires only on finish_reason=length with NO tool
call) nor ParseRetry (parse errors only) can catch. The pipeline simply lacks
the guard that would break it.

### Changes

- `config.yaml` — register the existing, contract-clean
  `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
  (_order=20, compaction-aware) into the pipeline with `warn_threshold=3`,
  `threshold=8` (raise). Warn appends a corrective "you are stuck in a loop,
  try something fundamentally different" nudge into the tool result at 3
  consecutive identical calls so the agent recovers early and spends its
  remaining budget productively; the raise at 8 is a compute backstop that
  turns a full-budget burn into a clean loop_detected exit. No new code
  authored — the deficiency was that this component was absent from the
  recipe pipeline, not that it needed to be written.

### Evidence

- result.json: `agent.exit_reason="budget_exceeded"`, `steps=80`,
  `reward=0`, final_pytest fails on `operator.py` missing (renamed to
  k8s_operator.py) and deploy-v2.yaml not in the API success log.
- messages.json steps 2,4,6,9,…,59: byte-identical assistant content AND
  byte-identical tool output (three `[python3] <defunct>` lines).
- step 60 onward: loop abandoned, script written and run, backup + port
  forward created — proof the agent makes real progress once unstuck.

### Uncertainty

Whether the warn nudge alone flips this specific task to pass depends on the
model using the freed ~50 steps to (a) rename operator.py correctly and (b)
apply BOTH manifests. If it flips: strong signal. If task_000010 stays
`loop_detected`/fail but no regressions: the guard is still a net-positive
compute cap and the residual failure is a model capability gap (the agent
must fix the `import operator` naming collision and iterate both manifests) —
note as capability gap next round rather than re-shipping.

## Round 2 (c4) — repeat-command loop breaker (nudge-only)

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-02T21:00:00Z
hypothesis_id: h_repeat_command_breaker_nudge_v1
levers: [control]
predicted_affected: [task_000140_01c78b42, task_000010_644ab1c2, task_000015_89886d8d, task_001818_b251e5ea]
cited_candidates: [C-002]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the degenerate identical-command/identical-output no-progress loop cluster (5+ failing tasks re-issue one Bash command 15-28x; 2 hit budget_exceeded). A nudge-only, content-agnostic breaker returns wasted steps without ever terminating a run."
regression_risk: "Low — appends one user message only after 4 consecutive byte-identical (command,result) pairs; never blocks/rewrites/terminates a tool call. Unlike the sibling h_loop_detection_pipeline_v1 hard-raise@8, it cannot kill a late-recovering task (task_000010 recovered at step 60). Post-completion echo loops (task_000329, reward 1) are unaffected."
cost_shift: "Net token/step reduction on tasks it fires on: ~120 tokens injected once/twice vs. 15-28 eliminated identical tool round-trips."
rollback_trigger: "If R3 shows no flip on the predicted tasks AND the same tasks still show 15-28 identical-command runs (model ignores the nudge) OR any previously-passing task regresses, revert; escalate to blocking the identical call via synthetic_result."
-->

### Why

Assigned focus task_000140_01c78b42: the agent spent steps 2-53 (of 59) in a
degenerate loop — 15 identical `kill -9 353 587 815 1030; ps aux | grep
vm_service` calls returning the identical listing of four `<defunct>` zombie
processes, then ~8 identical `pkill -9 -f vm_service` -> `(exit 137, no output)`.
Zombies cannot be killed; every iteration was a permanent no-op. Compaction
fired at step 54 and lost the plan; the task failed with 7 lingering PIDs and an
unfixed `test_pipeline.sh` (rc 127). A sweep of every messages.json shows this
is systemic: max identical-command runs of 28 (000010), 26 (000015), 21
(000118), 19 (001818), 15 (000140); command AND result verified byte-identical
for 000010/000015/001818; 000010/000015 exhaust their budget entirely. This is
the same cluster the sibling proposal h_loop_detection_pipeline_v1 targets, but
I ship a distinct, safer shape at a different lever (see Changes / candidates.md
C-002).

### Changes

- `processors/repeat_command_breaker.py` — new `RepeatCommandBreakerProcessor`
  (MultiHookProcessor, Control lever). `on_before_tool` captures the Bash
  command; `on_after_tool` fingerprints (command, result) and counts
  consecutive identical pairs; at `repeat_threshold` (4) it arms a one-shot
  corrective nudge; `on_before_model` injects it as one user message (replacing
  a trailing user message to satisfy the +1 contract), escalating wording if
  the loop persists. Content-agnostic — keys only on the observable
  command/result fingerprint; includes a zombie-process hint. Crucially it
  NEVER terminates the run (contrast: sibling registers stock
  LoopDetectionProcessor with a hard raise@8 that would have killed task_000010
  before its step-60 recovery).
- `config.yaml` — register via absolute `file://` path at order 6, after
  LengthTruncationRecoveryProcessor (disjoint shape: finish_reason=length only).

### Evidence

- `task_000140_01c78b42` messages.json steps 2-31: 15x identical zombie-kill;
  steps 38-53: ~8x identical pkill -> `(exit 137)`; step 54 compaction; final
  failure `test_no_lingering_service_processes` lists 7 lingering PIDs.
- `task_000010_644ab1c2`: 28x identical `ps aux|grep|xargs kill`, budget_exceeded;
  model narrates "I've been stuck in a loop" yet cannot self-break.
- `task_000015_89886d8d`: 26x identical python subprocess call, budget_exceeded.
- Passing control `task_000329_a3ac56b0` (reward 1) repeats only a harmless
  post-completion echo — nudge is a no-op there.
- Validators: canonicalize ok (checked_templates=0); dry_fire likely_bugs=0;
  contract violations=0; literals findings=0.

### Uncertainty

Threshold 4 sits above normal 2-3x polling but well below the 15-28 in failures.
Open risk: whether the 4B model actually changes behaviour on the injected nudge
or re-enters the loop. If predicted tasks still show long identical runs
post-fix (still_F, unchanged repeat counts), the model ignores the interrupt and
the next escalation is to block the identical call outright via
ToolCallEvent.synthetic_result rather than only nudging.

## Round 2 — enable tool-call loop detector

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-02T12:00:00Z
hypothesis_id: h_toolcall_loop_detection_v1
levers: [configuration]
predicted_affected: [task_000015_89886d8d, task_000010_644ab1c2, task_000118_3043e92d, task_001818_b251e5ea, task_001089_220cc46b]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the budget_exceeded identical-command-loop cluster (5 tasks) by capping consecutive byte-identical Bash calls so ~72 wasted steps are returned to the agent to pivot; generalizes to any single-Bash-tool task that gets stuck re-issuing one command"
regression_risk: "Three currently-passing tasks (task_000329, task_001652, task_001701, task_001591) also contain long identical runs, but in each the required work was already complete and the agent was merely spinning; LoopDetectedError exits cleanly (exit_reason=loop_detected, not error, verified runloop.py:781) preserving final filesystem state so they still pass while shedding idle steps"
cost_shift: "Net DOWN — caps 80-step budget_exceeded loopers at ~6 identical repeats (~15-25 steps saved each); non-looping tasks untouched"
rollback_trigger: "If R3 shows any previously-passing task flip to loop_detected+reward=0 (force-exited before work complete), raise threshold to 10 or revert"
-->

### Why

Assigned focus task_000015_89886d8d (budget_exceeded, 80 steps, reward=0): the
agent tried to OCR an image with tesseract, got the CLI argument order wrong
(`read_params_file: Can't open ...`), and then re-issued the *byte-identical*
`python3 << ... tesseract ...` command 26 times in a row — each returning the
identical STDERR — until it exhausted its 80-step budget without ever writing
`/home/user/migrate.py` or `/home/user/test_parser.py`. This is not a
task-specific problem: sweeping all trajectories for the longest run of
consecutive byte-identical tool-call arguments surfaced a 5-task
`budget_exceeded` cluster with runs of 12-28 identical (command, output) pairs.
The current pipeline has NO tool-call loop breaker — only
`LengthTruncationRecoveryProcessor`, which fires only on `finish_reason=length`.
These loops have valid tool calls (`finish_reason=stop`), so nothing intercepts
them. The purpose-built `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
exists as a builtin but was simply never wired into this config.

### Changes

- `config.yaml` — register `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
  after `parse_retry` with `warn_threshold: 3`, `threshold: 6` (raise),
  `window_size: 12`, `name_warn_threshold: 8`, `compaction_drop_threshold: 5`.
  Warns (injected into tool result) at 3 consecutive identical calls; hard-raises
  LoopDetectedError at 6 -> clean exit_reason=loop_detected.

### Evidence

- `task_000015` messages steps 2-56: identical assistant message + identical
  Bash args + identical `STDERR: read_params_file: Can't open
  tessedit_char_whitelist=...` (26x consecutive).
- `task_000010` most-repeated tool output 28x/33: `... [python3] <defunct> ...`.
- `task_000118` most-repeated tool output 22x/31: `(exit 0, no output captured)`.
- `task_001818` `cargo build --release` 22x; `task_001089` `make clean && make
  test` 12x consecutively.
- Passing-cluster safety check: `task_000329` (reward=1) resumed post-compaction
  with all output files already present (step 3 `ls` shows config_delta,
  delta_v1_v2.txt, etc.) then spun on the same `ls` 26x — force-exit would still
  pass.

### Uncertainty

Open question: whether returning ~72 steps to task_000015 is enough for the 4B
model to *find* the correct tesseract invocation (`tesseract IN OUT_BASE`, no
`-o`). If it re-enters a *different* identical loop the detector re-fires, but
the underlying OCR-CLI knowledge gap is a model-capability issue, not a harness
one — that would show as still_F on task_000015 with a much lower step count
(loop_detected instead of budget_exceeded), signalling the harness fix worked
but the capability gap remains. Watch for any passing task flipping to
loop_detected+reward=0 (the rollback trigger).
