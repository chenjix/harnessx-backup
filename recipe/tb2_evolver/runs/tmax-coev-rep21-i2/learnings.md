# Tmax coev-rep21-i2 — evolution journal

## Round 1 — install action-loop detector

<!-- journal:frontmatter
round: 1
timestamp: 2026-04-27T00:00:00Z
hypothesis_id: h_action_loop_detect_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_001031_a8f0eb37, task_000011_d089ef35, task_001382_6c9d34ea, task_000477_de422e4d, task_000313_1dce9844, task_001207_44e97fe1]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=13/50; +4/-0 gained=task_000133_20c45b39,task_001031_a8f0eb37,task_001382_6c9d34ea,task_001697_af4b85fb; gating disabled (tolerance < 0)
expected_global_gain: "Break the 12/50 budget_exceeded-at-80-steps cluster: escalating redirect warnings on repeated-identical Bash calls give a recovery path; hard-stop at 8 reclaims budget on hopeless verbatim loops."
regression_risk: "Very low — warn fires only on 3rd consecutive IDENTICAL command; raise only on 8th. No passing R0 task shows high-repeat histograms (passing tasks 10-37 steps). Strategy-2 name-only warns disabled (name_warn_threshold=999) so single-tool Bash never false-warns."
cost_shift: "Net down — terminating 26-29x verbatim loops at repeat 8 removes ~18-21 wasted budget iterations per affected task; only adds short warning strings to tool results."
rollback_trigger: "If R2 pass_rate is flat/down AND any previously-passing R0 task (e.g. task_000009, task_000602, task_000710, task_000760, task_000790, task_000791, task_001108, task_002096) regresses to loop_detected, revert."
-->

### Why

Assigned focus `task_000010_644ab1c2` fails at `budget_exceeded`/80
steps. Root cause: the task mandates writing `/home/user/operator.py`,
which shadows stdlib `operator`; since CWD is `sys.path[0]`, every
`python3` run circular-imports and crashes the interpreter (and the
pytest verifier). The model *identified* the cause but never applied
the fix (`python3 -P` / `PYTHONSAFEPATH` / different CWD) — a model
capability gap. The harness deficiency it exposes is systemic: the
model got trapped issuing the exact same failing command cycle ~10
times with zero corrective feedback. Sweeping the round, 12 of 50
tasks hit `budget_exceeded` at exactly 80 steps, and their command
histograms are dominated by a single Bash command repeated 13-29x
verbatim and consecutively (task_001031: 29x, task_000011: 27x,
task_001382/000477: 26x, task_000313: 18x, task_001207: 13x). No R0
processor detects action-level loops — the installed
`LengthTruncationRecoveryProcessor` only handles the distinct
`finish_reason=length`-with-no-tool-call shape.

### Changes

- `config.yaml` — add
  `harnessx.processors.control.loop_detection.LoopDetectionProcessor`
  (previously absent) with `warn_threshold=3`, `threshold=8`,
  `name_warn_threshold=999`, `window_size=12`. Warns (redirects) on
  each repeated-identical Bash call from repeat 3, raises
  `LoopDetectedError` at repeat 8. No new code authored — reuses the
  vetted in-tree processor. `LoopDetectedError` maps to
  `exit_reason=loop_detected` (runloop.py:781), not `error`, so it
  does not trip the replay crash gate.
- `system_prompt.txt` — copied byte-for-byte from R0 (sidecar for
  `SiblingSystemPromptBuilder`; unchanged).

### Evidence

- `task_000010_644ab1c2` messages.json: msgs 2/17/53 `ln -s
  .../operator.py`, msgs 6/21/33 `rm && mv`, circular-import Traceback
  at msgs 5/10/20/24 — identical (command → identical error) cycle,
  narration from msg 25 "I've been stuck in a loop" yet keeps issuing
  the same calls; `exit_reason=budget_exceeded`, 80 steps.
- `task_001031_a8f0eb37`: 29 consecutive identical
  `python3 -c "from mpi4py import MPI; help(...)"` (script over
  messages.json).
- `task_000011_d089ef35`: 27 consecutive identical `kill -9 66 ...`.
- `task_001382_6c9d34ea`: 26 consecutive identical `kill -9 -p 72 ...`.
- Cluster: 12/50 R0 tasks `exit_reason=budget_exceeded`, all at 80
  steps; passing tasks by contrast finish in 10-37 steps with no
  repeat histograms.

### Uncertainty

The assigned task's specific fix (interpreter shadowing) is a model
capability gap this change does not directly supply, so
`task_000010` may still fail even with the loop broken — the flip
depends on the model finding the fix once it stops thrashing. The
robust, measurable win is on the moderate-loop tasks (recovery via
escalating warnings) and the cost/budget reclaim on the extreme
loops. Signal to watch: if loop_detected appears on a previously
passing task, `warn_threshold`/`threshold` are too tight — raise
`threshold` first before reverting.

## Round 1 — ensure verifier HTTP-client dep

<!-- journal:frontmatter
round: 1
timestamp: 2026-08-31T12:35:00Z
hypothesis_id: h_verifier_requests_dep_v1
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000106_23215092, task_000958_4bb2b05d, task_001857_24daeef3, task_002063_8c8adcfe]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Unblocks up to 5 service tasks whose verifier crashes at import (ModuleNotFoundError: requests) before the working solution is scored"
regression_risk: "Non-service tasks untouched (heuristic-gated). For matched service tasks a spurious pip check costs ~1s; install is idempotent and all failures swallowed."
cost_shift: "Negligible: one pre-loop sandbox exec on service-shaped tasks only; zero model tokens."
rollback_trigger: "If R2 shows these five still fail with the same 'import requests' collection error, the container index is offline and no install can help — revert."
-->

### Why

Assigned focus was `task_000028_7fe033ac`. The agent actually SOLVED it — it
fixed the nginx socket path, fixed/compiled/ran the C++ server, wrote the
logrotate config, and verified `http://127.0.0.1:8080/` returns `150` through
nginx ("Integration test PASSED!"). Reward is still 0 because the *verifier*
phase crashes at collection: `/tmp/test_final_state.py:6: import requests` →
`ModuleNotFoundError: No module named 'requests'`. The verifier's pytest module
uses `requests` to probe the HTTP service, but the eval container's system
Python does not ship it. Per the tb2-playbook, verifier test files are injected
after the agent session ends, so the agent cannot see or infer this dependency
— an environment deficiency, not a capability gap. This is systemic: 5 of 41
failing tasks fail with the identical `requests` ImportError at verifier
collection, and all 5 are HTTP/socket service tasks.

### Changes

- `processors/verifier_dep_ensure.py` — new `VerifierDepEnsureProcessor`
  (Control). On `on_task_start`, for service-shaped tasks (regex over the task
  description), best-effort ensures `requests` imports in the system Python
  (`python3 -c 'import requests' || pip install requests || true`). Idempotent,
  contract-neutral (never touches `event.messages`), never raises.
- `config.yaml` — registered the processor via absolute `file://` path early in
  the pipeline (`_order=5`, before the model ever runs).
- `system_prompt.txt` — copied byte-for-byte from R0 (SiblingSystemPromptBuilder
  reads it next to the config; unchanged).

### Evidence

- `task_000028_7fe033ac` tool result: "Response body: 150 / Integration test
  PASSED!"; `result.json.final_pytest.output_tail`: `import requests` →
  `ModuleNotFoundError`. Service works; only verifier import fails.
- `task_000106_23215092`: `finished=no_tool_calls`, reward 0, same import error;
  body shows `Successfully installed scipy-1.15.3` — pip installs DO succeed in
  this container, so ensuring `requests` is feasible.
- `task_000958_4bb2b05d`, `task_001857_24daeef3`, `task_002063_8c8adcfe`:
  `result.json.final_pytest` all carry the same `import requests` ImportError.
- `grep -oh "No module named ..."` across all 50 result.json → `requests` 10x
  (5 tasks x 2 pytest runs), nothing else.

### Uncertainty

If the eval container has no reachable pip index at verifier time (the earlier
scipy install may have used a cache warmed during the agent phase), the install
could fail — but it's swallowed and the task is no worse off. Real risk: if
these tasks fail for a *second* reason beyond the missing dep, fixing collection
alone won't flip them (partial credit unknown). Rollback trigger noted above.

## Round 2 — assigned focus is infra race (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-31T14:00:00Z
hypothesis_id: h_docker_name_race_noop_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None from config — the assigned failure is a pre-agent Docker container-name collision, unaddressable by the HarnessConfig surface. Documented for human infra fix (potential +9/50 recovery)."
regression_risk: "None — R1 config + system_prompt.txt copied byte-for-byte."
cost_shift: "Zero — no config change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus `task_000010_644ab1c2` in this trajectory set fails with
`status: error`, `reward: 0`, `elapsed_s: 0.6` — the agent session NEVER
STARTED. Root cause is a Docker container-name collision at `docker run`:
`Conflict. The container name "/tmax-task000010644ab1c2-1788179434" is
already in use`. This is NOT the budget_exceeded/loop shape the R1 journal
described (that analysis was on an earlier R0); in THIS trajectory dir the
failure is pure infrastructure. `recipe/tmax_eval/docker_env.py:105` builds
`name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"` — a
second-granularity timestamp plus a 20-char id truncation, so parallel/retry
runs or truncation-colliding ids clash on the container name. This happens
BEFORE the harness instantiates, so no processor / tool / template can
intercept it. Per the tb2-playbook, config.yaml controls the processor
pipeline and system prompt, NOT the benchmark infrastructure — so the focus
is genuinely unsupported by any HarnessConfig intervention.

### Changes

- `config.yaml` — copied byte-for-byte from R1 (md5 e0cadb13…). No-op.
- `system_prompt.txt` — copied byte-for-byte from R1 (md5 914fb4a8…), so the
  SiblingSystemPromptBuilder still resolves next to the config.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — documents the infra race, its 9/50
  scope, and a concrete uuid4-suffix + best-effort `docker rm -f` fix in the
  read-only recipe code.

### Evidence

- `task_000010_644ab1c2.result.json`: `status: error`, `reward: 0`,
  `elapsed_s: 0.6`, error = docker "Conflict. The container name ... already
  in use". No `messages.json` emitted (agent never ran).
- Cluster: 9/50 tasks in this round fail identically (`status: error`,
  elapsed < 1s, docker-name Conflict): task_000010, task_000140, task_000264,
  task_000505, task_000748, task_000933, task_000958, task_001321, task_001937.
- `recipe/tmax_eval/docker_env.py:105` name formula confirms the second-
  granularity + truncation race.

### Uncertainty

The R1 loop-detector and verifier-dep bets remain `pending` attribution; this
no-op does not disturb them. If the infra race is fixed by the human, several
of the 9 error-tasks should re-enter scoring and the true agent pass-rate will
rise independent of any config lever. No config-side signal to watch here.

## Round 2 — strict pre-exit self-verify checklist

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-31T15:10:00Z
hypothesis_id: h_strict_self_verify_v1
levers: [instruction]
predicted_affected: [task_000069_41f1682c, task_001979_a1e24b6f]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flip the `done`/reward=0 'declared-success-but-missed-a-literally-stated-requirement' cluster (append-vs-overwrite, exact numeric format, exact counts) by forcing requirement-by-requirement wording checks and actual re-run of stated repeatability invariants before exit."
regression_risk: "Low — self-verify injection is one-shot per task and only ADDS user text (contract: 0 violations). Main residual risk: a passing task nudged to re-run a non-idempotent step; mitigated by gating the re-run instruction on the spec itself stating a repeatability requirement."
cost_shift: "Small net up where self-verify fires (longer checklist + a few extra ls/cat/re-run+wc -l confirmations, still one-shot); displaces budget wasted shipping a wrong answer that scores 0."
rollback_trigger: "If R3 pass_rate is flat/down AND any previously-passing `done` task regresses (re-run nudge breaks a non-idempotent step, or longer checklist tips a borderline task over budget), revert to stock CustomSelfVerifyProcessor."
-->

### Why

Assigned focus `task_000069_41f1682c` exits `done`/`no_tool_calls`,
reward=0. The agent wrote both files, ran the deploy script, and declared
success — but the task states the script must "redirect (**append**) the
standard output ... to /home/user/deployment.log", and the verifier
clears the log, runs the script twice, and asserts 2 lines. The agent
used truncating `>` (not `>>`), so the log has 1 line. It even ran an
"idempotency test" but only *eyeballed* one output; it never counted
lines after two runs against the spec's stated multiplicity. Same shape
recurs on `task_001979_a1e24b6f`: the agent claimed "rounded to 2 decimal
places" while the verifier shows `'33.0' != '33.00'` — an exact-format
requirement its `cat`-and-eyeball self-check accepted. Both are
exact-wording / observable-invariant mismatches that the stock
self-verify checklist (`benchmarks/terminal_bench_2/harness.py::_SELF_VERIFY_MSG`)
is too abstract to catch: it says "validate your verification method" but
never forces per-requirement literal-wording matching or actually
reproducing a stated repeatability scenario.

### Changes

- `processors/strict_self_verify.py` — new `StrictSelfVerifyProcessor`,
  subclasses the read-only `CustomSelfVerifyProcessor` (same singleton
  group `tb2_self_verify`, same one-shot fire + +1-user-message contract);
  overrides only the injected checklist text with a general, stricter one
  (enumerate each explicit requirement and match literal wording — append
  vs overwrite, exact format/precision/trailing-zeros, exact
  counts/ordering, exact paths; and for any stated repeatability
  requirement, actually re-run >=2x and re-measure observable state).
- `config.yaml` — replaced `benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor`
  with `file://…/processors/strict_self_verify.py::StrictSelfVerifyProcessor`
  (absolute path), same pipeline position.
- `system_prompt.txt` — copied byte-for-byte from R1 (SiblingSystemPromptBuilder sidecar; unchanged).

### Evidence

- `task_000069_41f1682c` messages.json msg 55: script body
  `/home/user/edge_router > /home/user/deployment.log` (truncate); msg 175
  "idempotency test" re-runs + `cat`s the log, sees one line, concludes
  "idempotent"; msg 248 asserts idempotency verified.
  `final_pytest.output_tail`: "deployment.log should have exactly 2 lines
  after running twice, but has 1".
- `task_001979_a1e24b6f` final assistant: "rounded to 2 decimal places";
  `final_pytest.output_tail`: "At index 4 diff: '33.0' != '33.00'".
  `_tb2_self_verify` fired (agent self-verified) yet missed the exact
  precision.
- Cluster context: 12 `done`/reward=0 tasks in this round; the requests-
  import subset is handled elsewhere; numeric-algorithm subsets are model
  capability gaps; these two are exact-wording spec-detail misses this
  checklist directly targets.

### Uncertainty

The strengthened checklist is an instruction, not a mechanism that
guarantees compliance — a weak model may still skip a step. The measurable
signal to watch: whether task_000069 / task_001979 flip, and whether any
previously-passing `done` task regresses (re-run nudge on a non-idempotent
step). If flat with a regression, revert to the stock processor.

## Round 2 — output-identity loop detector

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-01T00:00:00Z
hypothesis_id: h_output_loop_detect_v1
levers: [control]
predicted_affected: [task_001832_dd672877]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Reclaim budget across the loop_detected/output-loop cluster (11/50 tasks) by catching loops the input-fingerprint detector misses, and give weak models a more actionable 'output is byte-identical' warning that may recover a small number of them."
regression_risk: "Low — 6x byte-identical output is a strong 'nothing changed' signal; min_output_len=12 excludes short recurring outputs; passing R0/R1 tasks (9-68 steps) show no output-repeat histograms and should not trip."
cost_shift: "Net down — terminates output-loops ~2 repeats earlier than the input detector's fragmented verbatim path and catches loops it misses; adds only a short warning string to affected results."
rollback_trigger: "If R3 regresses any previously-passing task (task_000009/000602/000710/000760/000790/000791/001108/002096/001031/001382/001697/000682/000133) to loop_detected, raise threshold to 8 or min_output_len, or revert."
-->

### Why

Assigned focus `task_000015_89886d8d` fails `loop_detected` @ 20 steps. Root
cause is a model capability gap: it calls `tesseract <img> -o raw.txt`, but
tesseract 4.1.1 wants the output base as a positional arg, so every OCR call
fails with the identical `read_params_file: Can't open ...` error; it never
OCRs the schema, never writes the required `/home/user/migrate.py` and
`/home/user/test_parser.py`, and the verifier fails on `os.path.isfile(...)`.
This is NOT a regression — under R0 (no loop detector) the same task would
have hit `budget_exceeded` @ 80 and still failed. The tesseract-syntax fix is
domain knowledge and out of scope. But the failure exposes a general harness
weakness: the R1 input-based `LoopDetectionProcessor` fingerprints tool
*inputs*, so when the model reissues the same failing call with slight surface
variation (`-c`, here-doc, `os.makedirs`), its consecutive-run counter keeps
resetting and it burns budget on a loop that is, by *output*, going nowhere.
The same shape appears in the broader `loop_detected` cluster (11/50 tasks),
e.g. task_001832 repeats identical `echo "Test 0 1 2 3 4: ..."` output from
step 15→22.

### Changes

- `processors/output_loop_detection.py` — new `OutputLoopDetectionProcessor`
  (Control). Fingerprints the tool *result* (name + normalised output);
  warns from repeat 3 with a concrete "output is byte-identical, nothing is
  changing" nudge, raises `LoopDetectedError` at repeat 6. `min_output_len=12`
  guards short/empty recurring outputs; resets on compaction and per-task.
- `config.yaml` — register the processor via absolute `file://` path
  immediately after the input-based `LoopDetectionProcessor` (`_order=21`).
- `system_prompt.txt` — copied byte-for-byte from R1 (SiblingSystemPromptBuilder
  sidecar; unchanged).

### Evidence

- `task_000015_89886d8d` `.messages.json`: normalised-output Counter shows
  `STDOUT: STDERR: read_params_file: Can't open /tmp/ocr/raw.txt...` repeated
  **10x** across command variations; assistant narrates "let me try a Python
  script" but re-issues the same failing OCR call. `exit_reason=loop_detected`,
  20 steps; verifier fails `test_migrate_script_accuracy` /
  `test_property_based_tests` (files never written).
- `task_001832_dd672877` tool-call dump steps 15-22: identical
  `echo "Test 0 1 2 3 4: $(/app/recommender 0 1 2 3 4)" ...`; killed @ 21;
  verifier fails `test_pipeline_script_exists_and_executable`.
- Round shape: 11/50 tasks now `exit_reason=loop_detected`, all reward 0;
  passing tasks finish 9-68 steps with varied outputs and no repeat histograms.

### Uncertainty

The assigned task will likely NOT flip — the tesseract-syntax capability gap
remains. The robust, measurable win is faster budget reclaim on output-loops
plus a strictly-more-actionable warning; a small number of weak-model loops
may recover on the concrete nudge. Signal to watch: if `loop_detected` appears
on a previously-passing task, `threshold`/`min_output_len` are too tight —
raise `threshold` to 8 before reverting.

### needs_from_human

`task_000015`: requires tesseract 4.1.1 CLI knowledge (output base is a
positional argument, not `-o`) plus multi-part OCR→parser→hypothesis-test
synthesis beyond the 4B model — model capability gap, no harness fix; skip.

## Round 2 — ensure verifier requests dep (system python3)

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-31T13:20:00Z
hypothesis_id: h_verifier_requests_dep_v2
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000106_23215092, task_002063_8c8adcfe]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Unblocks the HTTP/socket-service tasks whose only failure is the verifier's `import requests` crashing at pytest collection before the working solution is ever scored (3 pure-collection failures observed; generalizes to any future service task the verifier probes with requests)."
regression_risk: "Very low — service-gated heuristic leaves non-service tasks untouched; idempotent import-guard makes already-present requests a no-op; contract-neutral (yields event unchanged, passed contract check); best-effort swallows every failure path so an offline index leaves the task no worse than today."
cost_shift: "Negligible — one guarded ~1s sandbox exec on service-shaped tasks only; zero model tokens."
rollback_trigger: "If R3 shows these tasks still fail with the identical `import requests` collection error, the container pip index is offline at agent time and no install can help — revert. Also revert if any previously-passing service task regresses to a container-state error."
-->

### Why

Assigned focus `task_000028_7fe033ac` reports `reward=0`, but the agent SOLVED
the task (nginx reverse proxy → C++ backend integration returned the expected
`150`). The failure is purely in the verifier phase: `/tmp/test_final_state.py`
(injected after the agent exits, invisible during the session) does
`import requests` under the container's system `python3` (`/usr/lib/python3.10`),
which the base image does not ship, so pytest crashes at COLLECTION. This is an
environment deficiency the agent cannot infer, and it is systemic: exactly 4 of
50 R0 tasks hit a `No module named` error and all 4 name `'requests'`; the first
three (000028, 000106, 002063) are pure verifier-collection failures.

### Changes

- `processors/verifier_dep_ensure.py` — new `VerifierDepEnsureProcessor`
  (control). On `on_task_start`, for service-shaped tasks (regex over the task
  description), best-effort ensures `requests` imports under the SYSTEM
  `python3` via `python3 -m pip install` (with pip/pip3 fallbacks), guarded by
  `python3 -c 'import requests'` so it is idempotent. Contract-neutral, never
  raises. `_order=5` so it runs before the model.
- `config.yaml` — registered the processor early via absolute `file://` path;
  copied R1 baseline byte-for-byte otherwise.
- `system_prompt.txt` — copied byte-for-byte from R1 (SiblingSystemPromptBuilder
  sidecar; unchanged).

### Evidence

- `task_000028_7fe033ac.result.json`: `final_pytest.rc=2`, tail =
  `/tmp/test_final_state.py:6: import requests` → `ModuleNotFoundError`,
  `Interrupted: 1 error during collection`; `initial_pytest.passed=true`.
- Cluster: `grep "No module named" *.result.json` → 4 tasks, string `'requests'`
  8x (4 tasks × 2 pytest runs), nothing else.
- pip works in-container: `task_000106_23215092.messages.json` shows
  `Successfully installed numpy-2.2.6` / `scipy-1.15.3` during the agent phase.
- API verified: `TaskStartEvent.task_description` (events.py:86);
  `get_current_sandbox()` (base.py:188) set at harness.py:1196 BEFORE run_loop
  (harness.py:1292) where on_task_start fires (runloop.py:206);
  `sandbox.exec(cmd, timeout=)` (base.py:57).

### Uncertainty

If the eval container has no reachable pip index at agent time, the install
fails (swallowed → task no worse off). If a task fails for a second reason
beyond the missing dep, fixing collection alone won't flip it. R1's
`h_verifier_requests_dep_v1` was never merged into the R1 baseline (grep
confirms absent), so this v2 ships it into the live config with a hardened
system-interpreter-targeted install command; watch R3 attribution to score it.

## Round 2 — detach backgrounded services

<!-- journal:frontmatter
round: 2
timestamp: 2026-04-27T00:00:00Z
hypothesis_id: h_bg_service_detach_v1
levers: [control]
predicted_affected: [task_000011_d089ef35]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flip the 'server-in-background dies before verifier' class: agent solves the task and its own in-session probe passes, but the backgrounded service is SIGHUP'd at session teardown so the verifier hits ConnectionRefused. Rewriting bare-and service launches under setsid makes them survive to verifier time."
regression_risk: "Low - rewrite is Bash-only, standalone-ampersand only, launcher-shaped only, skips setsid/nohup/disown and install/build, never blocks/raises, passes through unchanged on any doubt. Worst case: a job the agent meant to be transient is detached and its output goes to a log."
cost_shift: "Negligible - zero model tokens (silent command rewrite), one extra setsid exec per backgrounded launch; removes wasted verifier-phase failures."
rollback_trigger: "If R3 shows task_000011 still fails with ConnectionRefused (killer is cgroup/container teardown, not session SIGHUP - setsid insufficient), OR a previously-passing service task regresses because a transient backgrounded job now lingers, revert the processor."
-->

### Why

Assigned focus `task_000011_d089ef35` (scientific_computing) writes a C TCP
server on 127.0.0.1:9090, compiles it, and runs it in the background. The
agent SOLVED the task: msg 45/46 confirm port 9090 is LISTEN during the
session, msg 53's netcat probe returns the correct MSE for all four quadrants
(8.25/17.25/68.25/93.25). exit_reason=done, finished=no_tool_calls. Yet
final_pytest fails with 5x ConnectionRefusedError (Errno 111) - the server is
gone by verifier time. Root cause is the structural gap the tb2-playbook names
("Background process dies after agent exits"): every launch (msgs 26/28/36/38)
is a bare `/home/user/mesh_server` backgrounded with a plain ampersand - a job
of the ephemeral Bash tool shell, with no setsid/nohup/disown. When that shell
(and later the whole agent session) exits, the child is SIGHUP'd / reaped (msg
53 shows four defunct mesh_server zombies plus one live PID). This is a harness
mechanism gap, not a capability gap: the small model believes it succeeded and
cannot be relied on to apply nohup discipline (it killed and relaunched five
times).

### Changes

- `processors/bg_service_detach.py` - new `BgServiceDetachProcessor` (Control,
  order=16, runs right after BgInstallGuard). on_before_tool for Bash only:
  when a command backgrounds a launcher-shaped process (exec path or known
  runtime: python/node/java/gunicorn/uvicorn/nginx/socat/...) with a standalone
  background operator and is not already detached (no setsid/nohup/disown) and
  is not an install/build (owned by BgInstallGuard), rewrites that segment to
  run under setsid with stdin from /dev/null and stdio appended to a log.
  Detaches into a new session immune to SIGHUP on shell exit; stdio redirect
  only added when the author supplied none. Never blocks, never raises; passes
  through unchanged on any parse doubt.
- `config.yaml` - copied R1 byte-for-byte and inserted the new processor via
  absolute file:// path after BgInstallGuard.
- `system_prompt.txt` - copied byte-for-byte from R1 (SiblingSystemPromptBuilder
  sidecar; unchanged).

### Evidence

- task_000011_d089ef35 messages.json msgs 26/28/36/38: mesh_server launched
  with a bare background ampersand, no detachment. msg 45/46: port 9090 LISTEN
  confirmed. msg 53: netcat probe returns correct MSE for all quadrants; four
  defunct zombies + one live server PID. result.json final_pytest: 5 tests
  fail, all Connection refused to 127.0.0.1:9090.
- Sweep of all 50 result.json for ConnectionRefused in final_pytest:
  task_000011 (exit done) and task_000313 (exit loop_detected). task_000313's
  root cause is a kill/relaunch loop terminated by the R1 loop detector - a
  different mechanism - so this candidate claims only task_000011.

### Uncertainty

If the true killer is container/cgroup teardown rather than session SIGHUP,
setsid alone won't save the process and task_000011 stays red - the playbook
says these processes DO persist when not killed, which points at session
teardown, so setsid should suffice. Single clean member this round; the value
is the generalising mechanism for the server-in-background class, which recurs
across TB2 service tasks. Rollback trigger noted above.
