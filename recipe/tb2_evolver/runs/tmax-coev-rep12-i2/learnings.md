# Evolve Journal — tmax-coev-rep12-i2

Cross-round memory for the meta-agent. One `## Round N` section per evolve.

## Round 1 — early verifier-dep guard

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-12T00:00:00Z
hypothesis_id: h_verifier_dep_guard_early_v1
levers: [control]
predicted_affected: [task_000773_9bc1b20e, task_000796_828a72cf, task_000910_16cc0daf, task_002108_a8cfbf2a]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=20/50; +4/-4 gained=task_000199_d50c0e57,task_000603_3ed5cdb5,task_000718_2c9ef3a3,task_000948_7c813b00 lost=task_000197_1460b736,task_000311_82f9e3ba,task_000716_206dc0f6,task_001016_b1b861c4; score 0.4000 >= incumbent(mean) 0.4000 - tol 0.0400
expected_global_gain: "Recovers the 'correct/attempted solution, uncollectable verifier' class: 4 tasks abort at pytest collection with ModuleNotFoundError (3x requests, 1x imageio) across security/sysadmin/file_ops. Early+broader guard installs the dep regardless of exit path."
regression_risk: "One extra Bash tool call appended to the agent's first response on every task; fully `|| true`-guarded no-op when deps present. Worst case a few seconds of pip time on lean images."
cost_shift: "+1 short Bash round-trip per task (was already ~1 on clean-exit tasks under the old guard); negligible token delta."
rollback_trigger: "If R2 pass_rate drops vs R0 baseline (20/50) OR the synthetic replay / any task shows exit_reason=error attributable to the injected first-turn Bash call, revert to the exit-only guard."
-->

### Why

Baseline 20/50 (0.40). Beyond the large `budget_exceeded` thrash cluster
(~12 tasks stuck in repetition loops on genuinely hard crypto / reverse-
engineering / multi-service tasks — model capability gaps, not harness),
there is a clean **harness-shaped** cluster: 4 tasks whose verifier aborts
at **pytest collection time** with `ModuleNotFoundError` — the grader does
`import requests` / `import imageio` against a container whose base image
lacks the package. Solution quality is irrelevant; the test file cannot
even be collected, so `reward=0`.

The R0 config already carried a `VerifierDepGuardProcessor`, but it fired
**only on the agent's clean exit-intent turn** and its allow-list was just
`(requests, pyyaml)`. Trajectory grep of `VERIFIER DEP CHECK` shows it
fired on the 36 clean-exit tasks but on **none** of the 3 ImportError
tasks that ended via `budget_exceeded` / `error` — the exact tasks that
needed it. And task_000773 (a clean finish) still failed because `imageio`
was not in the allow-list.

### Changes

- `processors/verifier_dep_guard.py` — rewrote `VerifierDepGuardProcessor`
  to fire **once early** (first `on_after_model`), appending a forced Bash
  ensure-deps call alongside the agent's own first tool calls, instead of
  gating on a graceful exit. Expanded allow-list to the ubiquitous verifier
  libs: `requests, imageio, Pillow(PIL), numpy, scipy, pyyaml`. Still a
  no-op when present; fires at most once.
- `config.yaml` — repointed the guard `_target_` `file://` to the R1 copy;
  updated the comment. Everything else copied byte-for-byte from R0,
  including the `system_prompt.txt` sidecar (copied into R1 so the sibling
  prompt builder still finds it).

### Evidence

- `task_000773_9bc1b20e` final_pytest tail:
  `/tmp/test_final_state.py:8: in <module> import imageio.v3 as iio /
  E ModuleNotFoundError: No module named 'imageio'`; exit=done, guard
  fired but `imageio` absent from allow-list.
- `task_000796_828a72cf` result.json `"exit_reason": "error"`; tail
  `import requests / ModuleNotFoundError`; no dep-check marker in
  messages → old guard silent on error exits.
- `task_000910_16cc0daf`, `task_002108_a8cfbf2a` result.json
  `"exit_reason": "budget_exceeded"`, `steps: 80`; tail `import requests /
  ModuleNotFoundError`; no dep-check marker → old guard silent on step-cap.

### Uncertainty

Highest-confidence flip is task_000773 (clean finish blocked solely at
collection). The three `requests` tasks were thrashing / incomplete, so
the install alone may not flip them this round — but firing early
structurally guarantees deps for any correct-but-non-clean-exit solution
going forward, which the exit-only guard could not. Risk is the appended
first-turn Bash call: it runs after the agent's own first call and is
`|| true`-guarded, so it should never fail the run. If replay or R2 shows
a first-turn error or a pass-rate regression, revert.

### needs_from_human / capability gaps (no harness fix — skip)

- The `budget_exceeded` cluster (task_000032 crypto crack, task_000730 &
  task_001877 git-forensics, task_001044 binary reverse-engineering,
  task_000796/000910/002108 multi-service builds) reflects model
  reasoning/domain capability gaps, not harness deficiencies — the agent
  visibly loops ("I keep repeating the same commands"). No harness fix;
  a repetition-loop breaker could save budget but would not flip these.

## Round 2 — narration-loop breaker

<!-- journal:frontmatter
round: 2
timestamp: 2026-08-22T20:27:45Z
hypothesis_id: h_content_repetition_loop_v1
levers: [control]
predicted_affected: [task_000910_16cc0daf, task_001028_5bc8bc70, task_000716_206dc0f6, task_001877_bd2513aa]
cited_candidates: [C-002]
gating_outcome: accepted
gating_attribution: score=21/50; +2/-1 gained=task_000197_1460b736,task_000897_f4216424 lost=task_000603_3ed5cdb5; score 0.4200 >= incumbent(mean) 0.4000 - tol 0.0400
expected_global_gain: "Cuts wasted step/cost across the ~4-task narration-loop cluster (all pinned at 80-step cap); secondary chance to flip a borderline task by forcing an earlier pivot to writing required output files"
regression_risk: "Advisory nudge could fire on a legitimately iterative task that narrates similarly; mitigated by high threshold (3 near-identical + escalate at 5), min_content_chars floor, no passing R1 task showed a 3+ identical-narration streak"
cost_shift: "Net negative (cheaper) — interrupting a 15-turn identical streak at turn 3 removes ~12 wasted model turns; pure no-op on non-looping tasks"
rollback_trigger: "R3 pass_rate below R1 (20/50) beyond repeat noise, OR synthetic replay / any task shows exit_reason=error attributable to this processor, OR a previously-passing task regresses with a content-repetition nudge visible in transcript"
-->

### Why

R0=20/50, R1=20/50 (flat; R1's +4/-4 task swap classified as run-to-run
noise). The dominant *harness-shaped* signal in R1 is a content-repetition
loop: 4 of the 5 `budget_exceeded`/`max_steps` failures (all pinned at the
80-step cap) emit degenerate near-identical assistant narration turn after
turn. task_000910 shows 15 consecutive identical assistant turns;
task_001028 shows 9 consecutive identical turns and 24 turns sharing the
same 60-char prefix. Existing guards miss this: LengthTruncationRecovery
fires only on `finish_reason=="length" and not tool_calls` (these finish
normally / carry tool calls); CustomEditTool counts only repeated file-write
commands (these are diagnostic/compile commands); CustomSelfVerify fires once
on exit-intent only. No live mechanism detects a normal-finish narration loop
mid-run — a detection/interception gap, the canonical MultiHookProcessor case.

### Changes

- `processors/content_repetition_recovery.py::ContentRepetitionRecoveryProcessor`
  — new MultiHookProcessor, `_order=6` (right after length-recovery).
  Fingerprints normalized assistant content on `on_after_model`; after
  `repeat_threshold=3` consecutive look-alike turns injects a corrective nudge
  (change tactic / gather new evidence), escalating at `escalate_threshold=5`
  to a hard "STOP — write the required output file to its exact path or run one
  genuinely new command". +1 user message per chain max; `min_content_chars=40`
  avoids tripping on terse action turns. Counter resets after each nudge.
- `config.yaml` — register new processor after LengthTruncationRecovery;
  carry R1's VerifierDepGuardProcessor forward byte-for-byte (repointed to
  R2-local copy); SiblingSystemPromptBuilder sidecar `system_prompt.txt`
  copied into R2/.

### Evidence

- `task_000910_16cc0daf` (probe5.py over R1 messages.json): 15 consecutive
  identical assistant turns ("Let me try a different approach - use a simpler
  command to compile and test."); exit=budget_exceeded, 80 steps.
- `task_001028_5bc8bc70`: 9 consecutive identical turns ("I've been stuck in a
  loop trying to understand the binary. Let me take a completely different
  approach."); 24 turns share same 60-char prefix; exit=budget_exceeded, 80 steps.
- `task_000716_206dc0f6`: 4 consecutive identical turns; env-injected
  "stop the repetition" already present but not breaking the loop;
  exit=budget_exceeded, 80 steps.
- Counterfactual: had this been live in R1, task_000910 interrupts after turn 3
  of its 15-turn streak (~step 6-9 instead of 80); the escalated nudge routes
  toward writing the required output file before the cap — the specific gap
  that scored these 0 (files never created).

### Uncertainty

Neither loop task is guaranteed to flip (both are genuinely hard: multi-service
daemon + binary RE). Worst realistic case is no flips but large step/cost
savings on the loop cluster. Main risk is a false-positive nudge on a
legitimately iterative task; no passing R1 task showed a 3+ identical-narration
streak (passing tasks finished in 6-33 steps), so predicted collateral is near
zero. Trigger threshold is unvalidated at runtime — if R3 shows the nudge firing
without breaking loops, or any regression with a nudge in-transcript, revert.


## Round 3 — command-repetition loop breaker

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-22T21:25:00Z
hypothesis_id: h_command_repetition_loop_v1
levers: [control]
predicted_affected: [task_001028_5bc8bc70, task_000032_3fb303f6, task_000348_31fb8c8a, task_002108_a8cfbf2a, task_001465_aa3ed3f8]
cited_candidates: [C-003]
gating_outcome: accepted
gating_attribution: score=22/50; +2/-1 gained=task_000603_3ed5cdb5,task_000716_206dc0f6 lost=task_000199_d50c0e57; score 0.4400 >= incumbent(mean) 0.4200 - tol 0.0400
expected_global_gain: "Closes the repeated-Bash-command loop class (~9 budget_exceeded tasks all pinned at the 80-step cap that re-issue one identical command 10x-34x). Escalation nudge forces an output-write attempt before the cap on tasks that currently reach it empty-handed; the rest recover large step/cost budget."
regression_risk: "False-positive nudge on a legitimately iterative task that re-runs one command (e.g. poll-until-ready). Mitigated: threshold=4 is clear of the 2-3x build->fix->rebuild retries; no passing R2 task showed a 4x-or-more identical-command run (passing tasks finished in 6-41 steps). Nudge is advisory (+1 user turn), never blocks the tool call."
cost_shift: "Net negative (cheaper). Interrupting a 33-turn identical-command streak at turn 4 removes ~29 wasted model+tool turns; pure no-op on non-looping tasks."
rollback_trigger: "R4 pass_rate below R2 (21/50) beyond run-to-run noise, OR synthetic replay / any task shows exit_reason=error attributable to this processor, OR a previously-passing task regresses with a command-repetition nudge visible in transcript."
-->

### Why

R0=20/50, R1=20/50, R2=21/50. The dominant harness-shaped signal is the
budget_exceeded loop cluster (~9 tasks, all pinned at the 80-step cap). R2
shipped a content-fingerprint loop breaker (ContentRepetitionRecoveryProcessor)
predicted to fire on these, but tool-call analysis of the R2 run shows its
nudge text appears 0 times across all 50 messages.json: it never armed. The
loops do not repeat content verbatim, the assistant re-words long narration each
turn (task_000910: 1112/1964-char turns that oscillate; max consecutive
same-120-char-prefix run = 1). The loop is only visible at the tool-call layer:
the agent re-issues the SAME Bash command 4x-34x. LengthTruncationRecovery gates
on finish_reason==length (these finish normally with tool calls);
CustomEditToolProcessor counts only file-write commands (these re-issue
curl/compile/probe commands). No live guard watches repeated tool-call command
strings, the missing mechanical hook.

### Changes

- processors/command_repetition_recovery.py::CommandRepetitionRecoveryProcessor
  new MultiHookProcessor, _order=7 (after the content-repetition guard).
  Normalizes each Bash tool-call command over a rolling window=12; after
  repeat_threshold=4 occurrences injects a corrective nudge (get NEW info / run
  a command not yet tried), escalating at escalate_threshold=7 to a hard STOP
  (write the required output to its exact path with ls -l, or gather genuinely
  new evidence). +1 user turn per armed streak; no task-specific literals.
- config.yaml register the new processor after ContentRepetitionRecovery; carry
  R1 VerifierDepGuardProcessor and R2 ContentRepetitionRecoveryProcessor forward
  byte-for-byte (repointed to R3-local copies); system_prompt.txt sidecar copied
  into R3/.

### Evidence

- task_001028_5bc8bc70: 33/33 tool calls one identical echo pipe; exit=budget_exceeded, 80 steps.
- task_000032_3fb303f6: 34/34 tool calls identical probe; never wrote /home/user/report.txt; exit=budget_exceeded, 80 steps.
- task_000348_31fb8c8a: compile reissued 10x, server-kill 5x, nohup-start 5x; exit=budget_exceeded, 80 steps.
- task_001465_aa3ed3f8: identical curl POST /embed 13x; exit=budget_exceeded, 80 steps.
- Counterfactual: R2 content guard nudge text = 0 occurrences across all 50 messages.json. A command-string detector would interrupt task_001028 at command #4 (~step 8) instead of 80.

### Uncertainty

Neither cited task is guaranteed to flip; several are capability-bound (crypto
crack, multi-service builds). Worst realistic case: no flips but large step/cost
savings across the loop cluster. Main risk is a false-positive nudge on a
poll-until-ready pattern; threshold=4 over a 12-command window is above the 2-3x
retries seen on passing tasks. If R4 shows the nudge firing without breaking
loops, or any regression with a command nudge in-transcript, revert.

### needs_from_human / capability gaps (no harness fix, skip)

- task_000998_4d9c7852: verifier crashes with circular-import because the task
  mandates writing /home/user/operator.py, which shadows stdlib operator at the
  pytest-collection CWD. File name is task-required, no generalizable harness fix.
- The 19 exit_reason=done failures produce wrong values while the agent believes
  it verified ("all validations passed"), genuine domain/reasoning capability
  gaps (statistics thresholds, SQL graph queries). No harness fix.


## Round 4 — real workflow system prompt (instruction)

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-22T22:09:41Z
hypothesis_id: h_workflow_system_prompt_v1
levers: [instruction]
predicted_affected: [task_002146_0bc2994c, task_000348_31fb8c8a, task_000885_d1c1007b, task_000032_3fb303f6]
cited_candidates: [C-004]
gating_outcome: accepted
gating_attribution: score=22/50; +1/-1 gained=task_000311_82f9e3ba lost=task_000718_2c9ef3a3; score 0.4400 >= incumbent(mean) 0.4400 - tol 0.0400
expected_global_gain: "Recovers the slice of the ~15-task exit_reason=done-but-wrong cluster whose defect is a SKIPPED self-check (dead service, wrong endpoint response, un-recomputed value) rather than an unreachable capability; plausibly 2-4 flips and structurally hardens every task against premature-exit-with-empty-output."
regression_risk: "Longer prompt could over-verify and inflate steps on already-short passing tasks; mitigated — strategy-only, verification scoped to REQUIRED deliverables, passing tasks finish 6-40 steps under the 80 cap. No task literals."
cost_shift: "Mildly positive — a few extra verification Bash calls per task on tasks that previously exited early; bounded by the step cap and partly offset by less mid-task flailing from clearer up-front planning."
rollback_trigger: "R5 pass_rate below R3 (22/50) beyond run-to-run noise, OR a previously-passing short task regresses with visible over-verification thrash in transcript, OR synthetic replay shows exit_reason=error attributable to the prompt. Revert to the 5-line default stub."
-->

### Why

R0=20, R1=20, R2=21, R3=22 (all within run-to-run noise for 20+ repeats).
The three prior rounds all shipped Control-lever loop breakers. Direct
transcript inspection of the R3 run is decisive: the R3 command-repetition
nudge armed **0 times** across all 50 tasks (nudge text absent from every
messages.json) — even on task_001016 which ran ONE identical command 34/34
times, i.e. the exact case the guard was built for. The R2 content nudge is
likewise not present. So the Control lever on the loop cluster is exhausted /
unverifiable, and the residual budget_exceeded cluster has shrunk to 5 tasks,
all capability-bound (crypto, OCR pipelines, multi-service C++ APIs).

The dominant remaining failure mode is now `exit_reason=done` with wrong or
missing output — the agent calls end_turn believing it finished. A subset of
these are SKIPPED self-checks the agent could have caught with Bash: it never
exercised the service it was told to leave running, never POSTed to its own
endpoint, never recomputed the expected value. That is a knowledge-of-when gap
(when/how to verify), the canonical Instruction case — and Instruction has
never been tried on this benchmark (scoreboard: 0 attempts).

Crucially, the R3 config comment claimed a general-workflow prompt was
installed, but the shipped `system_prompt.txt` sidecar was still the 5-line
`DEFAULT_TMAX_PROMPT` stub — the intended Instruction change was documented
but never actually written. This round actually writes it.

### Changes

- `system_prompt.txt` (sidecar beside config.yaml, read by
  SiblingSystemPromptBuilder) — replace the 5-line stub with a strategy-only
  general workflow: survey the environment and confirm exact input/output
  paths before coding; restate concrete deliverables and format before
  implementing; diagnose before retrying (don't re-run the same command);
  verify every required output exists at its EXACT path in the correct format
  the way the external checker will (exercise servers with a real client,
  recompute expected values and compare); keep required background services
  alive and the checker's dirs clean; always write a best-effort partial to
  the required path rather than leaving it empty. No task-specific literals.
- `config.yaml` — pipeline carried forward from R3 byte-for-byte (all three
  meta processors repointed to R4-local copies); comment updated. The
  instruction change is isolated so its effect is attributable.

### Evidence

- Passing end (habit fired): task_000381 clean finish in 6 steps; task_000707
  in 9; task_000798 in 6; task_000457 in 7 (per-task result.json agent.steps,
  all exit_reason=done, reward=1) — scoped survey→act→confirm.
- Failing end (habit absent at decisive step): task_002146 final tool output
  prints `=== TASK COMPLETE === Manager is running: 1 process(es)` then the
  agent stops, yet the checker fails with
  `ConnectionError HTTPConnectionPool(host='127.0.0.1', port=8080)` — the
  served endpoint was never exercised before exit. task_000348 final_pytest:
  `TypeError: unsupported operand type(s) for -: 'NoneType' and 'float'` — the
  `/process_spectrum` endpoint returned a null field; the agent never POSTed to
  its own server. task_000032 final_pytest: report line 1 is `admin123`,
  expected `network2023` — no independent recomputation before exit.
- Control-lever exhaustion: grep of all 50 R3 messages.json for the command- and
  content-repetition nudge strings returns 0 and (near) 0 hits respectively.

### Uncertainty

A longer prompt could induce over-verification and inflate steps on short
passing tasks; passing tasks have wide headroom under the 80-step cap, so the
realistic worst case is a small cost bump, not a pass-rate regression. It will
NOT flip purely capability-bound tasks (OCR policy extraction, Go
circular-import refactor, statistics-threshold tasks) — those remain logged
capability gaps. If R5 drops below R3 or a short passing task visibly thrashes
on verification, revert to the stub.

### needs_from_human / capability gaps (no harness fix, skip)

- task_000885 (TF-IDF/SVD 50-components on 14 features → sklearn ValueError),
  task_000164 / task_000785 / task_000925 (statistics: wrong y_pred, ks_stat,
  ridge alpha), task_000311 / task_000908 (SQL graph collaborator ranking),
  task_000669 / task_001465 / task_001674 / task_000438 (OCR-policy / corpus
  filtering / Go refactor) — domain/reasoning capability gaps; the workflow
  prompt may help them verify-and-notice but cannot supply the missing method.

## Round 5 — fire truncation nudge on first hit

<!-- journal:frontmatter
round: 5
timestamp: 2026-08-22T23:01:13Z
hypothesis_id: h_length_recovery_threshold_v1
levers: [configuration]
predicted_affected: [task_000730, task_001028]
cited_candidates: [C-005]
gating_outcome: accepted
gating_attribution: score=22/50; +1/-1 gained=task_000773_9bc1b20e lost=task_000311_82f9e3ba; score 0.4400 >= incumbent(mean) 0.4400 - tol 0.0400
expected_global_gain: "Recovers the truncation-loop/budget_exceeded slice whose only failing assertion is 'required output file missing' by pulling the existing file-write escalation one turn earlier (>=2 tasks: task_000730, task_001028)"
regression_risk: "Escalated nudge fires one turn sooner, but ONLY on finish_reason=length & no tool_calls — ~absent on passing tasks (task_000267=1 event, still passed); no passing task reaches the step cap"
cost_shift: "Net negative (cheaper): forcing one short Bash call on the first truncation removes 7-18 runaway re-generation steps per truncation-heavy task; no-op on non-truncating passing tasks"
rollback_trigger: "R6 pass_rate below R4 (22/50) beyond noise, OR a previously-passing task regresses with the length-recovery nudge visible in-transcript, OR synthetic replay shows exit_reason=error from the earlier-firing nudge — revert repeat_threshold to 2"
-->

### Why

R4 stayed flat at 22/50 (0.44) for the third round running. The dominant
harness-fixable driver is a max_tokens truncation loop: the run loop appends
"Your previous response was cut off by the token limit. Please continue…" once
per `finish_reason=length & no tool_calls` step. Grepping R4 messages.json,
this string appears 2–18 times on EACH failing truncation task
(task_000730=18, task_000032=16, task_000796=15, task_001016=8, task_001028=7,
task_001044=4, task_000908=3, task_000506=2) and essentially zero on passing
tasks (only task_000267=1, which still passed). Two of these tasks
(task_000730, task_001028) fail solely because the required output file was
never created — the agent burned its whole step budget re-generating truncated
analysis and reached the cap empty-handed. `LengthTruncationRecoveryProcessor`
already exists to detect this and inject an escalating file-write nudge, but at
`repeat_threshold=2` the *hard* "write the required output to its exact path
now" directive is delayed to the second consecutive truncation.

### Changes

- `config.yaml` — `LengthTruncationRecoveryProcessor.repeat_threshold` 2 → 1
  (hard escalation nudge fires on the first truncation).
- `config.yaml` — meta-authored processor `file://` paths (command/content
  repetition guards, verifier dep guard) repointed from R4 to R5-local copies;
  the three processor modules and `system_prompt.txt` sidecar copied into R5
  byte-for-byte (md5 verified identical to R4). No behavioral change to those.

### Evidence

- `task_000730` final_pytest tail: `os.path` AssertionError on
  `test_solution_file` — required solution file never written before the cap;
  18 truncation events in messages.json.
- `task_001028` final_pytest tail: `test_success_file_exists_and_correct`
  AssertionError — required file absent; 7 truncation events.
- `task_001044` messages.json indices 3/7/14/22 are user turns carrying the raw
  "cut off by the token limit … continue" text, each preceded by an assistant
  turn with `tool_calls=False` — the exact `finish_reason=length & not
  tool_calls` condition the processor gates on.
- Rejected tightening the command/content-repetition guards instead: passing
  iterative tasks reach 5–6 identical full-command repeats (task_000197=6,
  task_000716=5), overlapping the failing loops at any firing threshold →
  real regression risk. The truncation signal has no such overlap, so it is
  the safe lever.

### Uncertainty

Firing the hard nudge one turn earlier gives ~1 extra early action turn aimed
at creating the missing file — the single failing assertion on task_000730 /
task_001028 — but does not guarantee the file content is correct; those may
stay F on a content mismatch even if the existence check flips. For the
capability-bound truncation tasks (task_000032, task_000796, task_001016) this
is cost-savings, not a flip. Worst case regression is one extra short user turn
on a task that truncated once and would have recovered anyway — bounded and
non-blocking. If R6 pass_rate drops below R4 or a passing task visibly thrashes
with the length-recovery nudge in-transcript, revert to `repeat_threshold=2`.

## Round 6 — explicit no-op (harness levers exhausted this batch)

<!-- journal:frontmatter
round: 6
timestamp: 2026-08-23T05:00:00Z
hypothesis_id: h_noop_capability_bound_residual_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=20/50; +2/-4 gained=task_000199_d50c0e57,task_000718_2c9ef3a3 lost=task_000224_82ea7667,task_000603_3ed5cdb5,task_000773_9bc1b20e,task_000897_f4216424; score 0.4000 >= incumbent(mean) 0.4400 - tol 0.0400
expected_global_gain: "0 flips expected — no low-regression harness lever with a plausible flip exists this round; protects the 22/50 baseline from a net-negative bet."
regression_risk: "None — config is byte-for-byte identical to R5 (md5 749ef886ec5fb9521d41ddfc35cc6df6). R5's truncation change is left in place, still under first-round evaluation."
cost_shift: "Zero — no pipeline change."
rollback_trigger: "N/A (no-op). Next round: if R6 (the R5 truncation change) shows a flip on task_000730/task_001028, keep it; if it regressed a passing task, revert repeat_threshold to 2."
-->

### Why

Baseline has been flat at 22/50 (0.44) for R2→R5; the accepted changes each
registered as ±1 run-to-run task swaps, not real movement. I re-swept all 28
R5 failures by `final_pytest.output_tail` and body-read the harness-shaped
candidates. The residual failure set is now overwhelmingly **capability-bound**,
not harness-fixable:

- **Wrong value / wrong logic (done, low steps):** task_000164 (y_pred
  3.44 vs 3.195), task_000718, task_000763, task_000785, task_000925,
  task_000908, task_000199, task_000311, task_000669, task_000438,
  task_001674, task_000032 (crypto), task_000885 (n_components(50)>14 features).
- **Oracle-equivalence reimplementation mismatch (`test_fuzz_equivalence`):**
  task_000506, task_001036, task_001044, task_001074, task_001465 — deep logic
  gaps ("Mismatch on iteration N").
- **Malformed service output:** task_000910 (raw-socket server echoes the full
  `HTTP/1.1 200 OK`+headers into the response body; also fails crontab check),
  task_002108 (JPEG frame 675B < 1000B), task_000348/task_000796 (multi-service
  builds, budget_exceeded).
- **Structural-unfixable:** task_000998 — task-mandated `/home/user/operator.py`
  shadows stdlib `operator`, crashing the verifier's pytest at interpreter
  startup (circular import). Filename is task-required; agent cannot rename it
  and the harness cannot alter the verifier's CWD. Logged as no-fix in R3; still
  stands.

### Changes

- `config.yaml` — copied byte-for-byte from R5 (explicit no-op). md5 verified
  identical. All three meta processors (verifier_dep_guard,
  content_repetition_recovery, command_repetition_recovery) remain pointed at
  their R5-local copies, which exist and import cleanly.

### Evidence — why every constructible candidate was dropped

- **HTTP-framing Instruction/Control candidate (rejected on regression risk):**
  failing server tasks use raw `socket`/`ncat`/`sendall` (task_000910=18×
  `HTTP/1.1 200 OK`, 3× sendall). But `ncat` is *also* used by ≥14 PASSING tasks
  (task_000597=21×, task_000756=23×, task_000716=9×, task_002152 uses Flask+ncat).
  So `ncat`/raw-socket is not the failure discriminator — a "don't use raw
  sockets / body must be payload-only" rule would risk regressing 14 passers for
  at most a partial flip on tasks that also fail a second capability check
  (task_000910 also fails crontab). Retroactive check (A): **no** clean flip.
- **NUL-byte output (task_001036 `_csv.Error: line contains NUL`):** exactly 1
  task across the batch — fails the ≥2-task systemic bar. Idiosyncratic; skip.
- **Missing-output-at-cap (task_000730, task_001016, task_001028):** already the
  target of R5's `LengthTruncationRecoveryProcessor.repeat_threshold 2→1`, which
  is under first-round evaluation this round. Re-touching it now would confound
  attribution.
- **Self-verify / workflow prompt:** already comprehensive (endpoint liveness,
  exact-path `ls`, independent recompute, keep-services-alive). The done-but-wrong
  tasks fail on values the model cannot detect are wrong — a knowledge gap the
  checklist cannot close.
- **Control loop-breakers (R2/R3):** journal already records these nudges armed
  ~0 times in R3/R4 real runs; lever is spent on this cluster.

Per the analyze skill ("a lever tried repeatedly on the same cluster without
flipping any task is a signal to look elsewhere") and SOUL's global-optimization
constraint ("a change that helps one corner case but likely harms global
pass-rate should be rejected by default"), the Pareto-correct action is a no-op:
no available change clears the retroactive check without material regression risk.

### Uncertainty

The main residual risk is that a *genuinely new* harness lever exists that this
sweep missed — but three independent angles (endpoint-framing, output-corruption,
missing-file-at-cap) all resolved to either capability gaps or high-regression
overlaps with passing tasks. If the R6 evaluation of R5's truncation change moves
task_000730/task_001028, that confirms the missing-file lever still has juice and
the next round should press it (e.g. a hard file-write escalation on
`budget_exceeded` trajectories, not just length-truncation). If it does not, the
next productive direction is likely dataset/model-side (capability), not harness.

### needs_from_human / capability gaps (no harness fix, skip)

- The wrong-value, oracle-mismatch, crypto, OCR, and malformed-service clusters
  above are model reasoning/domain capability gaps. No harness mechanism supplies
  the missing method; documented here so future rounds do not re-litigate them.

## Round 7 — explicit no-op + truncation-processor misdiagnosis correction

<!-- journal:frontmatter
round: 7
timestamp: 2026-08-23T12:00:00Z
hypothesis_id: h_noop_capability_bound_residual_v2
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=22/50; score 0.4400 >= incumbent(mean) 0.4400 - tol 0.0400 (final-round scoring)
expected_global_gain: "0 flips expected — no low-regression harness lever with a plausible flip exists this round; protects the ~20-22/50 noise-band baseline from a net-negative bet, and corrects a multi-round measurement artifact that was misdirecting the loop-breaker levers."
regression_risk: "None — config.yaml + system_prompt.txt copied byte-for-byte from R6 (config md5 749ef886ec5fb9521d41ddfc35cc6df6). All three meta processors still point at their R5-local copies, which exist and import cleanly."
cost_shift: "Zero — no pipeline change."
rollback_trigger: "N/A (no-op)."
-->

### Why

Baseline is a stable noise band: R0-R6 = 20,20,21,22,22,22,20 /50, with same-config
repeats swinging 20-22 (see history/OVERVIEW.md). The per-round ±1 "gains" were all
run-to-run task swaps, not signal. Cross-round pass/fail matrix (7 rounds):
**17 SOLVED (stable pass), 22 STUCK (never passed), 11 FRAGILE (flip on noise).**

I re-swept the R6 run from four independent angles and every constructible harness
candidate resolved to a capability gap or a high-regression overlap with passing tasks:

- **STUCK cluster (22 tasks)** — verified capability-bound via final_pytest tails:
  wrong values (task_000164 y_pred, task_000763 vuln PIN 2778≠2394, task_000785 KS-stat,
  task_000925, task_000908, task_000311, task_000438, task_000669/001674 corpus logic),
  oracle-equivalence reimplementation mismatch (task_000506/001036/001044/001074/001465
  "Mismatch on iteration N"), crypto (task_000032), and structural-unfixable
  (task_000998 `/home/user/operator.py` shadows stdlib operator → verifier interpreter
  circular-import crash; filename is task-mandated). All pass the "file exists at exact
  path / valid JSON" checks the R4 workflow prompt already enforces, and fail only the
  "value is correct" assertion — a method the harness cannot supply.
- **FRAGILE cluster (11 tasks)** — regressions in R6 (task_000224 empty PID, task_000603
  density grid, task_000897 chunk parse) are content mismatches from model
  non-determinism on borderline tasks, not harness bugs.
- **Service-death (task_002146)** — the agent's `manager.sh`/`inotifywait` daemon kept
  dying (zombie / exit 137) when backgrounded from inside a Bash tool call, and nginx:8080
  refused at verify time. Per tb2-playbook, nohup/& DO persist into the verifier phase if
  the agent daemonizes correctly — so this is an agent daemonization capability gap
  (setsid/disown), not a harness process-kill bug. task_000348's endpoint was reachable
  (converged_gamma=None is a logic bug), not a service-death case.

### Changes

- `config.yaml` — copied byte-for-byte from R6 (explicit no-op). md5 verified identical
  (749ef886ec5fb9521d41ddfc35cc6df6). `system_prompt.txt` sidecar also copied byte-for-byte
  (md5 914fb4a8dee1fc2f9c52cbca9324035e) so SiblingSystemPromptBuilder resolves it beside
  the active config. Canonicalize: {"ok": true, "checked_templates": 0}.

### Evidence — CORRECTION to a multi-round misdiagnosis

R2/R3/R4/R6 all claimed the loop-breaker / truncation nudges "armed 0 times" based on
grepping the persisted `messages.json` for the nudge text and finding it absent. **That
measurement was an artifact.** The run loop (harnessx/core/runloop.py:729-738) appends the
passive "Your previous response was cut off by the token limit. Please continue…" message
into `state` via `add_raw_message`; `state` is what gets dumped to messages.json.
`LengthTruncationRecoveryProcessor.on_before_model` replaces that last user message with its
corrective nudge only in the **transient `BeforeModelEvent.messages`** (the copy sent to the
model), which is re-assembled fresh from `state` every step and **never written back to
state**. So the corrective nudge is genuinely delivered to the model but is invisible in
the persisted transcript by construction.

Direct proof the processor IS firing on R6 task_001028: the collapsed assistant turns carry
the processor's `[response truncated by harness … repetition loop]` marker (indices 36/40/42/
44/46, each len=1964 after collapse from a 4096-token runaway). The processor fires, collapses
the runaway content, and injects the escalating nudge — exactly as designed. The model then
emits ANOTHER full 4096-token pure-narration turn ("The user is right - I've been repeating
myself endlessly…") with no tool call, and eventually exits via `finished=no_tool_calls`
(empty end-turn giving up). A clean discriminator confirms the pattern: failing truncation-
loop tasks have ~20 no-tool-call assistant turns (task_001016=20) vs 0-2 on passing tasks
(task_000197=1, task_000716=2, task_000948=1).

**Conclusion:** the loop-breaker / truncation-nudge lever family is not broken — it works and
is empirically IGNORED by this model on capability-bound tasks. Shipping another nudge/prompt
variant would be a timid bet that registers as noise and carries nonzero regression risk on
the fragile passers. Per SOUL's global-optimization constraint and the analyze skill's
"a lever tried repeatedly on the same cluster without flipping any task is a signal to look
elsewhere," the Pareto-correct action is a no-op.

### Uncertainty

The residual risk is that a genuinely novel harness lever exists that this sweep missed. But
five independent angles (STUCK value/oracle failures, FRAGILE content-mismatch noise, service
daemonization, compaction interaction, no-tool-call narration burn) all resolved to capability
or high-regression overlap. The single strongest remaining harness idea — force a tool-only
turn / hard-terminate no-tool-call narration loops — is a stronger version of a mechanism the
model has already demonstrated it ignores, so it is not worth the regression exposure on the
fragile passers this round.

### needs_from_human / capability gaps (no harness fix, skip)

- The wrong-value, oracle-mismatch, crypto, OCR/corpus, and daemonization clusters are model
  reasoning/domain capability gaps. Future rounds should treat the loop-breaker/truncation-
  nudge family as SPENT (works, model ignores it) and not re-litigate it via transcript-grep
  false negatives. The productive next direction is model/dataset-side (capability), not harness.
