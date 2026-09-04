# Tmax coev-rep24-i2 — evolve journal

## Round 1 — ensure grader requests dep

<!-- journal:frontmatter
round: 1
timestamp: 2026-09-03T07:10:00Z
hypothesis_id: h_grader_client_deps_v1
levers: [control]
predicted_affected: [task_000106_23215092, task_000809_760d7fa0, task_000958_4bb2b05d, task_001857_24daeef3, task_000939_1592be48, task_002063_8c8adcfe, task_000028_7fe033ac]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=22/50; +4/-1 gained=task_000028_7fe033ac,task_000106_23215092,task_000510_b49b430c,task_000809_760d7fa0 lost=task_001832_dd672877; score 0.4400 >= incumbent(mean) 0.4400 - tol 0.0400
expected_global_gain: "Flips up to 7 HTTP-service tasks whose verifier fails at collection on `import requests`"
regression_risk: "Very low — guarded one-shot idempotent `pip install requests`; only fires after a service signature is seen; cannot corrupt task outputs"
cost_shift: "+1 short Bash step (~seconds) only on service tasks; zero elsewhere"
rollback_trigger: "If any predicted task regresses or replay/synthetic errors on the injected install step, or if pass_rate is flat while cost rises, revert"
-->

### Why

Assigned focus `task_000106_23215092`: the agent built a fully correct
Flask co-authorship API (correct SQL, PageRank, exact JSON shape, 404
handling — all verified via `urllib` in-trajectory) yet scored 0. The
verifier's `test_final_state.py` does `import requests`, which is absent
from the image, so pytest errors at *collection* and the correct service is
never exercised. TB2 injects verifier tests after the agent exits, so the
agent has no signal the grader needs `requests`; it (and every agent in this
cluster) probes with `urllib`/`curl`, which are preinstalled. This same
`ModuleNotFoundError: No module named 'requests'` at collection appears on 7
of 31 failing tasks — all HTTP-service tasks. It is a harness deficiency,
not a capability gap: pip works during the agent phase (numpy/scipy were
installed successfully in the same trajectory).

### Changes

- `processors/grader_client_deps.py` — new `GraderClientDepProcessor`
  (`MultiHookProcessor`). Arms when a Bash command matches a generic
  HTTP-service signature (flask/fastapi/uvicorn/http.server/port binds/
  `127.0.0.1:<port>`/`--port`/…); on exit-intent (`on_after_model`, no tool
  calls) injects one idempotent, offline-safe `pip install requests` Bash
  call. Fires at most once per task. No task ids or dataset literals.
- `config.yaml` — register the processor (absolute `file://` path) at
  `_order=88`, immediately before `CustomSelfVerifyProcessor`.
- `system_prompt.txt` — copied byte-identical to R0 default (sibling builder
  requirement); no prompt change.

### Evidence

- `task_000106_23215092` result.json: `final_pytest.output_tail` =
  `ModuleNotFoundError: No module named 'requests'` … `Interrupted: 1 error
  during collection`; messages steps 21-46 show a working `/author/<id>`
  service returning the required JSON; agent never installs `requests`.
- `task_000809_760d7fa0`, `task_000958_4bb2b05d`, `task_001857_24daeef3`,
  `task_000939_1592be48`, `task_002063_8c8adcfe`, `task_000028_7fe033ac`:
  each result.json final_pytest tail = same `No module named 'requests'`;
  each user prompt asks for an HTTP service/microservice; grep over messages
  confirms none installs `requests` (all use curl/urllib).

### Uncertainty

If the underlying service is *also* wrong on some of the 7, the install is a
harmless no-op and those stay failing (removes the collection blocker only).
If a task's environment truly has no pip/network, the install no-ops and the
task stays as-is. Watch the injected step in replay: it must not error the
run loop. Rollback if any predicted task regresses or the install step
crashes.

## Round 2 — infra container-name collision (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-03T08:00:00Z
hypothesis_id: h_docker_name_collision_infra_v1
levers: []
predicted_affected: []
cited_candidates: [C-118NOOP]
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — assigned failure is not harness-evolvable; config kept byte-identical to protect the R1 accepted grader-deps cluster"
regression_risk: "None — byte-for-byte no-op copy of R1 config + sibling system_prompt.txt"
cost_shift: "Zero — no pipeline change"
rollback_trigger: "N/A (no-op)"
-->

### Why

Assigned focus `task_000118_3043e92d` failed with `status:error`,
`elapsed_s:0.1`, and **no messages.json** — the run loop never started.
Root cause is a Docker container-name collision raised in
`recipe/tmax_eval/docker_env.py::start_container` (line 124): the name is
`f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"`, whose only
uniqueness suffix is 1-second-granular wall-clock time. In a `rep24`
sweep, two runs of the same task starting in the same second collide →
Docker `Conflict` → hard error before any agent step.

This is pure orchestration infrastructure, exercised *before* any
HarnessConfig surface (processors / tools / system prompt). No lever on
the evolvable config surface can intercept a `docker run` name conflict.
The fix belongs in `docker_env.py` (read-only `recipe/**`), so I logged
it in `_meta_scratch/NEEDS_FROM_HUMAN.md` and made no config change.

### Changes

- `config.yaml` — copied byte-for-byte from R1 (explicit no-op).
- `system_prompt.txt` — copied byte-for-byte from R1 (sibling required
  by `SiblingSystemPromptBuilder`).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — infra bug + suggested fix.

### Evidence

- `task_000118_3043e92d.result.json`: `"status":"error"`,
  `"elapsed_s":0.1`, error = `docker: ... Conflict. The container name
  "/tmax-task0001183043e92d-1788418459" is already in use ...`;
  traceback ends at `docker_env.py:124 start_container`.
- Directory `task_000118_3043e92d/` contains only `result.json` — no
  `messages.json`, confirming the run loop never began.

### Uncertainty

None on diagnosis — the traceback names the exact line. Risk of the
no-op is zero; it preserves the R1 accepted grader-deps processor
cluster. If a human patches `docker_env.py`, the task should simply run
next sweep and be gradeable on its own merits.

## Round 2 — infra docker-name conflict (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-03T08:00:00Z
hypothesis_id: h_docker_name_conflict_infra_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None from harness surface — assigned failure is an infra bug outside HarnessConfig reach"
regression_risk: "None — config is a byte-for-byte copy of R1"
cost_shift: "None"
rollback_trigger: "N/A (no-op)"
-->

### Why

Assigned focus `task_000015_89886d8d` has `status=error`, `reward=0`,
`elapsed_s=0.1`, `agent=null`, `final_pytest=null` — it never entered the
agent run loop. The crash is at `recipe/tmax_eval/run_eval.py:150` →
`docker_env.start_container(...)`, which runs *before* any processor / tool /
system-prompt surface exists. Error string:
`docker run failed ... Conflict. The container name
"/tmax-task00001589886d8d-1788418459" is already in use`.

Root cause is in `recipe/tmax_eval/docker_env.py::start_container` (line 105):
`name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]`. The
`int(time.time())` gives only 1-second resolution and the task-id is truncated
to 20 chars, so concurrently-dispatched tasks (or orphaned containers from a
crashed prior run) collide on `docker run --name`. All 8/50 `status=error`
tasks in this trajectory set share the identical error string
(task_000010, _000015, _000118, _000140, _000505, _000933, _001781, _001937).

Per tb2-playbook, `config.yaml` controls the processor pipeline and system
prompt, **not the benchmark infrastructure**. No `MultiHookProcessor`, `@tool`,
or Jinja template runs early enough to intercept `docker run`. This is a model-
/infra-layer bug, not a harness deficiency the config can address. Drifting to
another proposal's cluster (e.g. the R1 HTTP-`requests` cluster) would break
batch diversity, so I make the smallest defensible edit: none.

### Changes

- `config.yaml` — byte-for-byte copy of R1 config (explicit no-op).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — documents the infra bug and the
  one-line fix (UUID-based container name + best-effort `docker rm -f` reap in
  `docker_env.start_container`), which is outside this agent's write scope.

### Evidence

- `task_000015_89886d8d` result.json: `status=error`, `elapsed_s=0.1`,
  `agent=null`, `final_pytest=null`, error = `docker run failed ... Conflict.
  The container name "...889886d8d-1788418459" is already in use`.
- 7 sibling error tasks with byte-identical error shape (listed above).
- `run_eval.py:150` shows `start_container` is called before the harness loop;
  `docker_env.py:105` shows the collision-prone name formula.

### Uncertainty

If the orchestrator retries these tasks with fresh container names they may
pass on their own (transient collision). The harness cannot influence this;
tracking is handled by the human-facing infra note.


## Round 2 — infra name-conflict, no harness fix

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-03T08:00:00Z
hypothesis_id: h_docker_name_conflict_infra_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — assigned focus is an infra failure outside the evolvable surface; explicit no-op protects the R1 pipeline"
regression_risk: "None — config is byte-identical to R1 (accepted round)"
cost_shift: "Zero — no pipeline change"
rollback_trigger: "N/A (no-op)"
-->

### Why

Assigned focus `task_000010_644ab1c2` failed with `status: error`,
`reward: 0`, `elapsed_s: 0.1` — the run loop never started. Error:
`docker run failed ... Conflict. The container name
"/tmax-task000010644ab1c2-1788418459" is already in use by container
"ee1e8516..."`. Seven sibling tasks failed identically
(task_000015_89886d8d, task_000118_3043e92d, task_000140_01c78b42,
task_000505_50b5162d, task_000933_1f27096a, task_001781_529727cf,
task_001937_ac874115) — all `elapsed_s ~0.1`, all the same Conflict.

Root cause is in read-only infra `recipe/tmax_eval/docker_env.py::start_container`:
the container name is keyed on `int(time.time())` (second resolution). When
run_eval.py's `--system-error-retries` loop re-submits a task within the same
wall-clock second (or a prior container was not `docker rm -f`'d), the
`docker run --name` collides. No per-attempt uniqueness (no PID/UUID/ns),
no pre-run removal.

This is NOT a harness-mechanism deficiency and NOT a model capability gap.
The evolvable surface (processors/tools/templates) runs INSIDE the run loop;
`start_container` runs BEFORE it, so no hook can fire (elapsed_s 0.1 confirms
the loop never began). `docker_env.py` and `run_eval.py` are outside the
meta-agent's writable scope. No config change can address this.

### Changes

- `config.yaml` — byte-for-byte copy of R1 (explicit no-op; canonicalizes,
  checked_templates=0).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — documents the infra race and a
  suggested fix (nanosecond/UUID/PID suffix or pre-run `docker rm -f`).

### Evidence

- `task_000010_644ab1c2.result.json`: `status: error`, `elapsed_s: 0.1`,
  error = `docker run failed ... Conflict. The container name ... is already
  in use`; no `.messages.json` (agent never ran).
- 8/8 error-status tasks in the trajectory dir share the identical
  `already in use` Conflict string (grep over all result.json).

### Uncertainty

If a future harness runner is granted a pre-run cleanup hook these tasks
become winnable, but that is a human infra edit, not a config change.
Shipping the no-op preserves the accepted R1 pipeline unchanged.

## Round 2 — recompute-and-reconcile discipline

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-03T07:34:51Z
hypothesis_id: h_recompute_reconcile_v1
levers: [instruction]
predicted_affected: [task_000011_d089ef35, task_000587_9862bb19]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the 'correct on trivial case, wrong on real case, self-detected-but-unreconciled discrepancy' failure shape on fully-specified-computation tasks (scientific_computing / data_science / debugging)"
regression_risk: "Additive prompt guidance; only bites on computation tasks. Minor extra Bash steps on some already-passing tasks; no path to corrupting outputs or changing tool/pipeline behavior"
cost_shift: "+1-3 short Bash steps on computation tasks (one independent hand-check + possibly one debug iteration); negligible elsewhere"
rollback_trigger: "If task_000011 / task_000587 stay failing AND any previously-passing computation task regresses, or if cost rises with flat pass_rate, revert the prompt addition"
-->

### Why

Assigned focus `task_000011_d089ef35`: a fully-specified C mesh-server MSE
task (all inputs, formula, reference vectors, output format given in-prompt).
Independently recomputing from the spec yields 8.25/17.25/68.25/93.25 — exactly
the grader's expectation. The agent's server returned 8.25/12.75/38.25/50.75:
right only on the trivial quadrant 0, wrong on 1/2/3 due to a bug in its C
extract/refine/MSE loop. This is not a math gap — the agent hand-summed
quadrant 1 to 276 → 17.25 (correct) but its server said 12.75, flagged the
contradiction twice ("Let me check if there's a bug…"), then exited declaring
success, trusting the buggy program over its own correct derivation. The
one-shot self-verify checklist fired but the agent satisfied it superficially,
re-testing only the passing quadrant 0. Same mechanism on
`task_000587_9862bb19` (C++ recommender debug): hand-derived Item 104 = 2.0 vs
program 2.2500, surfaced the mismatch at step 41, then declared complete
without reconciling. Root cause is a verification-discipline gap: agent treats
its running program as ground truth and exits on a discrepancy it already
noticed.

### Changes

- `system_prompt.txt` (sibling of config, read by SiblingSystemPromptBuilder)
  — appended a "Verification discipline" block: when a task fully specifies a
  computation, independently derive the expected result by hand for a
  non-trivial case and compare vs program output; if they disagree, the
  program is buggy — fix it before finishing; never exit on a known mismatch.
  General strategy, no task literals/constants.
- `config.yaml` — copied from R1 unchanged (processor pipeline identical,
  including the R1/c4 GraderClientDepProcessor). Only the sidecar prompt
  changed.

### Evidence

- task_000011_d089ef35 result.json: final_pytest expects 17.25/68.25/93.25 for
  quadrants 1/2/3, server returned 12.75/38.25/50.75; quadrant 0 (8.25) passes.
  messages step 17/23: "= 286/16 = 17.875 … But the server returned 12.75. Let
  me check if there's a bug…" (its 276-sum = correct 17.25); steps 33-37: after
  self-verify checklist, re-read task + re-tested only quadrant 0, then exited.
- task_000587_9862bb19 messages step 41: "Item 104 … = 8/4 = 2.0… wait, the
  output shows 2.2500. Let m[e]"; steps 43/45/49/51: "task complete" with no
  reconciliation of the mismatch.

### Uncertainty

The retroactive check is "plausible, not guaranteed": the rule forces the
agent to stop trusting the buggy program and debug, but flipping the task then
depends on the agent locating the C/C++ defect. Both agents already had the
correct target value in hand, so the decisive error (exit-on-known-mismatch) is
exactly what the rule targets. If neither predicted task flips and a
previously-passing computation task regresses, revert.

## Round 2 — numeric independent-recompute nudge

<!-- journal:frontmatter
round: 2
timestamp: 2026-09-03T07:38:03Z
hypothesis_id: h_numeric_crosscheck_v1
levers: [control]
predicted_affected: [task_000111_cbada64a, task_001330_f5aff1f5, task_000011_d089ef35]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Raises the scientific_computing failing cluster (>=3 tasks share 'clean-looking but slightly-wrong number, never re-derived') by prompting an independent recompute at the moment a numeric answer is produced"
regression_risk: "Very low — additive to one tool-result string, fires at most once, only on a numeric-run signature; non-numeric clusters never see it. Worst case: one extra confirming recompute step on an already-passing numeric task"
cost_shift: "+0 on non-numeric tasks; +1 short recompute Bash step (seconds, small tokens) on numeric tasks when the nudge is heeded, plus one appended paragraph"
rollback_trigger: "If any currently-passing scientific_computing task regresses, or replay/synthetic errors on the appended nudge, or numeric-cluster pass-rate flat while cost rises, revert"
-->

### Why

Assigned focus `task_000111_cbada64a`: agent wrote a textbook, deterministic
C++ OLS + seeded bootstrap program, ran it once, saw a well-formed
`m,c,ci_lower,ci_upper` in result.txt, and exited after only `ls`/`cat`/format
checks. Verifier expected slope m≈2.5997; got 2.5056 → reward 0. Because OLS is
RNG-independent and deterministic, the mismatch is a real computational bug
(data-read / formula / precision), and the agent had no in-loop signal (the
verifier is hidden during the agent phase). The decisive harness gap: nothing
prompted the agent to re-derive the number by an independent method. This is
the dominant shape of the scientific_computing failing cluster — task_001330
(m=0.048 vs 0.050; verifier hint: "Ensure you used numpy.random.seed(42) and
the correct parameters") and task_000011 (server value 50.75 vs 93.25) share
it: correct-looking compute, single self-consistent run, never cross-checked.
It is a mechanical, cross-task deficiency → Control hook, not a task solution.

### Changes

- `processors/numeric_crosscheck_nudge.py` — new `NumericCrossCheckNudgeProcessor`
  (`MultiHookProcessor`, `_order=32`). Arms on `on_before_tool` when a Bash
  command *runs* a computation (interpreter/compiler/binary run) AND either
  matches a generic numeric vocabulary (regress/bootstrap/percentile/
  monte-carlo/seed/mean/…) or writes a result/output artifact. On the matched
  `on_after_tool`, appends once a strategy-only nudge to the tool result:
  recompute the key quantity a second way, compare digit-by-digit, reconcile,
  re-check exact requirements/seed/order/format, confirm determinism. No task
  ids, answers, algorithms, or dataset literals. Contract-safe (result-string
  append only; validated: contract 0 violations).
- `config.yaml` — register the processor (absolute `file://`) at `_order=32`,
  after `CustomEditToolProcessor`; grader_client_deps + CustomSelfVerify
  retained unchanged.
- `system_prompt.txt` — copied byte-identical to R1 (sibling builder); no
  prompt change.

### Evidence

- `task_000111_cbada64a` messages: step 5 writes OLS+bootstrap C++; step 6 runs
  `g++ -O3 … && ./analyze && cat result.txt` → `2.5056,1.2262,3.9742,6.2925`;
  steps 7-9 (after self-verify) run only `ls -lh`/`cat` — no second-method
  recompute. `result.json.final_pytest`: `Expected m to be approx 2.5997, got
  2.5056`.
- `task_001330_f5aff1f5` `result.json.final_pytest`: `Expected m=0.050, but
  found m=0.048. Ensure you used numpy.random.seed(42) and the correct
  parameters.` — subtle seeded-MC parameter/order slip, never cross-checked.
- `task_000011_d089ef35` `result.json.final_pytest`: server responses
  `assert '50.75\n' == '93.25\n'` — computed value wrong, not re-derived.

### Uncertainty

If task_000111's C++ is actually correct and the reference/data is the
discrepancy source, an independent numpy recompute would agree with 2.5056 and
the task stays failing (nudge is a harmless no-op). The nudge relies on the
model choosing to act on it — it is guidance, not a forced recompute — so the
flip is probabilistic, not guaranteed. Watch: no regression on passing
scientific_computing tasks; the appended text must not error the run loop in
replay. Rollback per trigger above.
