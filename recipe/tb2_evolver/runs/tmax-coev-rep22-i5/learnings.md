# Tmax coev rep22-i5 — evolve journal

## Round 1 — break repeated length-truncation loop

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_length_loop_breaker_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_000958_4bb2b05d, task_001857_24daeef3, task_001116_4c65c2f5, task_001098_f5acdd79, task_001032_1adaccb9, task_001837_deaf31cb]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=10/50; +6/-1 gained=task_000297_01ba10b6,task_001492_94f30428,task_001696_b87e1463,task_001832_dd672877 lost=task_001321_658ce4a8; score 0.2000 >= incumbent(mean) 0.2000 - tol 0.0400
expected_global_gain: "Closes a 7-task failing cluster stuck in content-only max_tokens loops; flips tasks where a working artifact already existed and stops the rest from burning the full 80-step budget"
regression_risk: "False hard-stop on a task that would self-recover after 6+ consecutive length-truncations; inert on healthy trajectories since any tool call resets the counter"
cost_shift: "Net negative — prunes context on looping turns and caps wasted generations at ~6 instead of ~16-50"
rollback_trigger: "A previously-passing task newly fails with exit_reason=loop_detected AND <~10 steps of real tool activity → hard-stop too eager; raise hard_stop_after or revert"
-->

### Why

Assigned focus task_000010_644ab1c2 (create `/home/user/operator.py`) failed
`budget_exceeded` at 80 steps with reward=0. The agent correctly diagnosed the
real bug — naming the script `operator.py` triggers a Python stdlib circular
import (`from operator import eq` in `collections`) — and a working script
already existed at `k8s_operator.py`. But it then entered a degenerate loop:
16 of 33 assistant turns were content-only, hit `finish_reason=length`, and were
followed by the run-loop's passive "Your previous response was cut off by the
token limit. Please continue from where you left off." nudge, which re-primed the
same runaway narration. A single `mv` would have passed; the loop never let a
tool call out. This is systemic: 7 R0 tasks (task_000958, task_001857,
task_001116, task_001098, task_001032, task_001837, plus 000010) show the same
content-only length-truncation loop, all reward=0. The existing
`LengthTruncationRecoveryProcessor` only *nudges* (rewrites the passive message);
it never prunes the re-priming narration or hard-stops a non-recovering loop, so
the whole budget burns.

### Changes

- `processors/length_loop_breaker.py` — new `LengthLoopBreaker` MultiHookProcessor.
  Counts consecutive content-only `finish_reason=length` turns (`on_after_model`);
  any tool call resets. At `prune_after=3` it prunes the accumulated truncation
  narration + passive nudges from the context tail (keeps first message and all
  tool exchanges) and injects one hard "emit ONE Bash call" directive
  (`on_before_model`). At `hard_stop_after=6` it raises `LoopDetectedError` to
  reclaim the remaining step budget.
- `config.yaml` — register `LengthLoopBreaker` (via absolute `file://`) directly
  after `LengthTruncationRecoveryProcessor` (`_order=6`, so it escalates only
  after the per-turn nudge layer has run).

### Evidence

- `task_000010_644ab1c2` result.json: `exit_reason=budget_exceeded`, 80 steps,
  reward=0; final_pytest fails on missing `/home/user/operator.py`. Message log
  msgs 40–69: strict alternation of the passive "cut off by the token limit …
  continue" nudge and an assistant turn opening "The user is telling me to stop
  repeating myself and just run a command … Actually, the best solution is to
  just keep the script at /home/user/k8s_operator.py" — 5 byte-identical prose
  openings, zero tool calls.
- `task_000958_4bb2b05d` result.json: `budget_exceeded`, 80 steps, reward=0;
  assistant repeats "The user is telling me I'm stuck in a loop and hitting token
  limits" 8× with the passive nudge interleaved.
- Programmatic sweep of all `.messages.json`: content-only truncated assistant
  turns — task_000010 16/33, task_000958 16/33, task_001857 13/33,
  task_001116 10/48, task_001098 7, task_001032 6, task_001837 6. All reward=0.

### Uncertainty

The main risk is a false hard-stop on a task that would have self-recovered past
6 consecutive length-truncations. Evidence argues against this: the observed R0
loops ran 13–16 truncations without ever recovering, so 6 is well past the point
nudging has proven futile, and `keep_recent_pairs=1` keeps the model aware it was
just told to stop. If a previously-passing task newly fails with
`exit_reason=loop_detected` and little real tool activity, raise `hard_stop_after`
or revert.

## Round 2 — infra flake, no harness fix (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_docker_name_collision_infra_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — no config change. Assigned failure is an infra flake outside the evolvable surface."
regression_risk: "None — config copied byte-for-byte from R1."
cost_shift: "Zero — no pipeline change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Assigned focus `task_000028_7fe033ac` failed with `status=error`, `reward=0`,
`elapsed_s=0.1`. The error is a Docker container-name Conflict raised by
`recipe/tmax_eval/docker_env.py::start_container` (line 124) — the container never
started, so the agent run loop never began and there is **no `.messages.json`** for
this task. This is not a model capability gap and not an in-loop harness deficiency;
it is an infrastructure flake in the eval driver. `start_container` names containers
`tmax-{task_id[:20]}-{int(time.time())}` with whole-second resolution; when tasks
launch in the same wall-clock second under parallel batch eval, names collide and the
daemon rejects the second `docker run`. The same flake hit 5 tasks this round
(task_000015, task_000028, task_000140, task_001653, task_001781) — all
`status=error`, `elapsed_s=0.1`, all with no episode log.

### Changes

- `config.yaml` — byte-for-byte copy of R1 config (explicit no-op). No processor,
  tool, template, or knob change.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — documents root cause and requested fix
  (make container name collision-proof, e.g. UUID suffix) in read-only
  `recipe/tmax_eval/docker_env.py`.

### Evidence

- `task_000028_7fe033ac.result.json`: `status=error`, `elapsed_s=0.1`,
  `error="docker run failed ... container name ...-1788321907 is already in use"`,
  traceback at `docker_env.py:124 start_container`.
- No `task_000028_7fe033ac.messages.json` exists in the trajectory dir — agent
  loop never executed.
- Sweep of all `*.result.json`: 5 tasks share the identical `docker run failed`
  Conflict error at `elapsed_s=0.1`.

### Uncertainty

The evolvable `HarnessConfig` surface runs inside the agent phase; this failure
precedes container start. No config-side mechanism can intercept it. The correct
fix is a one-line change to container-name generation in the (read-only) recipe
driver — escalated in NEEDS_FROM_HUMAN.md. If a future round shows these 5 tasks
still erroring at elapsed_s=0.1, the human fix has not landed yet.

## Round 2 (c1) — same infra flake, assigned task_000015 (no-op)

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:30:00Z
hypothesis_id: h_docker_name_collision_infra_v1
levers: []
predicted_affected: []
gating_outcome: noop
gating_attribution: noop
expected_global_gain: "None — no config change. Assigned failure task_000015_89886d8d is the same Docker container-name-collision infra flake documented above; outside the evolvable HarnessConfig surface."
regression_risk: "None — config copied byte-for-byte from R1."
cost_shift: "Zero — no pipeline change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Independent diagnosis for this batch member's assigned focus,
`task_000015_89886d8d`. Confirmed the identical root cause already recorded in
the sibling Round 2 entry above (`h_docker_name_collision_infra_v1`):
`status=error`, `reward=0`, `elapsed_s=0.1`, no `.messages.json`, docker
container-name `Conflict` raised in `recipe/tmax_eval/docker_env.py::start_container`
(line 124) before the agent loop ever started. `start_container` names containers
`tmax-{task_id[:20]}-{int(time.time())}` at whole-second resolution, so parallel
launches within one second (or stale leftover containers) collide. No editable
processor / tool / prompt runs before container creation, so there is no defensible
HarnessConfig edit. Shipping explicit no-op.

### Changes

- `config.yaml` — byte-for-byte copy of R1 config (explicit no-op); sibling
  `system_prompt.txt` copied alongside so `SiblingSystemPromptBuilder` renders.
- `_meta_scratch/NEEDS_FROM_HUMAN.md` — root cause + requested fix in read-only
  `recipe/tmax_eval/docker_env.py` (high-entropy suffix, e.g. `uuid4`/`time_ns`,
  and pre-run stale-container cleanup / retry-on-conflict).

### Evidence

- `task_000015_89886d8d.result.json`: `status=error`, `elapsed_s=0.1`,
  `error="docker run failed ... container name /tmax-task00001589886d8d-1788321907
  is already in use by container f61b2..."`, traceback at
  `run_eval.py:150 -> docker_env.py:124 start_container`.
- No `task_000015_89886d8d.messages.json` exists — agent loop never executed.
- Sweep of `*.result.json`: 5 tasks (task_000015, task_000028, task_000140,
  task_001653, task_001781) share the identical Conflict error at `elapsed_s=0.1`.
- canonicalize on the copied config: `{"ok": true, "checked_templates": 0}`.

### Uncertainty

The failure precedes the agent phase; no config-side mechanism can intercept a
docker-daemon name conflict. Fix belongs in the read-only recipe driver
(escalated). If a future round still shows these 5 tasks erroring at
`elapsed_s=0.1`, the human-side fix has not landed.

## Round 2 — independent cross-check for computed results

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_numeric_crosscheck_v1
levers: [instruction]
predicted_affected: [task_000111_cbada64a, task_000117_1b598e44, task_001048_14335141, task_001937_ac874115, task_000396_e56917e2, task_000328_80fb4c9f]
cited_candidates: [C-004]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips a cross-domain (scientific_computing + data_science) wrong-answer/false-confidence cluster where the artifact is structurally perfect but the graded VALUE is wrong; an independent second-route recomputation catches the observed bug class"
regression_risk: "Prompt-only additive guidance; small token increase from an extra verification pass on numeric tasks; no loop/crash risk (control processors still guard); passing numeric tasks only get reconfirmed"
cost_shift: "Mildly positive (+tokens/steps) on correctness-graded numeric tasks that now run a second confirmation; negligible on non-numeric tasks (guidance is scoped)"
rollback_trigger: "A previously-passing numeric task newly fails, OR step/token cost rises materially while predicted tasks stay reward=0 (no cluster flips) -> revert to R1 prompt"
-->

### Why

Assigned focus `task_000111_cbada64a` (scientific_computing) finished cleanly —
`exit_reason=done`, 7 steps, `finished=no_tool_calls`, initial_pytest passed —
but `reward=0`: the verifier failed on the VALUE, `Expected m to be approx
2.5997, got 2.5056` (OLS slope off ~0.094). The agent's C++ was textbook-shaped
(correct OLS/bootstrap skeleton), compiled, ran, wrote a well-formatted
result.txt, and the `_tb2_self_verify` hook fired — but the agent's response was
purely structural (re-read checklist, `ls` the files, "formula correct ✓,
format correct ✓") with zero independent numeric recomputation. A one-line
numpy.polyfit on the same CSV would have produced the correct slope and exposed
the bug. This is a systemic cluster: sweeping for
`reward=0 AND exit_reason=done AND finished=no_tool_calls` surfaces multiple
wrong-VALUE / false-confidence failures — task_000117 (PDB column-parse bug),
task_001048 (integral 165.7908 vs 161.8028), task_001937 (grid 60 vs 50),
task_000396 (deviation 0.607 vs <0.1), task_000328 (300 frames vs ~10). All ran
without error and were declared done on an incorrect computed value.

### Changes

- `system_prompt.txt` (sibling read by `SiblingSystemPromptBuilder`, already
  wired in config) — add a general "independent cross-check for computed
  results" section: for any correctness-graded quantitative output, reconfirm
  the key result via a SECOND independent route (different language / tested
  library / hand computation on a slice) and reconcile discrepancies before
  finishing; sanity-check magnitude/sign/range; confirm the whole input was
  consumed. Names no task-specific constants, files, or algorithms.
- `config.yaml` — copied byte-for-byte from R1 (no processor/pipeline change).

### Evidence

- `task_000111_cbada64a.result.json`: reward=0, exit_reason=done, steps=7;
  final_pytest tail `AssertionError: Expected m to be approx 2.5997, got 2.5056`.
- `task_000111_cbada64a.messages.json` msgs 66–128: after `_tb2_self_verify`,
  the verification is structural only ("correct formula ✓ … format ✓ … files
  exist ✓") — no independent recomputation; declared SUCCESS on a wrong value.
- Cluster sweep: task_000117 `float('5 -94.99')` col-parse bug; task_001048
  `Chunk 0 integral mismatch. Expected 161.8028, got 165.7908`; task_001937
  `Expected Optimal Grid to be 50, but got 60`; task_000396 `max deviation
  0.6072 is not within (0.0, 0.1)`; task_000328 `got 300` frames vs ~10.

### Uncertainty

Main risk is cost inflation from an extra verification pass without flipping
the cluster — the model may still write a same-bug second implementation, or
over-verify trivially-correct values. If R3 shows predicted tasks still
reward=0 while cost climbs, the instruction is inert-but-expensive → revert to
the R1 prompt. Because the change is prompt-only and additive, it cannot
introduce loops or crashes (control processors remain in place).

## Round 2 (c3) — premise-consistency self-verify

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_premise_consistency_selfverify_v1
levers: [control]
predicted_affected: [task_000109_09ddd96b]
cited_candidates: [C-002]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Attacks the 'runs clean, wrong numbers, false confidence' cluster in data_science by enriching the exit-time self-verify checklist so it catches silent parse/units/type bugs the current file-existence-only checklist waves through"
regression_risk: "LOW — new processor is byte-identical in hook mechanics to the stock CustomSelfVerifyProcessor it replaces (same _singleton_group, _order=90, one-shot, contract-clean); only the injected checklist text grew by two task-agnostic steps"
cost_shift: "Slightly positive — a few hundred extra tokens in the single injected checklist message per task, plus possibly one diagnostic Bash turn on tasks that flag a premise mismatch; bounded and one-shot"
rollback_trigger: "A previously-passing data task newly fails with exit_reason in {loop_detected, budget_exceeded} traceable to post-self-verify re-examination breaking a correct decode -> revert to stock CustomSelfVerifyProcessor"
-->

### Why

Assigned focus `task_000109_09ddd96b` (data_science, Go WAV ETL) now finishes
`exit_reason=done`, 23 steps, `finished=no_tool_calls`, reward=0 — the R1
`AssistantReasoningRepeatBreaker` already killed the earlier `exit=error`
8x-reasoning loop, so this is no longer a loop failure: the program runs to a
clean natural stop but produces well-formed, wrong numbers. `final_pytest`:
mean of window 4 expected 0.0 got 135.243, and `anomaly.txt` expected "4" got
"1". The agent's own program reported "Total imputed values: 2" / "Imputation
proportion 0.000125" — which flatly contradicts the task's premise ("a batch
was corrupted ... silent conversion errors ... destroying downstream
correlation analysis") and its deliverable ("the window with the highest
proportion of imputed data"). Root cause is a silent data-decode bug (almost
certainly signed/unsigned int16) so window 4's 8000 `-32768` samples never
matched imputation. The stock `CustomSelfVerifyProcessor` fired but only made
the agent re-check file existence / JSON validity / window count — all of which
pass on the broken output. The checklist has no step that surfaces a result
contradicting the task's own stated severity.

This is distinct from the sibling `h_numeric_crosscheck_v1` (instruction lever,
independent recomputation): a second recomputation on the same wrongly-decoded
bytes agrees with itself and stays wrong. Only a premise-consistency check —
"the task says severe corruption but I measured ~none" — flags this class, and
it points the agent at the data-loading layer where the bug actually lives.
Different lever (control/processor vs instruction/prompt), different mechanism,
different anchor task.

### Changes

- `processors/plausibility_self_verify.py` — new
  `PlausibilitySelfVerifyProcessor`, a contract-clean drop-in replacement for
  `CustomSelfVerifyProcessor`. Identical one-shot keep-alive/exit mechanics;
  adds two task-agnostic checklist steps: (5) restate the answer magnitude/shape
  the task's framing implies and flag when computed numbers contradict it;
  (6) when flagged, re-examine the data-loading layer (signed/unsigned,
  endianness, field offsets, header/row skipping, units, column selection) with
  a raw-value diagnostic. No task IDs / constants / algorithms.
- `config.yaml` — replace the `CustomSelfVerifyProcessor` entry with the new
  processor via absolute `file://` path; all other pipeline entries unchanged.

### Evidence

- `task_000109_09ddd96b.result.json`: reward=0, exit_reason=done, steps=23,
  finished=no_tool_calls; final_pytest tail
  `Expected mean of window 4 to be 0.0, got 135.243` and
  `Expected anomaly.txt to contain '4', got '1'`.
- `task_000109_09ddd96b.messages.json` msg 30/44: program prints
  "Total imputed values: 2" / "Imputation proportion in anomaly window:
  0.000125"; msg 0 premise = "corrupted ... silent conversion errors ...
  destroying down-stream correlation analysis".
- msgs 40–42: `_tb2_self_verify` fired; agent's verification is structural only
  ("Valid JSON with 10 windows", `ls -lh`, "all features are floats") — never
  questions whether 0.000125 corruption is consistent with the task premise.

### Uncertainty

The specific int16 decode fix is model capability, not something the harness can
inject — so the flip is not guaranteed; the lever's job is to convert a silently
accepted wrong answer into an actively re-examined one, the only harness-level
handle available. Risk is a few hundred extra tokens per task and, rarely, one
diagnostic turn. If R3 shows task_000109 still reward=0 with no cost movement,
the check is inert; if a passing data task regresses via post-verify thrashing,
revert to the stock processor.

## Round 2 — ground exit check on task's own deliverable paths

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_deliverable_path_guard_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_000933_1f27096a, task_000321_ad43caad, task_000111_cbada64a, task_000118_3043e92d, task_000264_ab8c7253, task_001547_8cde5da2, task_001090_c61c71f2, task_000635_64d4f731, task_000396_e56917e2, task_000760_e197f7ff, task_000748_c9807703]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the 'correct logic, wrong/missing path' cluster (12 fail-set tasks whose first grader assertion is a missing prompt-named file) by blocking a clean exit until the task's own stated deliverable paths exist on disk"
regression_risk: "On a passing task the guard could add up to 2 extra verification turns if the prompt names an optional/alternative path that is legitimately absent; it never hard-stops (bounded max_blocks=2 then silent), and input files self-filter because they already exist"
cost_shift: "Mildly positive-to-neutral: <=2 short sandbox-probe turns on affected exits, offset by flipping runs that previously spent a whole budget for reward 0"
rollback_trigger: "A previously-passing task newly fails or its step count balloons with repeated [DeliverablePathGuard] injections that the agent cannot satisfy -> extraction is too aggressive; tighten the path regex or lower max_blocks, or revert"
-->

### Why

R1's `LengthLoopBreaker` fixed the assigned task's *loop* (task_000010 now exits
cleanly at 24 steps, `finished=no_tool_calls`, no runaway narration), but the
task still fails for a different, generalizable reason. The agent built a fully
working operator script, but relocated the deliverable off the required
`/home/user/operator.py` to `k8s_operator.py` (naming an actively-imported
script `operator.py` self-shadows the stdlib `operator` module when run from
that cwd). It then ran the generic `CustomSelfVerify` checklist, `ls`-ed its OWN
chosen path, declared success, and exited. Only 1 of 3 grader tests failed —
`test_operator_script_exists` on the missing required path. This is the
tb2-playbook "correct logic, wrong path" structural failure mode: the generic
self-verify is prose the agent satisfies against a path of its own choosing, and
is never grounded against the paths the task actually demands. The same
missing-required-file first assertion recurs across 12 fail-set tasks.

### Changes

- `processors/deliverable_path_guard.py` — new `DeliverablePathGuard`
  MultiHookProcessor. `on_task_start` extracts absolute file paths from the task
  description at runtime (conservative regex: workspace root + filename with
  extension; no hardcoded paths/ids). On an exit-intent turn (`on_after_model`,
  finish_reason end_turn/stop, no tool calls) it `test -e`s each in the sandbox;
  if any are missing it blocks the exit once (keepalive tool call + staged
  message naming the SPECIFIC missing paths and stating the file only needs to
  EXIST, not be importable, at that path). Bounded at `max_blocks=2`, then goes
  silent so an impossible deliverable cannot trap the loop.
- `config.yaml` — register `DeliverablePathGuard` (absolute `file://`) at
  `_order=91`, immediately after `CustomSelfVerifyProcessor` (_order=90), so the
  concrete path check escalates only after the generic checklist has run.

### Evidence

- `task_000010_644ab1c2` result.json: `exit_reason=done`, 24 steps,
  `finished=no_tool_calls`, reward=0; `final_pytest` — only
  `test_operator_script_exists` fails (`/home/user/operator.py does not exist`),
  2/3 pass. messages msg[0] prompt: "write a Python script at
  `/home/user/operator.py`"; msg[17] renames to `k8s_operator.py`; msg[45-47]
  `ls` on `k8s_operator.py`, "All requirements are met", exits.
- Programmatic sweep of the fail set: 12 tasks (`done`/`no_tool_calls`,
  reward=0) whose `final_pytest.output_tail` FIRST assertion is
  `os.path.isfile/exists(<prompt-named path>) ... does not exist / missing`:
  task_000010, task_000933, task_000321, task_000111, task_000118, task_000264,
  task_001547, task_001090, task_000635, task_000396, task_000760, task_000748.
- Path extraction verified against 5 prompts — captures required deliverables
  (`/home/user/operator.py`, `/home/user/result.txt`, etc.); input files it
  also captures self-filter because they already exist on disk.

### Uncertainty

Main risk is over-extraction flagging a path the task named but did not require
as an output, adding ≤2 verification turns on an otherwise-passing task. The
guard never hard-stops and input files self-filter, so the downside is bounded.
If a previously-passing task newly regresses with repeated unsatisfiable
`[DeliverablePathGuard]` injections, tighten the regex or lower `max_blocks`.
