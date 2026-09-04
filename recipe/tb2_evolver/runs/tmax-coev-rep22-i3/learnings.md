# Evolve journal — tmax-coev-rep22-i3

## Round 1 — hard-stop length loop

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_length_loop_hardstop_v1
levers: [control]
predicted_affected: []
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=12/50; +4/-3 gained=task_000328_80fb4c9f,task_001031_a8f0eb37,task_001653_c4cafa73,task_002167_3d0510c1 lost=task_000321_ad43caad,task_001321_658ce4a8,task_001492_94f30428; score 0.2400 >= incumbent(mean) 0.2400 - tol 0.0400
expected_global_gain: "Caps budget bleed on the 'model freezes into consecutive full-length no-tool-call turns' cluster; frees shared step/wall budget for other tasks in the round"
regression_risk: "A model that would have recovered on truncation turn 7+ is terminated at 6; judged unlikely — a 6-deep identical no-tool-call length loop recovering is not observed in the evolve set"
cost_shift: "Net down — bounds worst-case per-task output tokens on frozen runs (was unbounded to full budget, now ~6 truncations); zero added cost on healthy runs"
rollback_trigger: "Next round shows a previously-passing task newly ending exit_reason=loop_detected at the hard-stop boundary (model was mid-recovery) -> raise hard_stop_threshold or set 0"
-->

### Why

Assigned focus `task_000010_644ab1c2` (system_administration, reward=0,
exit_reason=done, finished=no_tool_calls, 539.8s). The reward-0 root cause is
a task-design trap: the task requires the deliverable at `/home/user/operator.py`,
which shadows Python's stdlib `operator`; when the verifier runs pytest from
`/home/user` the shadow triggers a circular import and pytest cannot collect.
The agent's script is otherwise functionally correct. A harness fix for that
would need task-specific knowledge (rename/guard the file) and fails the
generalization test — logged as a task/capability trap, not patched.

The generalizable harness gap the trajectory exposes is separate: while
agonizing over the operator.py conflict the model froze into **13 consecutive**
`stop_reason=length` no-tool-call turns (steps ~39-69), each a full 4096-token
re-narration — roughly 1/3 of the run's budget with zero forward action. The
existing `LengthTruncationRecoveryProcessor` fires (content collapsed to ~1964
chars, corrective nudge injected) but only ever *nudges* — it has no upper
bound and the model ignored the nudge all 13 turns. `CyclicLoopBreaker` keys on
tool-call cycles so a no-tool-call loop never enters its window; nothing in the
pipeline could terminate the loop.

### Changes

- `processors/length_recovery_escalating.py` — drop-in superset of the stock
  `LengthTruncationRecoveryProcessor`: keeps collapse + escalating-nudge
  verbatim, adds a `hard_stop_threshold` (default 6). After that many
  *consecutive* length-truncation no-tool-call turns, raises `LoopDetectedError`
  from `on_after_model` → clean `exit_reason='loop_detected'`, best output
  recovered, workspace preserved for the verifier. Counter resets on any tool
  call or non-length finish; `hard_stop_threshold<=0` restores stock behaviour.
- `config.yaml` — swapped the stock recipe `_target_` for the new `file://`
  processor and added `hard_stop_threshold: 6` (well above `repeat_threshold=2`
  so the nudge gets 4+ chances first). All other pipeline entries unchanged.

### Evidence

- `task_000010_644ab1c2.result.json`: `agent.finished="no_tool_calls"`,
  `elapsed_s=539.8`, `reward=0`; `final_pytest` tail =
  `ImportError: cannot import name 'namedtuple' ... circular import
  (/home/user/operator.py)` — the operator.py shadow trap.
- Raw event log (`0521d7a5-...jsonl`): 13 records with
  `meta.stop_reason="length"` and `usage.output_tokens=4096`, consecutive over
  steps ~39-69; persisted assistant messages collapsed to ~1964 chars (proof
  the stock processor fired) yet the model kept truncating.
- Messages index: user turns 40,42,46,50,...,68 all the passive/nudge
  continuation while assistant turns stayed 1964 chars with no tool call.

### Uncertainty

Predicted_affected is empty on purpose — this is a cost/budget-containment
claim, not a pass-rate flip (task_000010 stays failed on the trap). The bet is
that early termination of a demonstrably-frozen run is strictly better than
nudging forever. Watch next round for any task that flips T→loop_detected at
the boundary; if seen, the model was mid-recovery and the threshold should rise.

## Round 2 — deliverable path-integrity self-verify

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_deliverable_path_integrity_selfverify_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the largest failing shape this round — 16/50 tasks trip a verifier os.path.isfile(<exact_path>) assertion because the deliverable didn't land at the required exact path. A general exit-time path-reconciliation step forces the agent to restore any renamed/relocated required deliverable before exiting."
regression_risk: "Low — adds text to a fire-once, non-blocking exit checklist; a correctly-placed deliverable passes trivially. Worst case a few extra ls/mv Bash calls on already-passing tasks."
cost_shift: "Near-zero; +~10 lines on the one-shot self-verify message, no added turns on healthy runs, possibly 1-2 corrective Bash calls on tasks whose paths had drifted."
rollback_trigger: "A previously-passing task newly failing while the agent thrashes on path reconciliation, or self-verify message materially inflating step counts on the passing cluster -> revert to NumericSelfVerifyProcessor."
-->

### Why

Assigned focus `task_000010_644ab1c2`. Under R1's config the run no longer
freezes (116s, 30 steps) — the length-loop hard-stop landed. The live failure is
now the classic TB2 "correct logic, wrong path": the task mandates the deliverable
at the EXACT path `/home/user/operator.py`; the agent wrote it there, ran it, hit a
circular import (operator.py shadows stdlib `operator` when `/home/user` is
`sys.path[0]`), and "fixed" it by renaming the deliverable to `k8s_operator.py`.
The script then ran correctly, but the verifier's `os.path.isfile('/home/user/
operator.py')` fails. The self-verify checklist fired and the agent *explicitly
re-read* the constraint ("task says /home/user/operator.py ... I renamed it") yet
kept the wrong name — it lacked the principle that a required exact path is a
non-negotiable constraint whose workaround must adapt (run from a different cwd /
execution-only copy), not the deliverable. This is an instruction gap delivered at
the wrong moment: a step-0 prompt rule was already present and ignored; the fix
belongs at the exit boundary. The shape recurs — 16/50 failing tasks this round
trip a missing-exact-path verifier assertion.

### Changes

- `processors/path_integrity_self_verify.py` — `PathIntegritySelfVerifyProcessor`,
  a drop-in subclass of the stock `CustomSelfVerifyProcessor` sharing
  `_singleton_group="tb2_self_verify"` (so it REPLACES the active
  NumericSelfVerifyProcessor; only one self-verify fires). Same fire-once
  keepalive + one-shot injected user message mechanism. The injected checklist
  prepends a **deliverable path-integrity** step (enumerate every required exact
  path; if a required deliverable was renamed/relocated/re-extensioned — even to
  dodge an error — restore it to the exact path and adapt the workaround instead)
  and preserves the numeric/interpretation cross-check step verbatim. No task-
  specific literals (0 literals findings).
- `config.yaml` — swapped the `NumericSelfVerifyProcessor` entry for the new
  `PathIntegritySelfVerifyProcessor` `file://` path; all other pipeline entries
  unchanged.

### Evidence

- `task_000010_644ab1c2.result.json`: `reward=0`, `final_pytest` tail =
  `AssertionError: Operator script /home/user/operator.py does not exist.`
- `task_000010_644ab1c2.messages.json`: step ~15 `python3 /home/user/operator.py`
  → circular-import `ImportError`; step ~16 `mv operator.py k8s_operator.py`; step
  ~19 `_tb2_self_verify` fires, assistant acknowledges the deviation but keeps the
  renamed file, then hits the token limit.
- Cluster scan of all 50 `result.json`: 16 tasks fail on a verifier
  `os.path.isfile/exists(<exact_path>)` assertion (task_000010, _000111, _000118,
  _000264, _000321, _000396, _000635, _000760, _000933, _001048, _001116, _001492,
  _001547, _001704, _001877, _001937).

### Uncertainty

The 16-task cluster is heterogeneous — some are wrong-dir, some never created, some
content-wrong (task_000635 is a trailing-whitespace mismatch). The path-integrity
step directly targets the *deliberate-rename* sub-mechanism (task_000010) and, more
broadly, forces exit-time reconciliation of every required exact path, which should
also catch wrong-dir/wrong-extension drift. It cannot help tasks where the file was
never produced at all or where content (not path) is wrong. Predicted_affected is
scoped to the assigned task; watch the broader missing-exact-path cluster next round
for movement and any regression from checklist verbosity.


## Round 2 — infra container-name conflict (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_docker_name_conflict_infra_noop_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None this round — assigned failure is an out-of-scope orchestration bug, not a harness gap. No config change can flip it."
regression_risk: "None — byte-identical copy of R1 config."
cost_shift: "Zero."
rollback_trigger: "N/A (no change). If docker_env.py naming is fixed by a human, the 8 error-status tasks should re-run cleanly and rejoin the scored set."
-->

### Why

Assigned focus `task_000028_7fe033ac` (system_administration) has
`status="error"`, `reward=0`, `elapsed_s=0.1`. It is NOT a harness or model
capability failure — the agent never ran. The container failed to start:
`docker: Error response from daemon: Conflict. The container name
"/tmax-task0000287fe033ac-1788258698" is already in use ...`, raised at
`recipe/tmax_eval/docker_env.py:124` (`start_container`).

Root cause is in read-only runner code, `docker_env.py:105`:
`name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]`.
The second-resolution `int(time.time())` collides on retry / same-second
concurrency, so `docker run --name` hits an existing container and the
RuntimeError fires before the run loop boots. All 8 `status="error"` tasks in
this trajectory set share this identical signature (each elapsed_s ≈ 0.1):
task_000015, task_000028, task_000140, task_000505, task_000748, task_001032,
task_001090, task_001781.

Per tb2-playbook + hard invariant #2, container lifecycle/naming is outside the
config.yaml evolvable surface (processors / tool_registry / system prompt only)
and lives in read-only recipe code. The failure precedes the first agent turn,
so no MultiHookProcessor hook can ever fire on it. There is no in-scope harness
fix. Shipping explicit no-op (byte-for-byte R1 copy) rather than drifting onto
another proposal's territory.

### Changes

- `config.yaml` — byte-for-byte copy of R1 config (explicit no-op).
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — documents the docker_env.py naming bug
  and three candidate fixes (uuid/time_ns suffix, pre-run `docker rm -f`,
  retry-on-conflict), all out of meta-agent write scope.

### Evidence

- `task_000028_7fe033ac.result.json`: `status="error"`, `elapsed_s=0.1`,
  error = docker container-name Conflict; traceback tail at
  `docker_env.py:124 in start_container`.
- 8/8 error-status tasks in the set match `already in use by container` with
  elapsed_s ≈ 0.1 (grep-confirmed).
- `docker_env.py:105`: second-resolution `int(time.time())` name suffix — the
  collision source.

### Uncertainty

The bug is confidently diagnosed and confidently out of scope. Only risk: if a
human fix lands in docker_env.py, these 8 tasks re-enter the scored set and
their *actual* agent-phase pass/fail becomes visible for future rounds to work.

## Round 2 — infra container-name collision (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_docker_name_collision_infra_noop_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None from config surface — the failure is upstream of the run loop. Documented a 16% infra-loss floor (8/50) for the human/infra owner to fix in recipe/tmax_eval/docker_env.py"
regression_risk: "None — config is byte-identical to R1"
cost_shift: "Zero — no config change"
rollback_trigger: "N/A (no-op)"
-->

### Why

Assigned focus `task_000015_89886d8d` (software_engineering). Its
`result.json` shows `status="error"`, `reward=0`, `elapsed_s=0.1` — the
agent process NEVER started. Error:
`docker run failed ... Conflict. The container name
"/tmax-task00001589886d8d-1788258698" is already in use`. The run loop
dies at container creation, before any processor / tool / template can
instantiate.

This is systemic: across the 50 trajectories the status split is
`ok=41, agent_error=1, error=8`, and **all 8 `error` tasks share the
identical docker container-name Conflict** with `elapsed_s ~0.1`
(task_000015, task_001090, task_001781, task_000748, task_000140,
task_000028, task_001032, task_000505). A guaranteed ~16% infra-loss
floor every round, unrelated to model capability or harness config.

Root cause is in `recipe/tmax_eval/docker_env.py::start_container`:
`name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]`
— 1-second-resolution timestamp. Under `run_eval.py`'s
`ThreadPoolExecutor(max_workers=args.concurrent)`, two tasks launched in
the same wall-clock second generate the same container name and the
second `docker run` fails. `HarnessConfig` (my only writable surface)
governs the in-container processor pipeline + system prompt and is
loaded AFTER `start_container` succeeds — it is structurally incapable
of fixing a container that never starts. `recipe/tmax_eval/` is
read-only for the meta-agent.

### Changes

- `config.yaml` — **byte-identical copy of R1** (explicit no-op). No
  config-surface intervention can touch an upstream container-start
  failure; fabricating a processor here would be theater that risks
  regressing the 41 healthy tasks for zero possible upside.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — full diagnosis + three
  concrete fixes for the infra owner (uuid-suffixed container name;
  retry-on-conflict with `docker rm -f`; pre-run reap of dangling
  `tmax-*` containers).

### Evidence

- `task_000015_89886d8d/result.json`: `status="error"`, `reward=0`,
  `elapsed_s=0.1`, error = `docker: ... Conflict. The container name
  "/tmax-task00001589886d8d-1788258698" is already in use`.
- Round-wide scan: 8/50 tasks `status="error"`, every one the same
  Conflict message, every one `elapsed_s ~0.1` (agent never ran).
- `recipe/tmax_eval/docker_env.py:105`:
  `name = f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]`
  — 1s-resolution name under `--concurrent>1` ThreadPoolExecutor.

### Uncertainty

None on the diagnosis (identical error, sub-second elapsed, source
line reproduces the collision). The only "risk" is that the human does
not action `NEEDS_FROM_HUMAN.md`, in which case the 16% floor persists
regardless of any config the meta-agent ships. No harness fix exists.

## Round 2 (c4) — verifier fixed-width parse crash (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_verifier_pdb_fixedwidth_crash_noop_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None this round — assigned failure is an unwinnable verifier bug (the grader crashes computing the expected value), not a harness gap. No config change can flip it."
regression_risk: "None — byte-identical copy of R1 config."
cost_shift: "Zero."
rollback_trigger: "N/A (no change). If the task's verifier is repaired to whitespace-split PDB coordinates (or the PDB is re-emitted in true fixed-width columns), task_000117 becomes solvable and its real agent-phase pass/fail becomes visible."
-->

### Why

Assigned focus `task_000117_1b598e44` (scientific_computing, reward=0,
exit_reason=done, finished=no_tool_calls, 110.4s). The task: fix an OpenMP
atomic->reduction determinism bug in `compute_centroid.cpp`, compile, run, and
write the centroid to `final_centroid.txt`. The agent did all four correctly:
swapped the atomic adds for `reduction(+:sum_x,sum_y,sum_z)`, compiled with the
serial HDF5 libs, ran it, and wrote a well-formed `final_centroid.txt`
(`0.191334 44.973621 44.974152`) — passing 4/5 verifier tests.

The single failing test is a **verifier bug, not an agent failure**. The
grader's `get_expected_centroid()` recomputes the centroid by fixed-width
slicing `float(line[38:46])`, but this task's PDB file is *not* laid out in
standard PDB fixed-width columns — its coordinates are space-separated with
extra leading padding, so `line[38:46]` == `'5 -94.99'` and Python's
`float()` raises `ValueError` (unlike C++ `std::stof`, which stops at the
embedded space). The crash happens while computing the *expected* value,
before any comparison to the agent's file — so **no agent output can make this
test pass**. The task is unwinnable.

Per SOUL.md's harness-vs-capability test and hard invariant #4: any fix would
require injecting the task-specific fact "this verifier mis-parses this PDB",
which fails the generalization test and is out of the config-evolvable surface
(the verifier runs post-exit and is read-only per tb2-playbook). Shipping an
explicit no-op (byte-for-byte R1 copy) rather than drifting onto another
proposal's territory.

Secondary (non-actionable) observation: the agent burned steps ~54-84 (~half
the run) re-diagnosing the PDB column layout with near-identical Python probes;
`CyclicLoopBreaker` warned at step 64 but the slightly-varied index probes
kept it from a hard stop. Since the task is unwinnable regardless, tightening
the loop-breaker here would risk regressing already-passing tasks for zero
pass-rate gain — deliberately not touched.

### Changes

- `config.yaml` — byte-for-byte copy of R1 config (explicit no-op).

### Evidence

- `task_000117_1b598e44.result.json`: `final_pytest.passed=false`,
  `test_final_centroid_value` fails with
  `ValueError: could not convert string to float: '5 -94.99'` inside
  `get_expected_centroid()` at `y_sum += float(line[38:46])`; the other
  4 tests pass and the agent's `final_centroid.txt` parses cleanly.
- `task_000117_1b598e44.messages.json` step 70: agent's own probe shows
  `Index 38-46: '5 -94.99'` — the exact slice the verifier later crashes on.
- Step 77: agent correctly reasons "the task is to fix the floating-point
  reduction bug and not to fix the PDB parsing" — its scope judgment was right.

### Uncertainty

Diagnosis is high-confidence: the ValueError is in the grader's own
expected-value path, deterministic and independent of the agent's deliverable.
Only way task_000117 re-enters the winnable set is a verifier/task fix, which
is outside meta-agent scope.

## Round 2 (c3) — OLS slope reference-data trap (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_ols_reference_data_trap_noop_v1
levers: []
predicted_affected: []
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "None - assigned task is a reference-data/capability trap; the numeric cross-check harness capability it would need already exists (NumericSelfVerifyProcessor) and fired correctly on this task"
regression_risk: "None - byte-identical config copy; no behavior change"
cost_shift: "Zero - no config change"
rollback_trigger: "N/A (no-op)"
-->

### Why

Assigned focus task_000111_cbada64a (scientific_computing, reward=0,
exit_reason=done, finished=no_tool_calls, 9 steps, 36.6s). The task asks for a
C++ OLS linear regression on /home/user/noisy_data.csv writing
m,c,ci_lower,ci_upper to result.txt. The verifier expects m approx 2.5997; the
agent produced m=2.5056. Root cause is a reference-data / capability trap,
not a harness deficiency: the agent's C++ gave m=2.5056, AND its own
independent Python re-derivation of textbook OLS on the same container data
also gave exactly m=2.5056 (intercept c=1.2262 matched between both methods
too). Standard OLS is unambiguous - two independent implementations agreeing on
2.5056 means correct math on the data present in the container. The verifier's
golden 2.5997 is only reachable from a different dataset (non-deterministic data
generation) or a non-OLS reference formula. No harness mechanism can make a
correct OLS produce 2.5997 without hardcoding the answer, which is forbidden
(memorises one task, fails the generalization test).

Critically, the relevant harness capability already exists and fired: the
pipeline's NumericSelfVerifyProcessor (added R1/c3, cited task_000111) injects
a "recompute the key quantity by a second independent method and reconcile any
mismatch" checklist. The trajectory shows the agent doing exactly that
(independent Python re-derivation) - the slope matched, the only discrepancy was
the bootstrap CI, which the agent correctly attributed to C++ std::mt19937 vs
Python random RNG differences. Retroactive check (Variant A): the corrective
fix is already in place, it fired, the agent complied, and the task still
failed, so the symptom is downstream of a data/capability gap outside harness
scope. Re-proposing any numeric-verify instruction would duplicate the existing
mechanism (novelty violation) and would still not flip the task.

Per the brief's escape clause ("if your focus turns out to be unsupported by the
trajectories, say so and make the smallest defensible edit"): explicit no-op.

### Changes

- config.yaml - byte-for-byte copy of R1 config (explicit no-op).
- system_prompt.txt - copied alongside so SiblingSystemPromptBuilder
  resolves against the output dir.

### Evidence

- task_000111_cbada64a.result.json: final_pytest.passed=false,
  AssertionError Expected m to be approx 2.5997, got 2.5056
  (deviation 0.0941, far above tol 0.001).
- task_000111_cbada64a.messages.json tool result: agent's C++ binary
  emits 2.5056,1.2262,3.9742,6.2925.
- Same file, self-verify step: agent's independent Python OLS prints
  m = 2.5056, c = 1.2262 - identical slope/intercept to C++, confirming the
  math is correct on the container data; only ci bounds differ (3.9894 vs 3.9742)
  from RNG implementation, which the agent correctly diagnosed.
- NumericSelfVerifyProcessor present in R1 config (lines 94-95), its
  _tb2_self_verify call appears in the trajectory, so the numeric cross-check
  harness capability already fired and the agent followed it.

### Uncertainty

High-confidence no-op. The only path back into the winnable set for task_000111
is a task/reference-data fix (regenerate golden from the same CSV the container
ships), which is outside meta-agent scope. Broader scientific_computing cluster
(task_001048 integral, task_001937 grid, task_000396 sim-error) shows a related
"plausible-but-wrong numeric value" shape, but each has a distinct mechanism and
is already the target of the existing NumericSelfVerifyProcessor - no new
generalizable lever surfaced from task_000111 specifically.
