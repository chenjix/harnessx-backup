# Evolve Journal — tmax-coev-rep24-i1

## Round 1 — guard self-sabotaging exits

<!-- journal:frontmatter
round: 1
timestamp: 2026-05-14T00:00:00Z
hypothesis_id: h_unmet_requirement_exit_guard_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=24/50; +1/-8 gained=task_001321_658ce4a8 lost=task_000684_1a33ef37,task_000760_76ba653c,task_000912_770802f8,task_001088_6f566806; score 0.4800 < incumbent(mean) 0.6200 - tol 0.0400 but unique solves task_001321_658ce4a8 — keep as pivot, incumbent unchanged
expected_global_gain: "Recovers no_tool_calls exits where the agent solved the task but shipped the deliverable at the wrong path/name/form after a secondary obstacle (TB2 'correct logic, wrong path' class)."
regression_risk: "Content-gated + one-shot; silent on clean exits and cannot loop. Worst case one extra model turn on a truly-complete task that happened to use blocker-like phrasing."
cost_shift: "Negligible aggregate; +~1 model turn only on the subset of exits containing acknowledged-blocker phrasing."
rollback_trigger: "If R2 shows pass_rate flat/down AND new T->F regressions on tasks that previously exited cleanly, revert the processor."
-->

### Why

Assigned focus `task_000010_644ab1c2` failed `test_operator_script_exists`:
`/home/user/operator.py` did not exist at exit. The agent had in fact built a
correct, tested pipeline (backup tarball created, port-forward up, manifests
applied — `api_success.log` confirms), but the required filename `operator.py`
shadows the stdlib `operator` module, so `python3 /home/user/operator.py`
crashed on the transitive `import operator`. The agent diagnosed the shadow,
concluded (wrongly) that the *only* fix was to rename the file, and left the
deliverable at `k8s_operator.py`. The existing `CustomSelfVerifyProcessor`
fired its one-shot checklist and the agent complied — but confirmed the
*wrong* file, because it had already accepted the wrong path. It then exited
verbalising the unmet requirement: "The only issue is that the script can't be
named `operator.py` ... The task is complete." This is the TB2 "correct logic,
wrong path" hard-failure mode: a mechanical exit-gate gap, not a capability or
knowledge gap.

### Changes

- `processors/unmet_requirement_exit_guard.py` — new one-shot
  `UnmetRequirementExitGuard` (Control). On an exit turn (finish_reason in
  {end_turn, stop}, no tool calls) whose text matches acknowledged-blocker
  phrasing, injects one keepalive + a focused nudge to satisfy the exact
  deliverable path/name instead of abandoning it. Orthogonal to and ordered
  after `CustomSelfVerifyProcessor` (order 91 vs 90).
- `config.yaml` — register the processor via `file://` after
  `CustomSelfVerifyProcessor`.

### Evidence

- `task_000010_644ab1c2` result.json: `final_pytest` fail
  `test_operator_script_exists`; `exit_reason=done`, `finished=no_tool_calls`,
  42 steps.
- messages step 20: traceback `import subprocess ... import operator` (stdlib
  shadow by the agent's own `operator.py`).
- messages step 50/74: pipeline works as `k8s_operator.py` (manifests applied,
  backup created) — solution is correct, only the path is wrong.
- messages step 79-82: self-verify fires once; agent confirms `k8s_operator.py`
  (not the required path).
- messages step 83 (final): "The only issue is that the script can't be named
  `operator.py` ... The task is complete." → exits with deliverable absent.

### Uncertainty

The nudge must fire at the right boundary and the agent must then act on it
(copy/move the artifact to the required path, or run the required file from a
non-shadowing cwd). The regex is intentionally general to catch the wider
"knowingly-incomplete exit" class; if it proves too broad (fires on complete
tasks) or too narrow (misses the phrasing), tune the pattern next round. Single
distinct task in this batch shows the exact shadow mechanism, but the exit-gate
mechanism it fixes is generic; watch R2 attribution to confirm it generalises
rather than only flipping the one focus task.

## Round 2 — OCR-hardening discipline

<!-- journal:frontmatter
round: 2
timestamp: 2026-05-15T00:00:00Z
hypothesis_id: h_ocr_extraction_validation_v1
levers: [instruction]
predicted_affected: [task_000015_89886d8d, task_000505_50b5162d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the two failing members of a 6-task OCR-extraction cluster (image to structured data via tesseract) by adding extraction-fidelity + cross-validation discipline that generalises to any unseen image-extraction task."
regression_risk: "Guidance is conditional on image/OCR tasks; four passing OCR tasks (000206/000338/000536/001652) extract short low-ambiguity strings that a naive pass already gets right, so an extra validation pass should not change a correct result. Non-OCR tasks ignore the block."
cost_shift: "Small positive on image tasks only (1-3 extra Bash calls for preprocess + second OCR pass + a validation run); negligible aggregate since most tasks touch no image."
rollback_trigger: "If R3 pass_rate is flat/down AND any previously-passing OCR task (000206/000338/000536/001652) regresses T to F, revert the prompt block."
-->

### Why

Assigned focus `task_000015_89886d8d` fails (`final_pytest` accuracy 0.3564,
below the 0.98 threshold). The task requires OCR-reading `/app/routing_schema.png`
(tesseract) to extract a URL to JSON routing schema, then writing `migrate.py`
that reproduces those mappings on a hidden 2000-URL set. The trajectory shows
the agent ran OCR once (pre-first-compaction, so raw output is not in the
visible log), extracted a `ROUTES` table, then spent the entire visible
trajectory (~50 msgs) debugging its `hypothesis` property test — never
re-examining whether the extracted rules were correct, and never using the
provided `/home/user/sample_urls.txt` to validate the end-to-end mapping. The
0.36 accuracy means the OCR-derived mappings were substantially wrong and
trusted as ground truth. This is one member of a recurring 6-task
OCR-extraction cluster; `task_000505_50b5162d` fails the same way ("2 of 2 evil
bypassed" — its `detect_trojan.sh` grepped for an SSH key string that was off
by at least one OCR character). The four passing OCR tasks extract short,
low-ambiguity strings a naive tesseract pass gets right. Root cause is
knowledge, not capability: tesseract + Bash + preprocessing tools are all
present; the agent just doesn't apply OCR-hardening discipline.

### Changes

- `system_prompt.txt` (sibling of config, read by `SiblingSystemPromptBuilder`)
  — added a general OCR-hardening strategy block: treat the first OCR pass as a
  draft; improve fidelity (preprocess image, try more than one PSM/engine mode
  and reconcile); cross-validate extracted values against any provided sample/
  reference data and structural expectations before building downstream logic;
  verify exact-match values (keys/tokens/matrices) character-for-character. No
  task literals embedded.
- `config.yaml` — byte-identical copy of R1 (keeps `SiblingSystemPromptBuilder`
  and the existing processor pipeline); the intervention is the sibling prompt.

### Evidence

- `task_000015_89886d8d` result.json: `final_pytest` FAILED
  `test_migrate_script_accuracy` — "Accuracy metric 0.3564 is below the 0.98
  threshold"; `exit_reason=done`, `finished=no_tool_calls`, 78 steps.
- `task_000015` messages msg 56 (assistant): lists three extracted routes as
  fact and declares "The task is complete"; msgs 4-54 are all `test_parser.py`
  debugging with zero OCR re-verification. `sample_urls.txt` provided (msg 0),
  never used to validate the mapping.
- `task_000505_50b5162d` result.json final_pytest output tail:
  "2 of 2 evil bypassed: cat_evil, ls_evil" — extracted SSH key did not match
  the planted key (single OCR char error is fatal), reward=0.
- Passing OCR cluster (protected): task_000206 (r=1), task_000338 (r=1, 3x3
  matrix), task_000536 (r=1, single Employee ID), task_001652 (r=1, single
  token) — all extract short strings; discipline should not regress them.

### Uncertainty

Instruction-lever OCR discipline only helps if the model acts on it — runs a
preprocess/second-pass and actually cross-checks against sample data rather
than paraphrasing the intent. If R3 attribution shows the two focus tasks still
fail with the same un-validated-OCR shape, the gap may need a Control hook that
detects image inputs and injects a preprocessing/OCR-reconciliation scaffold;
watch for any T to F regression on the four passing OCR tasks as the rollback
signal.

## Round 2 — module-shadow crash recovery

<!-- journal:frontmatter
round: 2
timestamp: 2026-05-15T00:00:00Z
hypothesis_id: h_module_shadow_recovery_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Recovers the 'required deliverable filename shadows a stdlib module' slice of the TB2 'correct logic, wrong path' class (operator.py/test.py/random.py/queue.py/token.py/types.py/email.py/...). Reactive at the crash, not at exit."
regression_risk: "Near-zero: two-conjunct signature gate (circular-import phrase AND stdlib path in traceback) + one-shot; silent unless a real shadow crash occurs. Worst case +1 model turn on a genuine stdlib circular-import task."
cost_shift: "Negligible aggregate; +1 user message and at most +1 model turn only on the rare shadow-crash subset."
rollback_trigger: "If R3 shows task_000010_644ab1c2 still failing AND any T->F regression on a task that previously exited cleanly, revert the processor."
-->

### Why

Assigned focus `task_000010_644ab1c2` still fails (reward 0). The task requires
a script at the EXACT path `/home/user/operator.py`. `operator.py` shadows the
Python stdlib `operator` module, so running `python3 /home/user/operator.py`
from `/home/user` triggers a stdlib circular-import crash (script dir is first
on `sys.path`). At msg 49 the agent `mv`'d its working script to the correct
path `operator.py` and ran it — msg 50 crashed on the shadow. Instead of
recognising this as a working-directory problem, at msg 51 it concluded "the
naming conflict is real" and MOVED THE FILE BACK to `k8s_operator.py`,
destroying the correct deliverable and failing `test_operator_script_exists`.
This is the tb2-playbook "correct logic, wrong path" hard-failure mode: a
mechanical footgun, not a knowledge gap (the agent correctly diagnosed the
shadow — it just chose the wrong remedy). The fix must redirect at the moment
the crash appears, before the destructive undo.

### Changes

- `processors/module_shadow_recovery.py` — new one-shot
  `ModuleShadowRecoveryProcessor` (Control). On `on_after_tool`, when Bash
  output matches a stdlib circular-import signature (partially-initialized
  module / cannot-import-from-partially-initialized) AND the traceback walks
  through the standard library path, injects one keepalive user message
  explaining that this is a module-shadow at RUN time (not a reason to rename
  the deliverable) and that the fix is to keep the file at its required path and
  run it from a non-shadowing cwd (`cd /tmp && python3 /home/user/<file>.py`).
  Order 92, after the self-verify (90) and exit-guard (91) slots.
- `config.yaml` — register the processor via `file://` after
  `BehavioralSelfVerifyProcessor`.

### Evidence

- `task_000010_644ab1c2.result.json`: `final_pytest` fails
  `test_operator_script_exists` (`/home/user/operator.py` absent) and
  `test_api_success_log`; `finished=no_tool_calls`, `exit_reason=done`, 75 steps.
- messages msg 0: "write a Python script at `/home/user/operator.py`".
- messages msg 49 (tool_call): `mv .../k8s_operator.py .../operator.py &&
  python3 /home/user/operator.py &` — file placed at CORRECT path.
- messages msg 50 (tool result): `AttributeError: partially initialized module
  'functools' has no attribute 'lru_cache' (most likely due to a circular
  import)` walking `/usr/lib/python3.10/...`.
- messages msg 51 (assistant): "The naming conflict is real ... I need to keep
  the script at `/home/user/k8s_operator.py`" → moves it back (self-sabotage).
- messages msg 58 (final): declares done with file at wrong name.

### Uncertainty

Single task in this batch shows the exact shadow mechanism, but the mechanism is
a generic Python footgun spanning a class of required-filename collisions, so
the fix is written for the class. Distinct from the R1/c0 exit-gate guard (which
was NOT merged into the incumbent and fired only at exit on NL phrasing — too
late here). The task also fails `test_api_success_log` (config.yaml never
applied) — that residual is an agent-logic gap in the pexpect/port-forward loop,
NOT a harness deficiency; no harness fix without task-specific knowledge, so
skip. Watch R3: if the path test flips but the log test remains, that confirms
C-001 closed the harness-fixable half.

## Round 2 — lingering-process exit guard

<!-- journal:frontmatter
round: 2
timestamp: 2026-05-14T00:00:00Z
hypothesis_id: h_lingering_process_exit_guard_v1
levers: [control]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the process-table-end-state-at-exit gap in system_administration — a documented TB2 class (lingering PIDs / daemon left in wrong state graded from the final process table). Directly targets task_000140; generalizes to any service/lifecycle/cleanup task graded on the final process table."
regression_risk: "Very low. Non-destructive (no auto-kill); one-shot; fires only when a background process was started AND the agent's most recent shell command was itself a background launch. Silent on clean exits, on exits not preceded by a bg launch, and on tasks with no bg processes. Worst case: +1 model turn on a task whose final act was a legit bg launch (reconciliation is cheap insurance; keep-daemon-up tasks only asked to confirm alive)."
cost_shift: "Negligible aggregate; +~1 model turn only on exits whose immediately-preceding command was a background launch."
rollback_trigger: "If R3 shows pass_rate flat/down AND new T->F regressions on tasks that previously exited cleanly with an intentional live daemon, revert the processor."
-->

### Why

Assigned focus `task_000140_01c78b42` (system_administration) failed
`test_no_lingering_service_processes`: the verifier found 3-4 lingering
`vm_service` PIDs after the agent exited. The functional deliverable was
correct (`vm_setup.log` contained `PROVISIONED_VM_FOR: admin_alice`), but the
graded invariant is the *state of the process table after exit*, not the
console output the agent saw. The agent's `test_pipeline.sh` starts
`vm_service &`, saves the PID, and `kill -TERM`s only that one PID — so
re-running the pipeline (which the agent did more than once, including a final
re-run triggered *after* the self-verify checklist fired) accumulates
orphaned background processes. A `pkill -f vm_service` "cleanup" returned exit
143 (interrupted) and was never re-checked. The agent's last action before
exit was a background launch, and it never inspected the live process table.
This is the "runtime process invariant, not a convenient final snapshot"
failure class; the existing behavioral self-verify checklist advises cleanup
in prose but the unclean table is created on a *later* exit turn, so prose
alone doesn't close it — a mechanical exit-gate hook is required.

### Changes

- `processors/lingering_process_guard.py` — new one-shot, non-destructive
  `LingeringProcessGuard` (Control, order 92, after the self-verify gate). On
  an exit turn, when the session started background processes AND the agent's
  most recent shell command was itself a background launch (trailing lone `&`,
  `nohup`/`setsid`/`disown`/`start-stop-daemon`/`systemctl start`/`service …
  start`), it injects one keepalive + a focused nudge to `ps`-enumerate every
  matching PID (including duplicates from repeated runs) and reconcile the
  live process table against the task's required end state — reap all
  lingering PIDs for cleanup tasks, confirm still-alive for keep-daemon-up
  tasks. It never kills anything itself.
- `config.yaml` — register the processor via `file://` after
  `BehavioralSelfVerifyProcessor`.

### Evidence

- `task_000140_01c78b42` result.json: `agent.finished=no_tool_calls`,
  `steps=12`, `final_pytest.passed=false`, assertion
  `Lingering vm_service processes found: ['332','558','770','971']`.
- messages step ~9/11: `bash /home/user/test_pipeline.sh` run repeatedly,
  each start spawning a new `vm_service &`; shutdown only kills the last saved
  PID; a `pkill -f vm_service` returns exit 143 and is never re-verified.
- messages final turn: "The task is complete." → exit with the freshly
  started background process (and accumulated earlier ones) still alive.
- Adjacent supporting: `task_000118_3043e92d` `test_deployment_monitor` —
  same "runtime process invariant, not the settled snapshot" class (peak
  log-dir size while workers run); different failure shape / exit reason
  (budget_exceeded), so this guard does not fire there but the class recurs.

### Uncertainty

The guard must fire at the right boundary (exit turn whose preceding command
was a background launch) and the agent must then act (reap or confirm). The
bg-launch regex is intentionally general to catch the class; if it proves too
broad (fires on complete keep-daemon-up tasks unnecessarily) it still only
adds one confirm-alive turn — low cost. If too narrow (misses a background
launch shape), tune the pattern next round. Single sharp instance in this
batch, but the exit-gate mechanism it fixes is a documented generic TB2
class; watch R3 attribution to confirm it generalises rather than only
flipping the one focus task.

## Round 2 — HTTP-verifier dependency guard (`import requests` collection error)

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-03T00:00:00Z
hypothesis_id: h_http_verifier_requests_dep_guard_v1
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000958_4bb2b05d]
cited_candidates: [C-001]
expected_global_gain: "Closes the 'correct HTTP service, but the verifier test module ModuleNotFoundError('requests') at pytest collection -> score 0' cluster (>=2 tasks across system_administration + data_querying). Generalizes to any TB2 task whose grader imports the requests HTTP client, which is the common test client on this benchmark."
regression_risk: "Low. Preserves the behavioral self-verify checklist byte-for-byte (HttpVerifierDepGuard extends BehavioralSelfVerifyProcessor); the extra step fires ONLY when the task description matches an HTTP-verifier signal (verifier/grader + http/endpoint/port/curl). Non-HTTP tasks see the identical prior checklist. Worst case on a matched task where requests already exists: one cheap importable check + no-op. Install is best-effort; if pip/apt/internet are blocked it fails silently and the task is no worse than today."
cost_shift: "Negligible; +~1 short Bash check (and possibly one install) on the HTTP-verifier subset only, at the single exit turn. No per-step overhead."
rollback_trigger: "If R3 shows pass_rate flat/down AND new T->F regressions on HTTP tasks that previously passed (e.g. an install step wedges a service or eats the time budget), revert to plain BehavioralSelfVerifyProcessor."
-->

### Why

Assigned focus `task_000028_7fe033ac` (system_administration) failed with
`reward=0`, `final_pytest.rc=2` — but not on any assertion. The failure is a
pytest **collection** error: `ImportError while importing test module
/tmp/test_final_state.py ... import requests -> ModuleNotFoundError: No module
named 'requests'`. The agent's solution was fully correct: its own HTTP smoke
test (messages IDX 65) returned `HTTP/1.1 200 OK` with body `150` from the
nginx -> C++ unix-socket pipeline, and IDX 66 confirmed all five objectives.
The verifier test file is injected only in the verifier phase and imports the
Python `requests` library; this container image lacks `requests`, so the grader
crashes at collection and scores a correct service 0. The agent had no way to
know the hidden grader needed `requests` (the task text mentions "HTTP
requests" only in the networking sense) and never installed it.

The identical mechanism recurs on `task_000958_4bb2b05d` (data_querying): a C++
HTTP server on 127.0.0.1:9090, same `import requests` collection error at
`/tmp/test_final_state.py:4`. Cross-check `task_001498_df8254c9` (data_science,
HTTP verifier) passed — its image already had `requests`. So availability is
inconsistent across task images; a well-behaved solution silently loses only
where the grader dep is absent. This is a mechanical verifier-environment gap
across the HTTP-service class, not a capability or knowledge gap — Control lever.

### Changes

- `processors/http_verifier_dep_guard.py` — new `HttpVerifierDepGuard`,
  extends `BehavioralSelfVerifyProcessor` (same `tb2_self_verify` singleton
  slot, `_order=90`). Captures `task_description` in `on_task_start`; when the
  behavioral base fires its one-shot exit checklist AND the task text signals an
  HTTP/network verifier, appends one general step: confirm `python3 -c "import
  requests"` and, if it fails, install the common HTTP test client (pip / apt)
  so the grader can run. No task IDs, ports, paths, or literals from any
  trajectory.
- `config.yaml` — replace the `BehavioralSelfVerifyProcessor` file:// entry with
  `HttpVerifierDepGuard` (which subclasses it, preserving all behavioral
  behavior).

### Evidence

- `task_000028_7fe033ac` result.json: `final_pytest` rc=2, output_tail
  ModuleNotFoundError('requests') at collection; messages IDX 65 shows the
  service answering `HTTP/1.1 200 OK ... 150`.
- `task_000958_4bb2b05d` result.json: same collection error at
  `/tmp/test_final_state.py:4`.
- `task_001498_df8254c9` result.json: HTTP verifier, `reward=1` — `requests`
  already present in that image (control that shows the guard is scoped to the
  absent-dep case).

### Uncertainty

The install must actually succeed in-container. Internet is blocked in the
agent phase per the TB2 playbook, so the nudge is best-effort: it tries pip then
apt (`python3-requests`) and relies on a local wheel/apt cache if one exists. If
none is reachable, the step is a harmless no-op and the task is no worse off.
Watch R3 attribution: if neither focus task flips, the container has no offline
install path and the fix is inert (revert); if they flip, the dep-guard
generalizes to the wider HTTP-service grader class.

## Round 2 — stateless-shell background guard

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-03T03:41:52Z
hypothesis_id: h_stateless_shell_bg_guard_v1
levers: [control]
predicted_affected: [task_000118_3043e92d]
cited_candidates: [C-002]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Unblocks the system_administration daemon/service/monitor cluster where the agent must test a background process co-running with a foreground workload across the stateless Bash tool — a recurring TB2 structural trap."
regression_risk: "Low: additive one-shot user hint gated on a bare-and-then-separate-call heuristic; silent on the ~90 percent of tasks that never background a persistent process. Worst case one extra short user turn on a task using legitimate nohup-style backgrounding (excluded via nohup/setsid/disown/wait guards plus per-task fire cap)."
cost_shift: "Negligible aggregate; plus ~1 short user message only on the subset of tasks exhibiting the antipattern (capped at max_fires=3)."
rollback_trigger: "If R3 pass_rate is flat/down AND new T-to-F regressions appear on tasks that previously backgrounded processes cleanly, revert the processor."
-->

### Why

Assigned focus task_000118_3043e92d (system_administration) failed with
exit_reason budget_exceeded at 80 steps; the grader shows peak log dir far above
its threshold, so the monitor never worked. Root cause is a structural fact
about the execution model, invisible from the task text: each Bash tool call
runs as an independent docker exec bash -lc command, so a process backgrounded
with a bare ampersand in one call is NOT co-scheduled with a command issued in a
later call — that shell and its background child tear down when the call
returns. The agent launched the monitor backgrounded in one call and the
deployment in a separate call, saw "No worker processes found. Exiting." every
time, never grasped why, and spun in a degenerate repetition loop until the
budget was gone. The existing length-recovery processor fired on the symptom but
its "issue one Bash command" nudge never told the agent WHY its test was
structurally impossible.

### Changes

- processors/stateless_shell_guard.py — new StatelessShellBackgroundGuard
  (Control). Detects the "background a persistent process with a bare ampersand
  in one call, then issue a separate call that does not co-schedule" antipattern
  and injects one-shot execution-model context (each Bash call is a fresh shell;
  co-run processes in a SINGLE command, or use nohup). Excludes already-correct
  forms, caps at max_fires=3 per task, mirrors the length-recovery before_model
  injection contract.
- config.yaml — register the processor via file path after the length-recovery
  processor, before compaction.

### Evidence

- task_000118_3043e92d: exit_reason budget_exceeded, 80 steps, grader log-size
  far over threshold.
- monitor backgrounded alone in one call; deploy in a separate call — never
  co-run; every attempt returns "No worker processes found. Exiting."
- identical stuck hypothesis repeated to finish_reason length (many truncation
  markers plus passive continue nudges).
- Offline state-machine replay of the actual command sequence fires the hint
  exactly after the separate-call deploy, the point where the live trajectory
  began looping to death.

### Uncertainty

The hint must land at the right boundary (verified offline) and the model must
then act on it (combine into one command). The heuristic is intentionally
general to catch the wider daemon/service class; if too broad or too narrow,
tune the regex or max_fires next round. Single focus task shows the exact
mechanism, but the execution-model gap it fixes is generic — watch R3
attribution to confirm it generalises beyond the one task.
