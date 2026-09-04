# Tmax co-evolution journal — tmax-coev-rep22-i4

## Round 1 — deterministic requests install for service tasks

<!-- journal:frontmatter
round: 1
timestamp: 2026-09-01T22:00:00Z
hypothesis_id: h_http_verifier_dep_install_v1
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000297_01ba10b6, task_001857_24daeef3]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=10/50; +6/-6 gained=task_000109_09ddd96b,task_000297_01ba10b6,task_000328_80fb4c9f,task_001031_a8f0eb37 lost=task_000396_e56917e2,task_001098_f5acdd79,task_001321_658ce4a8,task_001447_8bde38ef; score 0.2000 >= incumbent(mean) 0.2000 - tol 0.0400
expected_global_gain: "Flips up to 3 correct-but-ungraded HTTP/network-service tasks (6% of the 50-task round) failing solely on ModuleNotFoundError: requests at verifier collection; generalizes to any requests-graded service task."
regression_risk: "Extra Bash install call fires only on detected service tasks; idempotent no-op if requests present. Broadened detection could false-positive on a background binary that isn't a service — worst case one wasted pip install, task stays correct."
cost_shift: "+1 Bash tool call + one short pip install per detected service task at exit; negligible tokens, removes 3 hard zeros. Net positive."
rollback_trigger: "Revert to R0 reminder-only HttpVerifierDepProcessor if net pass_rate regresses, or if replay/REPLAY_FAIL shows the injected Bash call crashes or pip install requests is blocked offline."
-->

### Why

Assigned focus `task_000028_7fe033ac` built a fully-correct service (nginx
reverse proxy → C++ backend on `/tmp/video_backend.sock` returning the ffprobe
frame count 150; agent verified `HTTP/1.1 200 OK` body `150`). It still scored
0: the external verifier's `test_final_state.py` does `import requests`, the
container lacks it, and pytest fails at *collection* time
(`ModuleNotFoundError: No module named 'requests'`). The agent can never see
this — verifier files are injected only after it exits. This is a harness
deficiency. The same root cause recurs on `task_001857` (bare
`/home/user/diagnostic_server &`) and `task_000297` (python http.server on
`127.0.0.1:8080`), all three reward 0 with `initial_pytest.passed=true`.

The existing `HttpVerifierDepProcessor` (reminder-only) failed here two ways:
(1) it inspects only the Bash *command string*, so service starts whose port
lives in a config file (`nginx -c`) or in a bare compiled binary carry no
matchable token — no reminder fired on 000028/001857; (2) on 000297 a
reminder-style nudge was insufficient — the agent ran `python3 -c 'import
requests'`, saw `ModuleNotFoundError`, and finished anyway.

### Changes

- `processors/http_verifier_dep_install.py` — new
  `HttpVerifierDepInstallProcessor`. Broadens service detection (framework/port
  signal OR generic daemon / background-executable launch shape) and, on exit
  intent for a detected service task, injects ONE real `Bash` tool call:
  `python3 -c 'import requests' || python3 -m pip install --quiet requests`.
  Deterministic (does not rely on model compliance), fires at most once per task,
  no task-specific constants. Uses the proven `CustomSelfVerifyProcessor`
  synthetic-tool-call injection pattern.
- `config.yaml` — replaced the R0 `HttpVerifierDepProcessor` reference with the
  new processor (kept `_order=91`, after CustomSelfVerifyProcessor 90).

### Evidence

- `task_000028_7fe033ac.result.json`: `final_pytest.output_tail` =
  `ModuleNotFoundError: No module named 'requests'` at `test_final_state.py`
  collection; `initial_pytest.passed=true`; endpoint returns `200 OK` body `150`.
  messages.json: service started via `nohup /app/server ... &` and `nginx -c
  /app/nginx.conf`; grep `HttpVerifierReminder` = 0 (old regex never matched).
- `task_001857_24daeef3`: only service-launch command is
  `/home/user/diagnostic_server 2>&1 &` (bare binary); `HttpVerifierReminder` = 0.
- `task_000297_01ba10b6` idx 7-8: `python3 -c 'import requests'` →
  `ModuleNotFoundError` (exit 1); idx 29 declared success without installing.
- Feasibility: tasks 000010/000140/000378/001090/001937/002063 ran
  `python3 -m pip install requests` and got `requests OK 2.34.2` — pip works in
  these containers, so the injected install is actionable.

### Uncertainty

Main risk is a false-positive service detection triggering a harmless extra pip
install, or the injected Bash call landing awkwardly relative to the
self-verify keepalive (mitigated by ordering after it and firing on a *later*
exit intent). If containers ever block outbound pip (offline), the install is a
no-op and the task stays exactly as it was — no worse than R0. Watch the R1→R2
gating attribution for the three predicted tasks and any T→F regressions on
other service tasks.

## Round 2 — no-op: focus task is an infra docker-conflict, not a harness gap

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-02T00:00:00Z
hypothesis_id: h_noop_docker_conflict_infra_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — explicit no-op. Config copied byte-for-byte from R1. The assigned focus failure is not addressable by any HarnessConfig surface."
regression_risk: "None — byte-identical config, no authored files."
cost_shift: "Zero — no change to the harness."
rollback_trigger: "N/A (no change to roll back). Human infra fix in recipe/tmax_eval/docker_env.py is the real remedy; see NEEDS_FROM_HUMAN.md."
-->

### Why

Assigned focus `task_000028_7fe033ac` failed this round for a reason entirely
outside the meta-agent's editable surface. Its `result.json` shows
`status=error`, `reward=0`, `elapsed_s=0.1`, and a `RuntimeError: docker run
failed ... Conflict. The container name "/tmax-task0000287fe033ac-1788298078"
is already in use`. There is no `.messages.json` and the task dir contains only
`result.json` — **the agent never launched**. The R1 diagnosis
(`HttpVerifierDepProcessor` / `requests` at verifier-collection time) is
superseded for this trajectory set: the task never reached the verifier because
its container failed to start.

This is not isolated: **9 of 50 tasks** in this trajectory set failed with the
identical container-name `Conflict` at `docker run` time
(`task_000015, 000028, 000140, 000264, 000396, 000505, 000748, 001032,
001090`), all `status=error`, `elapsed_s≈0.1`, reward 0 — ~18% of the round
silently zeroed by infrastructure.

Root cause is in the **read-only** eval runner
`recipe/tmax_eval/docker_env.py::start_container`: the container name is
`f"tmax-{task_id[:20]}-{int(time.time())}"` (1-second resolution, no unique
suffix, no pre-run `docker rm -f`, no retry on Conflict). Two tasks starting in
the same second — or a leftover container from a prior run — collide. A
`MultiHookProcessor` runs inside the agent loop, which only exists *after* the
container starts, so no harness edit can intercept a container that never
launched. There is no HarnessConfig knob for container naming/retry.

### Changes

- `config.yaml` — byte-for-byte copy of R1 config (explicit no-op; verified
  via `cmp`, canonicalize `{"ok": true}`).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — documents the docker-conflict root
  cause and suggests the fix (unique suffix / pre-run rm / retry-on-conflict)
  in the read-only recipe layer.

### Evidence

- `task_000028_7fe033ac.result.json`: `status: "error"`, `elapsed_s: 0.1`,
  `error: "docker run failed ... Conflict. The container name ... is already
  in use"`, traceback at `recipe/tmax_eval/docker_env.py`, line 124.
- 9/50 tasks share the identical `Conflict` error (enumerated above); all
  `elapsed_s≈0.1`, no messages.json.

### Uncertainty

Making an off-focus processor/tool change here would violate SOUL.md
(no task-specific injection) and the brief's anti-drift rule, and could only
regress. The only real fix is the human infra change flagged in
NEEDS_FROM_HUMAN.md. If a future trajectory set for this task actually starts
the container and reaches the verifier, the R1 HttpVerifierDep hypothesis
becomes testable again.

## Round 2 — infra container-collision, no harness lever (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-02T00:00:00Z
hypothesis_id: h_no_op_infra_docker_name_collision_v1
levers: []
predicted_affected: []
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "None — assigned focus is an eval-runner infra bug, not a harness deficiency; no config lever applies. Explicit no-op preserves R1 state."
regression_risk: "Zero — config + sibling system_prompt.txt copied byte-for-byte from R1 (md5 d1a7e90e / 914fb4a8). Canonicalize ok."
cost_shift: "None."
rollback_trigger: "N/A (no change)."
-->

### Why

Assigned focus `task_000015_89886d8d` has `status: error`, `elapsed_s: 0.1`,
`reward: 0` — the agent container never started. Error is a Docker
container-name **Conflict** raised by `recipe/tmax_eval/docker_env.py::start_container`
before the harness run loop engages. The name is
`tmax-<task_id[:20]>-<int(time.time())>` — 1-second resolution + truncated
task_id, so a stale/parallel container with the same name blocks `docker run`,
and the runner does not `docker rm -f` the pre-existing name first. This is
identical across ALL 9 errored tasks in this trajectory set — same timestamp
`1788298078`, all `elapsed_s: 0.1`: task_000015, 000028, 000140, 000264,
000396, 000505, 000748, 001032, 001090.

This is categorically outside the HarnessConfig surface: the failure precedes
agent startup, so no processor/tool/template can intercept it; and
`recipe/**` (the eval runner) is read-only. Per tb2-playbook, config.yaml
controls only the processor pipeline + system prompt — zero levers over Docker
lifecycle. The brief's "unsupported focus" path applies: make the smallest
defensible edit (none) rather than drift onto another proposal's territory.

### Changes

None. `config.yaml` and sibling `system_prompt.txt` copied byte-for-byte from
`current_config` (R1). Explicit no-op.

### Evidence

- `task_000015_89886d8d.result.json`: status=error, elapsed_s=0.1, error =
  docker Conflict on container name `/tmax-task00001589886d8d-1788298078`.
- 8 sibling tasks show the identical Conflict at the same `-1788298078`
  timestamp with elapsed_s=0.1.
- `docker_env.py:105`: `name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]`; no pre-run `docker rm -f`, no retry on Conflict.
- Canonicalize on copied config: `{"ok": true, "checked_templates": 0}`.

### Needs from human

Wrote `_meta_scratch/NEEDS_FROM_HUMAN.md`: fix `start_container` to be
collision-proof (uuid suffix instead of second-resolution timestamp, and/or
best-effort `docker rm -f <name>` before run, and/or retry once on daemon
Conflict). Until fixed these 9 tasks stay false zeros and contaminate gating
attribution (they also pollute R1's predicted service tasks 000028/000140/
000505/001090).

### Uncertainty

Low. The root cause is unambiguous in the result.json errors and the runner
source. The only open question is why the collision occurred (likely stale
containers from a prior aborted batch or overlapping parallel dispatch within
the same wall-clock second) — either way it is an infra/orchestration concern,
not a harness one.

## Round 2 — broken verifier (self-crashing reference calc), no harness lever (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-02T00:00:00Z
hypothesis_id: h_no_op_verifier_reference_crash_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — assigned focus is a broken external verifier whose own reference computation raises ValueError before comparing the agent's output; unwinnable by any HarnessConfig surface. Explicit no-op preserves R1 state."
regression_risk: "Zero — config.yaml copied byte-for-byte from R1 (sha256 457fad09…c699d9). Canonicalize ok, no authored files."
cost_shift: "None."
rollback_trigger: "N/A (no change to roll back). Real remedy is a task-authoring fix to test_final_state.py column offsets / parsing leniency; outside meta-agent scope."
-->

### Why

Assigned focus `task_000117_1b598e44` (scientific_computing) fails with
`reward=0` despite the agent solving the task correctly. The prompt asks it to
fix a non-deterministic OpenMP float reduction in `compute_centroid.cpp`,
recompile, run, and write the HDF5 centroid to `final_centroid.txt`. The agent
did exactly that: replaced the atomic reduction with per-thread partial sums
combined in fixed thread-id order, verified byte-identical output across 10
runs (`0.191334 44.973614 44.974155`), and even *noticed* the PDB column
misalignment at its own self-verify step but correctly reasoned it must not
alter the reference C++ parsing logic.

The failure is entirely on the verifier side. `test_final_state.py`'s
`get_expected_centroid()` recomputes the centroid with **strict Python**
`float(line[30:38])`, `float(line[38:46])`, `float(line[46:54])`. The C++
program uses **lenient** `std::stof(line.substr(...))`, which stops at the first
non-numeric char. `complex.pdb`'s coordinate columns are shifted one char left
of the standard PDB fixed-width positions the verifier assumes, so on the very
FIRST ATOM line `line[38:46]` = `'5 -94.99'` and Python `float()` raises
`ValueError` — the verifier crashes inside its own reference computation,
BEFORE any comparison with the agent's `final_centroid.txt`. The crash is
unconditional: no value the agent writes can change it.

This is a task-authoring / verifier bug (strict-vs-lenient float parse mismatch
+ wrong column offsets for this file), not a harness deficiency and not a model
capability gap. The verifier files are injected only after the agent exits
(tb2-playbook sandbox topology), so no processor/tool/template can patch a
crashing `/tmp/test_final_state.py`. No HarnessConfig lever applies. Injecting a
"rewrite the input file to appease the grader" strategy would be a task-specific
hack that is Pareto-negative for the other 49 tasks. Per the brief's
"unsupported focus" path: make the smallest defensible edit (none).

### Changes

None. `config.yaml` copied byte-for-byte from `current_config` (R1). Explicit
no-op.

### Evidence

- `task_000117_1b598e44.result.json`: `initial_pytest.passed=true`,
  `reward=0`; `final_pytest.output_tail` shows the failure is inside
  `get_expected_centroid()` at `x_sum += float(line[30:38]) / y_sum +=
  float(line[38:46])` → `ValueError: could not convert string to float:
  '5 -94.99'`, i.e. the verifier's own reference calc, not the agent output.
- messages idx 40: agent's deterministic C++ produces
  `0.191334, 44.9736, 44.9742`; idx 76: identical across 10 reruns.
- messages idx 62/68: agent's independent Python re-derivation hit the SAME
  `ValueError: '5 -94.99'` at `float(line[38:46])`, confirming the file's
  columns don't match `[30:38]/[38:46]/[46:54]`.
- Local simulation: `float('   27.88')`=27.88 but `float('5 -94.99')` raises —
  crash occurs on line 1 (`ATOM 1`), a standard-format record, so it is
  input-independent of the agent.
- Only one other REF-CRASH-tagged task in the set (task_000635) is a distinct
  trailing-null-byte mismatch, not this class — so no generalizable cluster.
- Canonicalize on copied config: `{"ok": true, "checked_templates": 0}`;
  sha256 identical to R1 config.

### Uncertainty

Very low. The verifier crash is deterministic and reproduced locally. The only
remedy is a task-authoring fix (align the verifier's column offsets to this PDB,
or parse leniently like the C++ does) — outside the meta-agent's editable
surface. No harness change could flip this task.

## Round 2 — no-op: task_000111 is a capability/reference gap, not harness

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-02T00:00:00Z
hypothesis_id: h_noop_scicomp_reference_mismatch_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None claimed. Explicit no-op: assigned focus task_000111 is not fixable by any harness mechanism; the existing SelfVerifyCrossCheckProcessor already fired and worked correctly on it. Shipping nothing protects the already-passing clusters from regression."
regression_risk: "None — config is byte-identical to R1."
cost_shift: "Zero — no config change."
rollback_trigger: "N/A (no change)."
-->

### Why

Assigned focus `task_000111_cbada64a` (scientific_computing). The task asks for
a C++ program that does OLS linear regression on `/home/user/noisy_data.csv`
(100 x,y rows) plus a seeded-mt19937 bootstrap CI, writing `m,c,ci_lower,ci_upper`
to `/home/user/result.txt`. The agent wrote a textbook-correct OLS
(`m = (n*Sxy - Sx*Sy)/(n*Sx2 - Sx*Sx)`), compiled with `g++ -O3`, ran it, and
emitted `2.5056,1.2262,3.9742,6.2925`. The verifier expects `m ≈ 2.5997`
(`AssertionError: Expected m to be approx 2.5997, got 2.5056`, tol 0.001).

Critically, this is NOT the usual "confident-exit-on-wrong-number" shape that a
self-verify checklist can catch. The `SelfVerifyCrossCheckProcessor` (added a
prior meta-round) DID fire on this task (msg idx 90-96 `_tb2_self_verify`), and
the agent DID follow it: it independently re-derived the slope in Python and got
`m=2.5056` — matching its C++ output exactly. It then re-derived the bootstrap CI
in Python, found a discrepancy (`3.9894` vs C++ `3.9742`), and CORRECTLY
attributed it to Python's `random` vs C++ `std::mt19937` being different RNGs
(a legitimate, non-bug explanation). So the harness's re-derivation +
contradiction-sweep discipline executed exactly as designed.

The residual error is the slope itself: two self-consistent, correct
implementations of the *specified* OLS both give 2.5056, yet the reference wants
2.5997 (a ~0.094 gap, far larger than float noise). That value is not derivable
from the described algorithm on the data the agent sees — it points to a
reference/data-generation mismatch or an under-specified detail in the task, not
a harness deficiency. No harness mechanism could bridge this without embedding
the literal answer "2.5997", which is forbidden task-specific injection and would
not generalize.

Same shape recurs across the scientific_computing cluster
(`task_001048_14335141`: got 165.7908 vs expected 161.8028;
`task_001937_ac874115`: got grid 60 vs expected 50) — subtle spec-interpretation
/ reference-value gaps that are model capability limits, not recoverable harness
states. `task_000111`: requires exact reference-slope reproduction the specified
OLS does not yield on the visible data — no harness fix — skip.

### Changes

- `config.yaml` — byte-identical copy of R1 config (explicit no-op). Verified
  via `diff -q` (IDENTICAL) and `canonicalize` (`{"ok": true}`).

### Evidence

- `task_000111_cbada64a.result.json`: `final_pytest.output_tail` =
  `AssertionError: Expected m to be approx 2.5997, got 2.5056` at
  `test_final_state.py:32`; `initial_pytest.passed=true`; `exit_reason=done`,
  `finished=no_tool_calls`, 13 steps.
- `task_000111` messages.json idx 142: agent's Python OLS re-derivation prints
  `OLS: m=2.5056, c=1.2262` — matches its own C++ output, confirming the slope is
  the correct OLS for the file it read.
- `task_000111` messages.json idx 162 vs 182: Python bootstrap CI `3.9894/6.2904`
  vs C++ `3.9742/6.2925`; agent correctly attributes to mt19937-vs-Python-random
  RNG divergence (idx 188) — the cross-check discipline fired and reasoned
  correctly.
- Cluster corroboration: `task_001048_14335141` (`isclose(165.7908,161.8028)` →
  False) and `task_001937_ac874115` (`60 == 50` → False) — same
  self-consistent-but-reference-mismatched numeric shape.

### Uncertainty

If a later round obtains the full `noisy_data.csv` for task_000111 and finds a
concrete, generalizable interpretation (e.g. reference uses regression-through-
origin, a header-skip convention, or a specific numpy/scipy fit variant that
systematically shifts the slope across many tasks), that would justify a
strategy-level system-prompt nudge. Absent that shared, generalizable cause,
strengthening the self-verify checklist further would risk regressing passing
tasks (agents distrusting correct answers, burning steps) — so the defensible
action this round is a no-op.

## Round 2 — stdlib-shadow circular-import guard

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-02T00:00:00Z
hypothesis_id: h_stdlib_shadow_guard_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes an agent-invisible, grader-fatal failure class: a task-required Python file whose basename equals a stdlib module (operator.py, types.py, queue.py, ...) sitting on sys.path poisons every python3 start-up in that dir — including the external verifier's pytest/runpy — so a fully-correct solution scores 0. Guard teaches the escape the instant the circular-import signature appears."
regression_risk: "Very low. Fires only when tool output carries the circular-import / 'partially initialized module' / 'Could not import runpy' signature AND a non-site-packages traceback frame whose basename is a real stdlib module. Appends to a tool result once per task; never blocks a call, never edits the system prompt. Site-packages/dist-packages frames excluded, so ordinary third-party circular imports don't trigger. Verified it does NOT match plain ModuleNotFoundError (the requests case handled elsewhere)."
cost_shift: "Negligible: ~250-token guidance block at most once, only on tasks that hit the signature; zero cost on the ~49 other tasks."
rollback_trigger: "Revert if net pass_rate regresses, if replay/REPLAY_FAIL shows the processor crashes on real events, or if the guard fires on tasks with no stdlib-shadow issue (check for spurious [StdlibShadowGuard] injections in R2->R3 trajectories)."
-->

### Why

Assigned focus `task_000010_644ab1c2` (system_administration) required a Python
script at exactly `/home/user/operator.py`. The agent wrote it correctly
(backup created, socat port-forward, pexpect-driven CLI applied both manifests,
api_success.log populated — all verified in the trajectory). It still scored 0:
`operator.py` shadows Python's stdlib `operator` module, and because
`/home/user` is on `sys.path`, Python's interpreter start-up
(`collections/__init__.py` -> `from operator import eq`) resolves to the
agent's file -> circular import. This crashes EVERY `python3` invocation in
that directory, including the external verifier's pytest/runpy
(`Could not import runpy module` / `partially initialized module 'collections'
... circular import`). The agent literally observed the same traceback mid-run
(its own `python3 -c 'import pexpect'` broke), diagnosed it as a naming
conflict, but had no escape because the task mandates the filename — so it
restored `/home/user/operator.py` at the end and re-poisoned the interpreter
for the grader. Harness deficiency: the failure surfaces only in a tool result
and is fatal only in the post-exit verifier phase the agent can't see.

### Changes

- `processors/stdlib_shadow_guard.py` — new `StdlibShadowGuard`
  (`MultiHookProcessor`, `_order=25`, singleton). `on_after_tool` detects the
  structural circular-import signature in `Bash` output, confirms a
  user-authored (non-site-packages) traceback frame whose basename is a real
  stdlib module (`sys.stdlib_module_names` + fallback set), and appends ONE
  recovery-strategy block naming the offending file/module: make it a
  transparent shim that re-exports the real stdlib module, guard all top-level
  side effects under `if __name__ == '__main__':`, and verify a bare
  `python3 -c 'import <mod>'` from the grader's likely CWD succeeds. Fires ≤1×
  per task; no task-specific constants.
- `config.yaml` — copied R1 byte-for-byte and appended the new processor after
  the HTTP-verifier hooks.

### Evidence

- `task_000010_644ab1c2.result.json`: `final_pytest.output_tail` =
  `Could not import runpy module` + `ImportError: cannot import name
  'namedtuple' from partially initialized module 'collections' (most likely due
  to a circular import)` with frame `File "/home/user/operator.py", line 10`.
  `reward=0`; task effects (backup, api log) actually correct.
- messages.json idx 16: agent's own `import pexpect` returns the identical
  circular-import traceback ending at `/home/user/operator.py`; idx 17 correct
  diagnosis; idx 39-40 renames back to `operator.py` and exits.
- Unit-tested the detector: fires on the real traceback -> ('operator',
  '/home/user/operator.py'); returns None on a site-packages-only circular
  import; returns NO_SIG on plain `ModuleNotFoundError` (so it never fights the
  requests-install processor).

### Uncertainty

Single-instance in this round's failing set, but the class (stdlib-named
required file) is general and the fix is dormant on all non-matching tasks, so
regression surface is minimal. Main residual risk is that even with the
guidance the model may not implement a correct transparent shim — that would be
a remaining capability gap, not a harness one. Watch R2->R3 attribution for
task_000010 and for any spurious `[StdlibShadowGuard]` injections on unrelated
tasks.
