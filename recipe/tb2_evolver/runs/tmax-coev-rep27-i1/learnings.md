# Evolve Journal — tmax-coev-rep27-i1

## Round 1 — verifier requests dep

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_verifier_requests_dep_v1
levers: [instruction]
predicted_affected: [task_000028_7fe033ac, task_000958_4bb2b05d]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=43/50; +3/-1 gained=task_000028_7fe033ac,task_000958_4bb2b05d,task_001818_b251e5ea lost=task_000396_e56917e2; score 0.8600 >= incumbent(mean) 0.8200 - tol 0.0400
expected_global_gain: "Flips the 2-task cross-domain cluster whose verifier dies at `import requests`; generalizes to any future service task using that verifier convention"
regression_risk: "Low — additive checklist item on the once-per-task self-verify turn; identical singleton group/hooks to the processor it replaces, so no double-injection; `|| pip install` is a no-op when requests present or no service exists"
cost_shift: "Negligible — ~90 extra tokens in the single self-verify turn; at most one extra Bash call on service tasks"
rollback_trigger: "If task_000028/task_000958 stay F AND any previously-passing service task (e.g. task_000344, task_000015) flips to F, revert"
-->

### Why

R0 scored 41/50 (0.82). Of the 9 failures, only one root cause recurred
across ≥2 distinct tasks with the same mechanism: the verifier's test module
does `import requests` at collection time, and the agent — which never sees
the verifier file — never installs `requests`. Both affected tasks build a
localhost network service (task_000028: Nginx + C++ backend on :8080;
task_000958: C++ HTTP microservice on :9090), test them with `curl`/`ss`, and
exit reporting success. The verifier then dies with
`ModuleNotFoundError: No module named 'requests'`, scoring 0 regardless of
whether the service is correct. For task_000958 this was a hard collection
error blocking ALL assertions. The other 7 failures are one-off,
distinct-mechanism capability/logic gaps (process cleanup, log-rotation size,
SQLite index usage, numeric error, packaging, text-casing) plus a task-design
collision (task_000010's mandated `operator.py` shadows the stdlib module and
breaks the verifier's Python) — none harness-fixable without embedding domain
knowledge, so logged as skips.

### Changes

- `processors/service_verify.py` — new `ServiceVerifyDepsProcessor`: drop-in
  replacement for `CustomSelfVerifyProcessor` (same one-shot keepalive
  mechanism, same `tb2_self_verify` singleton group and `_order=90`) with one
  added checklist item — for network-service tasks, ensure Python `requests`
  is importable (`python3 -c "import requests" || pip install requests`)
  because the automated checker probes services from Python.
- `config.yaml` — replaced the `CustomSelfVerifyProcessor` pipeline entry with
  a `file://` reference to the new processor.

### Evidence

- `task_000028_7fe033ac` final_pytest: `test_final_state.py:6: in <module>
  import requests → ModuleNotFoundError: No module named 'requests'`. Body:
  agent tested via `curl` ("curl: command not found"), never installed requests.
- `task_000958_4bb2b05d` final_pytest: `test_final_state.py:4: in <module>
  import requests → ModuleNotFoundError`, `1 error in collection` (all tests
  blocked). Body: agent probed its own server with `curl`/`ss`, never installed
  requests.
- Feasibility: pip installs succeed at run time in this environment (numpy in
  task_000684, pillow in task_001652, biopython in task_001031) — internet is
  NOT blocked, so the `pip install requests` the checklist nudges toward works.

### Uncertainty

If the two service tasks have additional, independent failures beyond the
missing import (task_000028's Nginx/C++ integration may still be wrong), fixing
the import unblocks collection but may not flip the reward. task_000958 is the
higher-confidence flip (pure collection error). Watch the rollback trigger: any
regression on a currently-passing service task means the checklist swap
disturbed the self-verify flow and should be reverted.

## Round 2 — stray/zombie process cleanup

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_lingering_process_cleanup_v1
levers: [instruction]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-002]
gating_outcome: accepted
gating_attribution: score=44/50; +1/-0 gained=task_000396_e56917e2; score 0.8800 >= incumbent(mean) 0.8600 - tol 0.0400
expected_global_gain: "Flips the lingering-service-process failure in the system_administration cluster (2/5 failing) and hardens all passing service/daemon tasks against the same silent final-state trap; same class of structural verifier fact as the R1 requests fix that flipped both its predicted tasks"
regression_risk: "Low — additive checklist text on the once-per-task self-verify turn; mechanism (keepalive/singleton) unchanged from R1 which flipped both service tasks with no service regression; item is conditional on having launched a background process and explicitly warns against killing a service the task needs alive"
cost_shift: "Negligible — ~120 extra tokens in the single self-verify turn, and at most 2-3 extra Bash calls (ps|grep, pkill, re-check) on tasks that actually spawned background processes"
rollback_trigger: "If task_000140 stays F AND any previously-passing service/daemon task (e.g. 000028, 000958, 001090) flips to F — i.e. the cleanup nudge made an agent kill a service the verifier needed alive — revert to the R1 checklist"
-->

### Why

R1 scored 43/50 and both R1-predicted service tasks (000028, 000958) flipped to
pass. Of the 7 remaining failures, exactly one is a recurring, structural,
harness-fixable pattern: **lingering background processes at container final
state**. The TB2 verifier runs pytest against the final container state after
the agent exits; some verifiers assert "no lingering `<service>` processes" via
`pgrep -f <name>` (which matches `<defunct>` zombies too). Agents that build a
daemon develop by launching it repeatedly in their persistent Bash session
(`./svc &`), leaving orphaned/zombie children that fail the clean-state
assertion even when the deliverable scripts are correct. The other 6 failures
are one-off model capability/logic gaps (log-rotation size, SQLite index
semantics, numeric deviation, grid optimization, tarball packaging) plus the
task_000010 `operator.py` stdlib-shadow task-design collision — none
harness-fixable without embedding domain knowledge, so logged as skips.

### Changes

- `processors/service_verify.py` — extended R1's `ServiceVerifyDepsProcessor`
  with an additive checklist item #7: audit for and clear stray/zombie
  background processes the agent spawned during development. It explains that a
  `<defunct>` process cannot be signalled and is reaped only when its parent
  exits (so kill the parent PID for zombies), kill ordinary strays with
  `pkill -f`, then confirm the process list is empty — with a guard not to kill
  a service the task requires to stay alive. Same singleton group
  (`tb2_self_verify`), `_order=90`, and keepalive mechanism as R1 (pure text
  extension).
- `config.yaml` — repointed the `ServiceVerifyDepsProcessor` `file://` entry to
  the R2 copy of the processor.

### Evidence

- `task_000140_01c78b42` verifier: `test_no_lingering_service_processes` →
  `AssertionError: Lingering vm_service processes found: ['353','587','889','1114','1307']`.
  Agent's own `ps` output (msg 36): `[vm_service] <defunct>` (Z-state zombies)
  from repeated dev runs. Agent msg 37/39: "There are some zombie processes from
  previous runs. Let me clean those up" — then exited without doing so. The
  other 2 verifier tests passed and `vm_setup.log` was correct.

### Uncertainty

task_000140's zombies are children of the agent's own persistent shell; whether
the agent can actually reap them depends on identifying the right parent PID at
exit time. If the parent is the agent's session shell itself (which persists for
the verifier), killing it may not be possible from within — in that case the
nudge helps only for strays the agent can `pkill` directly, and the flip is
uncertain. Watch the rollback trigger: a regression on a keep-alive service task
means the cleanup nudge over-killed and should be reverted.

## Round 3 — orphan-process reap-in-shell mechanism

<!-- journal:frontmatter
round: 3
timestamp: 2026-06-03T00:00:00Z
hypothesis_id: h_orphan_process_reap_v2
levers: [control]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-003]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips task_000140 (the sole remaining recurring, structural, harness-fixable failure) by replacing R2's mechanically-wrong 'kill the parent PID' advice with the correct general mechanism (reap-in-same-shell via `wait` + don't-relaunch-last + end with a `pgrep` clean check); hardens the whole service/daemon cluster — system_administration is the weakest domain at 2/5 — against the reparent-to-init zombie trap on unseen tasks"
regression_risk: "Low — pure text refinement of the already-shipped one-shot self-verify Control item; same tb2_self_verify singleton group / _order=90 / keepalive mechanism as R1/R2 (no double-injection); item keeps the explicit 'do NOT kill a service the task needs alive' guard, protecting passing service tasks 000028/000958/001090"
cost_shift: "Negligible — ~120 extra tokens on the single self-verify turn; at most 2-3 extra Bash calls (pgrep, one wait/kill, re-check) on tasks that spawned background processes"
rollback_trigger: "If task_000140 stays F AND any previously-passing service/daemon task (000028, 000958, 001090) flips to F — i.e. the reap/ordering nudge disturbed a keep-alive service — revert to the R1 checklist"
retry_rationale: "R2's h_lingering_process_cleanup_v1 (accepted, not reverted) told the agent to 'kill the parent PID' of a zombie; new body evidence from task_000140 shows the parent is PID 1 (init) — unkillable — and that the agent re-ran its test pipeline as a last action, re-orphaning a fresh zombie. C-003 is a distinct mechanism (prevention: reap-in-same-shell + ordering, not post-hoc kill), a legitimate re-scope on new evidence, not a re-proposal of a reverted bet"
-->

### Why

R2 scored 44/50 (0.88). Of the 6 remaining failures, five are one-off model
capability/logic gaps or a task-design collision — none harness-fixable without
embedding domain knowledge: task_000264 (SQLite `USING COVERING INDEX` vs the
verifier's `USING INDEX` substring + CSV quoting), task_001937 (numeric answer
60 vs expected 50), task_000933 (tarball missing extracted binaries),
task_000118 (log-rotation daemon let dir peak at 209MB vs 45MB threshold),
task_000010 (the known `operator.py` stdlib-shadow collision that breaks the
verifier's own Python). The one recurring, structural, harness-relevant failure
is task_000140 — R2's predicted task, which STAYED F despite R2's cleanup item
firing. The body reveals R2's mechanism was mechanically wrong: the zombie's
`PPID=1` (init), so "kill the parent" is impossible, and the agent re-ran its
test pipeline as a final action, spawning a fresh orphan. The correct general
fix is prevention: reap background test processes in the *same* shell with
`wait`, and never make a fresh service launch one of the last actions.

### Changes

- `processors/service_verify.py` — R3 rewrite of the R2 zombie-hygiene
  checklist item #7. Replaces "identify the parent PID and terminate that
  parent" (impossible when PPID=1) with the correct general mechanism:
  (a) reap background test processes in the launching shell via
  `kill $PID; wait $PID`; (b) run any end-to-end pipeline EARLY and make the
  final action a `pgrep -f <name>` clean-state check, never a fresh launch;
  (c) explains that zombies reparented to init cannot be reaped, so prevention
  is the only path. Same singleton group `tb2_self_verify`, `_order=90`,
  keepalive mechanism as R1/R2 (drop-in text refinement). Kept the R1
  `requests`-import item and the guard against killing a required service.
- `config.yaml` — repointed the `ServiceVerifyDepsProcessor` `file://` entry to
  the R3 copy of the processor.

### Evidence

- `task_000140_01c78b42` final_pytest: `test_no_lingering_service_processes` →
  `AssertionError: Lingering vm_service processes found: ['362','643','956','1219','1420']`.
- Body msg 256 `ps aux`: `root 362 ... Z ... [vm_service] <defunct>` (zombie).
- Body msg 324 `ps -ef`: `root 362 1 0 ... [vm_service] <defunct>` — **PPID=1**,
  so R2's "kill the parent" is impossible.
- Body msg 336-354: agent ran `kill -9 362` / `pkill -9 vm_service` (no-ops on a
  zombie), then re-ran `test_pipeline.sh` (msg 458) → fresh vm_service PID 956;
  msg 514 shows 362, 643, 956 all `<defunct>`. Cleanup undone by final relaunch.
- Root cause: `./vm_service &` inside `start_service.sh` (a subshell that exits)
  → child reparented to init → SIGTERM leaves a `<defunct>` that
  `pgrep -f vm_service` matches.

### Uncertainty

The self-verify item can only nudge; whether the model actually restructures its
teardown (reap-in-shell + no-final-relaunch) rather than repeating the
kill-a-zombie no-op is uncertain — the flip depends on the model following the
refined mechanism. If task_000140 stays F with no service regression, the
lingering-zombie class is likely beyond a checklist nudge (would need the agent
to fundamentally not orphan the process) and should be logged as a
model-capability skip. Watch the rollback trigger for over-killing a keep-alive
service.
