# Tmax coev-rep19-i3 — evolve journal

## Round 1 — hard-block repeated-command loops

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_repeated_cmd_hard_block_v1
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000118_3043e92d, task_000506_c13429e7]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=18/50; +0/-1 lost=task_001898_471c0535; gating disabled (tolerance < 0)
expected_global_gain: "Frees 12-20 wasted steps on the budget_exceeded identical-command-loop cluster (3 R0 failures), giving the agent a real chance to reach the actual fix; generalises to any stuck-repeat class."
regression_risk: "A legitimate idempotent poll intentionally retried 7+ identical times gets blocked; bounded because the synthetic result redirects to a different probe and 7 identical no-progress runs is already pathological. Passing task_001032 maxes at 5 identical repeats — below threshold, unaffected."
cost_shift: "Down — blocked commands are not executed, so loop tasks stop burning tokens/time on redundant turns."
rollback_trigger: "A previously-passing task newly fails and its trajectory shows a [RepeatedCommandBreaker] BLOCKED line on a command that was legitimately being retried; then raise block_threshold or set to 0 (advisory-only)."
-->

### Why

Assigned focus `task_000028_7fe033ac` (nginx reverse-proxy → C++ UNIX-socket
backend) died `budget_exceeded` at 80 steps / 544s, reward 0, verifier
502 Bad Gateway. The trajectory tail shows the agent running a
byte-identical Bash command (`pkill -9 server ...; /app/server ...;
python3 -c "import socket..."`) 12 times in a row. Each run SUCCEEDED
(backend returned `HTTP/1.1 200 OK ... 150`) — the real fault was
elsewhere (nginx couldn't reach the socket) — yet the agent kept
re-probing the part that already worked. The existing
`RepeatedCommandBreaker` fired its `_HARD` advisory each turn
("Your very next command MUST be different") and the model ignored it
every single time. The same shape recurs on two more R0 failures:
task_000118 re-issued `ps aux | grep -v grep | grep python` 16 times,
task_000506 re-issued a `wfg_analyzer.py` probe 21 times — both
`budget_exceeded`, reward 0. The harness deficiency: the loop breaker
was advisory-only and had no teeth against a weaker model.

### Changes

- `processors/repeated_command_breaker.py` — new copy of the processor
  adding an `on_before_tool` HARD BLOCK: once a byte-identical command
  has already executed `block_threshold` times, the next re-issue is
  short-circuited (`approved=False` + `synthetic_result`), not executed.
  Existing warn(3)/hard(5) advisories retained.
- `config.yaml` — repoint `RepeatedCommandBreaker` `_target_` to the new
  file and add `block_threshold: 7`.

### Evidence

- `task_000028_7fe033ac` result.json: `exit_reason=budget_exceeded`,
  steps=80; last ~12 assistant turns byte-identical while tool result
  carried `[RepeatedCommandBreaker] STOP. This identical command has now
  run 6..12 times`.
- `task_000118_3043e92d` `budget_exceeded`; max identical-command repeat
  = 16 (`ps aux | grep -v grep | grep python`).
- `task_000506_c13429e7` `budget_exceeded`; max identical-command repeat
  = 21 (`echo ... | python3 .../wfg_analyzer.py 1 3`).
- Contrast: passing `task_001032_1adaccb9` (reward 1) maxes at 5 identical
  repeats — below block_threshold, so not affected.

### Uncertainty

The block frees budget but does not guarantee the agent finds the real
fix (e.g. the nginx socket-path/permission bug on task_000028). If the
cluster stays red at `budget_exceeded` even with steps freed, the gap is
a capability/knowledge one (debugging reverse-proxy integration), not a
loop-mechanism one — logged for a future round. Watch for any BLOCKED
line landing on a legitimately-retried idempotent poll.

## Round 2 — no-op: task_000117 has a broken grader

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_task117_broken_grader_noop_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — assigned focus task_000117 is graded by a verifier whose own reference-value computation crashes; no harness mechanism can flip it, and touching the pipeline only risks the 18 passing tasks."
regression_risk: "None — byte-identical config copy."
cost_shift: "None."
rollback_trigger: "N/A (no change)."
-->

### Why

Assigned focus was `task_000117_1b598e44` (scientific_computing: fix an
OpenMP float-reduction bug in `compute_centroid.cpp`, emit a PDB centroid
to `final_centroid.txt`). `exit_reason=done`, reward 0, 75 steps.

The failure is **not** a harness deficiency and **not** even an
achievable model gap — the task's grader is intrinsically broken. The
verifier's `test_final_state.py::get_expected_centroid()` computes the
reference centroid by reading `complex.pdb` with FIXED-WIDTH column
slices (`float(line[30:38])`, `float(line[38:46])`, `float(line[46:54])`).
But `complex.pdb` is WHITESPACE-separated with variable-width fields:
`ATOM      1  CA  ALA A    1      27.885 -94.998 -44.994  1.00 ...`.
So `line[38:46]` = `'5 -94.99'` (a digit from one number + a space +
part of the next) and `float()` raises
`ValueError: could not convert string to float: '5 -94.99'` — the crash
originates INSIDE the grader's own expected-value function, before any
comparison to the agent's output. No `final_centroid.txt` the agent
could write can make this task pass; it is impossible as graded.

Notably the agent actually produced the numerically correct answer:
its centroid `[0.19137382 0.14408625 -0.12847214]` matches the
whitespace-parsed Python reference (`Python centroid_x: 0.19137382…`),
written to `final_centroid.txt` as `0.191374 0.144086 -0.128472`. It
even explicitly discovered the fixed-width-vs-whitespace mismatch
(step body: "The PDB file is not in the standard fixed-width format").
The only conceivable pass path — reformatting `complex.pdb` into
standard fixed-width PDB columns so the grader's slices parse — is (a)
never requested by the task, (b) a domain-specific guess, and (c)
exactly the task-specific knowledge SOUL forbids baking into the harness.

Secondary observation (logged, not acted on): the agent burned ~60 of
its 75 steps in a reasoning oscillation ("Actually, I think… Let me
check… Actually…"), repeatedly re-`cat`-ing the .cpp and re-running the
same 5x determinism probe, and hit `response truncated by harness`
(output-token limit) several times mid-thought. `RepeatedCommandBreaker`
and `LengthTruncationRecoveryProcessor` both fired; the model churned
anyway. This is a model reasoning-quality gap, and even a perfect loop
break cannot pass an impossible-grader task — so no mechanism is worth
the regression risk to the 18 passing tasks this round.

### Changes

- `config.yaml` — byte-identical copy of R1 config (explicit no-op).

### Evidence

- `task_000117_1b598e44` result.json `final_pytest.output_tail`:
  `y_sum += float(line[38:46])` → `ValueError: could not convert string
  to float: '5 -94.99'`, raised inside `get_expected_centroid()` at
  `/tmp/test_final_state.py:23`, called from `test_final_centroid_value`
  at line 53 — i.e. the reference computation, not output validation.
- `task_000117_1b598e44` messages step (probe): `Line 0: Parse error -
  X='   27.88', Y='5 -94.99', Z='8 -44.99'` on the raw PDB line.
- Agent's final artifact matches the correct whitespace-parse reference:
  centroid.h5 `/centroid = [0.19137382 0.14408625 -0.12847214]`,
  `final_centroid.txt = 0.191374 0.144086 -0.128472`.

### Uncertainty

If a future round's data regenerates `complex.pdb` in standard PDB
fixed-width layout, the grader would work and this becomes a genuine
(model-capability) task — revisit then. Until then, treat task_000117
as a dead grader: do not spend evolve rounds on it.

## Round 2 — optimum-selection clause on exit-intent nudge

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_optimum_selection_argmin_v1
levers: [control]
predicted_affected: [task_000017_fed73abc]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips task_000017 and any 'return the optimum of a stochastic/iterative search' task where the agent commits the last-iterate value instead of the argmin/argmax candidate; targets the 7/7-red scientific_computing cluster via a pure strategy clause with no task literals."
regression_risk: "Low — adds one gated bullet to a message that fires at most once per task on the voluntary exit-intent turn; runs that die at the step cap never see it, and no already-passing cluster depends on committing a last-iterate value."
cost_shift: "Negligible-to-slightly-up: one ~120-word paragraph on <=1 turn/task, plus maybe 1-2 verification Bash calls on genuine optimum tasks."
rollback_trigger: "If a previously-passing scientific_computing task newly fails and its trajectory shows the agent second-guessing a correct last-iterate optimum after reading the [NumericCrossCheck] optimum-selection bullet, drop the clause."
-->

### Why

Assigned focus `task_000017_fed73abc` (scientific_computing) exited `done` in
17/80 steps with reward 0. Every mechanical step was correct: it found the
`temp *= 1.05` bug, changed it to `0.95`, confirmed `libnetcdf-dev`, compiled,
ran the annealer, and `ncdump`ed the trajectory. The single error: the objective
`score_position(pos) = (pos-42)^2 - 50` has its minimum at position 42, but the
agent took the LAST element of the `position` array (43) — a stochastic accepted
step, one index off the optimum — and never inspected the `score` variable at
all. Expected primer `AGCTAGCGCGCTAGC` (pos 42); committed `GCTAGCGCGCTAGCT`
(pos 43). This is a generalizable failure shape: for "return the optimum of a
search" deliverables the answer is the candidate at argmin/argmax of the recorded
objective, not the last iterate the search happened to log.

### Changes

- `processors/numeric_crosscheck_nudge.py` — copy of the R1/c3 NumericCrossCheckNudge
  with one added bullet (#2): if the deliverable is the optimum of a search/
  optimization (annealing, MCMC, gradient descent, genetic/random search, grid
  sweep), select it by inspecting the recorded score/energy/error series and
  taking the argmin/argmax candidate, and reconcile against the last-iterate
  value rather than assuming the last logged value is the optimum. Same
  exit-intent trigger, singleton, order 91, no tool calls.
- `config.yaml` — repoint NumericCrossCheckNudge `_target_` to the new file;
  updated the accompanying comment to describe the optimum-selection clause.

### Evidence

- `task_000017_fed73abc` result.json: `exit_reason=done`, steps=17; final_pytest
  `AssertionError: Expected primer sequence 'AGCTAGCGCGCTAGC', but got
  'GCTAGCGCGCTAGCT'` — a one-position shift (42 vs 43).
- `task_000017` messages.json (assistant after `ncdump -v position`): "The final
  position (last value in the array) is 43 ... So the optimal position is 43";
  the `score` variable was never inspected. The subsequent `_tb2_self_verify`
  exit-intent turn fired the existing nudge, but its "re-derive by a second
  method" wording gave no reason to re-select the trajectory value, so the agent
  re-verified file existence and committed 43.
- Cluster context: all 7 scientific_computing tasks scored 0 this round
  (task_000017/000111/000117/001035/001048/001330/001937); task_001035 (optimal
  primer GCGG vs GCAT) and task_001937 (optimal grid 60 vs 50) are same-cluster
  "find the optimum" deliverables, weaker corroboration (different mechanisms).

### Uncertainty

The clause frees the correct selection path but does not guarantee the agent
executes it — it must still inspect the `score` series and take argmin. If
task_000017 stays red at `done` with the same one-shift answer even with the
clause present, the gap is a model-capability one (won't act on the reminder),
not a harness one — log and stop spending rounds on it. Watch for any
previously-passing scientific_computing task regressing after reading the new
bullet.

## Round 2 — spec-convention audit on exit-intent nudge

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-30T23:47:06Z
hypothesis_id: h_spec_convention_audit_v1
levers: [control]
predicted_affected: [task_000111_cbada64a, task_001330_f5aff1f5, task_001048_14335141, task_000017_fed73abc, task_001035_26564093, task_001937_ac874115]
cited_candidates: [C-010]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Targets the 7/7-red scientific_computing cluster whose single recurring mechanism is a self-consistent implementation off by a SPEC CONVENTION (regression axis/order, RNG seed & call order, indexing/off-by-one, integration endpoints, search stopping rule, rounding). Reorients the pre-exit nudge to an adversarial line-by-line convention audit — the correct remedy for convention bugs, where a second-method cross-check is useless because both methods share the misread convention."
regression_risk: "Low — SUPERSEDES (does not add to) the R1 NumericCrossCheckNudge in the same pipeline slot, so no double-injection; fires at most once per task on the voluntary exit-intent turn, appends one user message, emits no tool call. Runs that die at the step cap never see it; non-computational tasks ignore it. No confirmed-passing cluster depends on the old wording (R1 nudge attribution still pending)."
cost_shift: "Negligible-to-slightly-up: one ~250-word message on <=1 turn/task, plus possibly a few extra verification Bash calls on genuine exact-value tasks (intended — buys correctness)."
rollback_trigger: "If a previously-passing scientific_computing (or any exact-value) task newly fails and its trajectory shows the agent thrashing/second-guessing a correct value after reading the [SpecConventionAudit] message, revert to the R1 nudge wording."
-->

### Why

Assigned focus `task_000111_cbada64a` (scientific_computing) exited `done` in
8/80 steps, reward 0. The agent wrote a textbook OLS + bootstrap C++ program,
ran it once, got slope `m=2.5056`, verified only that `/home/user/result.txt`
existed and was formatted, and committed. The grader expected `m=2.5997` — a
~3.7% gap on a deterministic regression, i.e. not float noise but a spec-
convention deviation the agent never audited. This is the whole cluster's
shape: all 7 scientific_computing tasks scored 0, and every verifier tail shows
a self-consistent implementation off by a convention (task_001330 regressed
y-on-x instead of the required x-on-y and mis-ordered the seed-42 draws → m=0.048
vs 0.050; task_001048 integration off by an endpoint/bin convention → 165.79 vs
161.80; task_000017 primer shifted one char; task_001035/001937 wrong optimization
convention). The R1 NumericCrossCheckNudge leads with "re-derive by a SECOND
INDEPENDENT METHOD and compare", which is weak here: two methods that share the
same misread convention agree with each other and both stay wrong. The nudge
needs to lead with an adversarial, line-by-line convention re-read.

### Changes

- `processors/spec_convention_audit.py` — new `SpecConventionAuditNudge`, same
  one-shot exit-intent trigger / singleton / order 91 / no-tool-call shape as the
  R1 NumericCrossCheckNudge, but message content reoriented: step 1 forces an
  adversarial re-read of every value-changing convention (axis/order, seed & RNG
  call order, indexing/off-by-one, endpoint/boundary, sample size, rounding,
  units), citing the exact spec sentence and code line per convention, and treats
  "close but not exact" as a convention-mismatch signal; the second-method
  cross-check and magnitude/sign sanity-check are retained as steps 2-3.
- `config.yaml` — replace the last processor entry (NumericCrossCheckNudge) with
  SpecConventionAuditNudge pointing at the new file; updated the comment to
  describe the convention-audit reorientation. No other pipeline change.

### Evidence

- `task_000111_cbada64a` result.json: `exit_reason=done`, steps=8; final_pytest
  `AssertionError: Expected m to be approx 2.5997, got 2.5056`. messages.json
  final assistant: "The task is complete ... The output is: `2.5056,1.2262,3.9742,6.2925`"
  — committed after one run, only file/format re-checked.
- `task_001330_f5aff1f5` spec quotes "you must regress x on y (x = my + c)" and
  "numpy.random.seed(42)"; final assistant "The task is complete ... m=0.048,
  c=40.237"; grader: "Ensure you used numpy.random.seed(42) and the correct
  parameters" — convention misread, committed with no audit.
- Cluster: all 7 scientific_computing tasks reward 0 with self-consistent-but-off
  values (000111/000117/000017/001035/001048/001330/001937), not crashes/format
  errors.

### Uncertainty

The audit frees the correct diagnosis path but does not guarantee the model
executes it — it must actually re-read the spec and spot the deviation. For
task_000111 specifically the win is less certain (its convention deviation is
subtle); the bet is cluster-level, not a single-task guarantee. Note a sibling
R2 proposal (`h_optimum_selection_argmin_v1`) edits the same R1 nudge for the
argmin/argmax sub-case; only one proposal is selected, so no live collision.
If the cluster stays red at `done` with the same self-consistent values even
with this nudge present, the residual gap is model-capability (won't act on the
reminder), not harness — log and stop spending rounds on it.

## Round 2 — non-persistent-shell / cwd-reset guard

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-31T00:30:00Z
hypothesis_id: h_shell_cwd_nonpersistent_guard_v1
levers: [control]
predicted_affected: [task_000028_7fe033ac]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the non-persistent-shell/cwd-reset cluster: any compile-and-run / build-a-service / cd-then-run task where the agent assumes an interactive terminal, invokes a relative path (`./prog`, `python app.py`) after building elsewhere, and gets `No such file or directory` it mis-reads as a code bug. task_000028 is the clearest instance (backend never launched → verifier 502)."
regression_risk: "Low — nudge only appends text to a tool result that ALREADY errored, and only when the command relied on non-persistent state (relative invocation / standalone cd). Commands using absolute paths or self-chained `cd DIR && cmd` are never flagged; bounded to <=2 fires/task."
cost_shift: "Down on affected tasks (agent stops looping on a phantom missing-file bug and reaches the fix, freeing budget); negligible elsewhere (<=2 short appends, only on erroring commands)."
rollback_trigger: "A previously-passing task newly fails and its trajectory shows a [ShellCwdGuard] nudge on a command that legitimately used a relative path that DID resolve in the workspace root (false positive); then tighten `_looks_like_cwd_trap` or set max_fires=1."
-->

### Why

Assigned focus `task_000028_7fe033ac` (nginx reverse-proxy → C++ UNIX-socket
video backend) died `budget_exceeded` at 80 steps / 574s, reward 0, verifier
502 Bad Gateway. R1's `RepeatedCommandBreaker` hard-block was aimed here, and
its own Uncertainty note predicted that if the task stayed red at
`budget_exceeded` the residual gap was elsewhere — this round pins that gap. The
real fault is a **non-persistent-shell / cwd-reset** trap: `docker_sandbox.exec`
runs every Bash call as a FRESH `docker exec` with `workdir=self._workspace_path`
(there is no persistent shell). The agent compiled a correct binary to
`/app/server` (step [66] `g++ ... 2>&1` → exit 0) then ran it by RELATIVE path
(`./server ...`, steps [68]/[72]); each run returned `bash: ./server: No such
file or directory` because cwd was the workspace root, not `/app`. The
`stoi`/`Error parsing frame count:` lines at the top of those results were
STALE append-only content of `/app/server.log` from the first broken binary,
which compounded the confusion ("the server is still running old code"). The
backend never launched, so nginx proxied 502. `EnvironmentContextInjector`'s
integrity/sandbox reminders cover data fabrication, service-killing, and
background-process non-persistence — but say nothing about cwd/shell
non-persistence. The error text looks like a missing file, so a weaker model
cannot infer the cwd cause: a harness "agent lacks dynamic context" gap.

### Changes

- `processors/shell_cwd_guard.py` — new `ShellCwdGuard` (contract-safe: augments
  `event.result` only, like `RepeatedCommandBreaker`, order 32). `on_before_tool`
  flags Bash commands relying on non-persistent state (relative executable/script
  invocation, or a standalone `cd` not chained with `&&`); `on_after_tool`, if
  that command's output carries a `No such file or directory` / `command not
  found` signature, appends ONE corrective nudge (bounded `max_fires=2`)
  explaining the shell is non-persistent and to use absolute paths or chain
  `cd DIR && cmd`. Self-chained commands (`cd /abs && ./prog`) are never flagged.
- `config.yaml` — append `ShellCwdGuard` after `NumericCrossCheckNudge`;
  everything else byte-identical to R1.

### Evidence

- `task_000028_7fe033ac` result.json: `exit_reason=budget_exceeded`, steps=80,
  final_pytest 502 Bad Gateway (`assert 502 == 200`).
- messages.json step [66] `g++ -o /app/server /app/server.cpp -std=c++11 2>&1`
  → `(exit 0, no output captured)`; steps [63]/[69] tool result contains
  `bash: ./server: No such file or directory`.
- Root cause in code: `recipe/tmax_eval/docker_sandbox.py::exec` uses
  `workdir=self._workspace_path` per call (fresh exec, cwd reset each turn).
- Detector unit-checked: fires on the exact trap command `./server >> ... &`
  and on `python3 app.py`; does NOT fire on `/app/server &`,
  `cd /app && ./server &`, `cat /app/server.log`, or the `g++` build line.

### Uncertainty

The nudge surfaces the cwd cause but does not guarantee the agent then launches
the backend correctly AND leaves it running (its final R0 step was
`pkill -9 -f server`, which would kill the backend even if it started). If
task_000028 reaches `done`/verify but still 502s because the server was killed
at exit, the residual gap shifts to the "don't kill your own service" reminder
(already present) not landing — log and revisit. Watch for any false-positive
[ShellCwdGuard] fire on a relative path that legitimately resolved.


## Round 2 — degenerate-repetition recovery (supersede length-only)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_degenerate_repetition_recovery_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_000028_7fe033ac, task_000747_424c178b, task_001717_a9c46d8d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Reclaims 30-40 wasted steps on the 4-plus-task degenerate-loop budget_exceeded cluster (task_000010/000028/000747/001717 all hit 80/80), converting dead loop turns into a real fix attempt or a committed best-effort artifact; generalises to any repetition attractor on any task class."
regression_risk: "Self-repetition heuristic false-positives on a legitimately repetitive long turn; bounded by requiring 55pct-plus duplicate sentence-fragments over 900-plus chars AND collapse only above head+tail size, preserving 1200+600 chars of real structure. Terminal nudge only after 5 degenerate turns, so healthy runs never see it."
cost_shift: "Down - degenerate turns collapsed (fewer input tokens re-fed) and run pushed to commit-and-stop instead of grinding to the 80-step cap; no change on non-degenerate runs."
rollback_trigger: "A previously-passing task newly fails and its trajectory shows a self-repeating collapse landing on a turn that was NOT a loop (e.g. a legitimate long enumerated answer), or the terminal nudge firing on a healthy run; then raise repetition_ratio toward 0.7 / min_repetition_chars, or revert to LengthTruncationRecoveryProcessor."
-->

### Why

Assigned focus `task_000010_644ab1c2` (write `/home/user/operator.py` K8s
operator) died `budget_exceeded` at 80/80 steps, reward 0. Two layers:

1. Root task blocker - the required filename `operator.py` shadows the stdlib
   `operator` module when run as `python3 /home/user/operator.py` (CWD on
   sys.path[0]); `collections` doing `from operator import eq` then crashes the
   interpreter, and the verifier hits the same crash. The model found a
   working `importlib.util` run from `python3 -c` but never found the in-file
   fix (strip sys.path[0] before stdlib imports). That specific Python trick
   is a capability gap - no harness change conjures it.
2. The dominant, generalizable harness deficiency - a repetition attractor
   loop. From ~step 40 the model content collapses to the same
   "Actually, let me try using importlib.util..." narration re-emitted every
   turn to the 80-step cap. The existing LengthTruncationRecoveryProcessor
   only acts when finish_reason=length AND no tool call; but 20 of 30
   truncated turns here also carry a repeated no-op `ls`, so the processor
   never collapsed the runaway content and hard-reset its escalation counter
   each interleaved turn - the attractor persisted, budget exhausted. Same
   shape recurs on task_000028 (13 continue-nudges), task_000747 (13),
   task_001717 (7).

### Changes

- `processors/degenerate_repetition_recovery.py` - new
  DegenerateRepetitionRecoveryProcessor, superset of the length-recovery
  processor. Triggers on degeneracy (length-truncated no-tool-call turn OR
  content dominated by self-repetition via a sentence-fragment de-dup ratio),
  collapses runaway content regardless of an attached tool call, keeps a
  decaying (not hard-reset) degeneracy score so escalation is sticky across
  interleaved turns, and escalates through a terminal "commit best artifact
  and stop" nudge. Purely structural - no task-specific literal.
- `config.yaml` - replace the LengthTruncationRecoveryProcessor entry with
  the new processor (same pipeline position / _order); all other processors
  byte-identical to R1.

### Evidence

- task_000010_644ab1c2 result.json: exit_reason=budget_exceeded, steps=80;
  final_pytest "Could not import runpy module ... module importlib has no
  attribute util" (the shadowing crash).
- messages.json lines 79-96: ~2 KB repeated narration + a tool call - the OLD
  processor "not event.tool_calls" guard is FALSE, so no collapse, counter
  reset. Script confirms 20/30 truncated turns carry a tool call.
- messages.json lines 454-540: RepeatedCommandBreaker counts the same `ls`
  up to 18x while narration regenerates; run reaches 80/80.
- Truncation-marker / continue-nudge density across the cluster:
  task_000010 53/14, task_000747 15/13, task_000028 11/13, task_001717 7/7.

### Uncertainty

Breaking the loop reclaims budget but does not guarantee task_000010 flips to
pass - its underlying operator.py-shadowing fix is a capability/knowledge gap
(logged: task_000010_644ab1c2 requires knowledge of CWD sys.path shadowing of
the stdlib operator module; no harness fix flips it - the value is cluster-wide
budget recovery, not this single flip). If the cluster stays red at
budget_exceeded even with the loop broken, the residual gap is capability, not
loop-mechanism. Watch for any false-positive self-repetition collapse on a
legitimately long/enumerated answer turn.
