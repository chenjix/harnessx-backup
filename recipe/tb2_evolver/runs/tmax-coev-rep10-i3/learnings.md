# Tmax coev rep10 i3 — evolve journal

## Round 1 — repeated-command loop guard

<!-- journal:frontmatter
round: 1
timestamp: 2026-01-01T00:00:00Z
hypothesis_id: h_repeated_command_guard_v1
levers: [control]
predicted_affected: [task_000408_0c17d946, task_001716_c1f2ac56, task_000010_644ab1c2, task_000015_89886d8d, task_000118_3043e92d, task_002160_7b255cce, task_000020_eb7b8782]
cited_candidates: [C-001]
gating_outcome: reverted
gating_attribution: score=12/50; +1/-4 gained=task_001716_c1f2ac56 lost=task_000123_aa456f4d,task_000358_4ab35712,task_001035_5a64a9e8,task_001776_abf6bf58; score 0.2400 < incumbent(mean) 0.3000 - tol 0.0400 -> revert to R0
expected_global_gain: "Recover step budget from exact-command thrash loops (6 budget_exceeded + long done-fails); redirect the model mid-loop toward a different approach"
regression_risk: "Passing runs with high exact-repeat counts (task_000123 echo x16, task_000358 x4, task_001035 x5) receive an extra nudge line; nudge-only + append-only so cannot un-do already-correct work"
cost_shift: "Slightly negative overall — ~90 tokens per fire, but breaks 10-22 step loops early, reducing total steps/tokens on pathological runs"
rollback_trigger: "R1 pass-rate < 15 (R0 baseline) OR any of task_000123/task_000358/task_001035 flips T->F"
-->

### Why

R0 = 15/50 (30%). Failure taxonomy: **29/35 failures exit
`no_tool_calls`=`done`** (agent voluntarily stops) and only 6 hit
`budget_exceeded`. The `CustomSelfVerifyProcessor` already fires on
28/29 of the `done`-fails AND on every passing run, so the one-shot
verify nudge is already present — most `done`-fails are semantically
wrong output that passes an existence check, i.e. **model capability
gaps, not harness deficiencies** (no harness fix — skip). The one
genuinely harness-addressable, cross-task cluster is **exact-command
thrash**: runs that re-issue the *literally identical* Bash command
many times without any state change, burning the whole 80-step budget.
The existing `CustomEditToolProcessor` only counts file-WRITE commands
(redirect/sed -i/tee) and is blind to read/poll/debug loops.

### Changes

- `processors/repeated_command_guard.py` — new `RepeatedCommandGuard`
  `MultiHookProcessor`: counts exact-identical Bash command executions
  per task; at threshold (5) appends a one-shot "you are looping, take
  a fundamentally different action" nudge to that tool's result, then
  resets the counter. Append-only, contract-clean, never blocks.
- `config.yaml` — register the processor after `CustomEditToolProcessor`
  (`_order=31`), `threshold: 5`.

### Evidence

- `task_000408_0c17d946`: `exit_reason=budget_exceeded`; command
  `debugfs /home/user/drive.img 2>&1 << 'EOF'\nls -l /\nEOF` executed
  **17 times** (exact Counter over full command strings). Died at
  step 80 with no fix.
- `task_001716_c1f2ac56`: `budget_exceeded`; the same
  `python3 -c "...struct...PIL.Image..."` inline script re-run **22
  times** (steps 0–21) before dying at step 80 with no `repo.pack`.
- `task_000010_644ab1c2`: done-fail, 76 steps; `sleep 10\nps aux |
  grep -E "mock_api|socat|python"` x10 — polling a process that never
  started; deliverable `/home/user/operator.py` missing.
- `task_000015_89886d8d`: done-fail, 54 steps; `cd /home/user &&
  python3 -m pytest test_parser.py` x10 — re-running the same failing
  test without changing the fix.
- Caveat (regression scope): passing runs also show high repeats —
  `task_000123` `echo "Task completed successfully!"` x16 (passed),
  `task_001035` x5, `task_000358` x4 — hence exact-match + high
  threshold + nudge-only design so these are not harmed.

### Uncertainty

The exact-repeat signal is present in both failing and passing runs,
so the guard is deliberately scoped to nudge (not block) and to fire
only at a high count. Risk: the nudge lands but the model still can't
find the different approach (the underlying task may be a capability
gap) — in that case the round is flat, not negative. If pass-rate
drops or a cited passing task regresses, revert (see rollback_trigger).

## Round 3 — verifier requests dep primer

<!-- journal:frontmatter
round: 3
timestamp: 2026-08-21T02:39:19Z
hypothesis_id: h_verifier_requests_dep_v1
levers: [action]
predicted_affected: [task_000020, task_000028, task_000661, task_000689, task_000958, task_002160, task_000010]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=13/50; +3/-2 gained=task_000029_80a5350d,task_001035_5a64a9e8,task_001832_dd672877 lost=task_000185_46261088,task_000212_46a770d4; score 0.2600 >= incumbent(mean) 0.2700 - tol 0.0400
expected_global_gain: "Flips up to 6 HTTP/service tasks whose verifier import requests hits rc=2 ModuleNotFoundError and forces reward 0 despite correct solutions"
regression_risk: "Extra one-shot pip install tool-call appended to first turn of all 50 tasks; on the 12 passing tasks it is an idempotent <1s no-op but adds a 2nd tool_call/result to turn 0"
cost_shift: "+1 tool call/task, negligible tokens (~<1s already-satisfied on most)"
rollback_trigger: "If global pass-rate drops vs R0 (15/50), or any smoke/task errors on the injected turn-0 tool call, revert to R0"
-->

### Why

A distinct cluster of HTTP/service tasks scored 0 in R2 not from bad
solutions but because the external verifier's pytest module does
`import requests`, and `requests` is absent from the task container →
pytest rc=2 `ModuleNotFoundError: No module named 'requests'` at
collection time → automatic reward 0. The agents built the server in
C/Go/Node or Python stdlib and probed with `curl`, so they never had a
reason to install the Python client. This is a verifier-side environment
gap the harness can close, not a model reasoning error.

### Changes

- `processors/verifier_dep_primer.py` — new `VerifierDepPrimerProcessor`
  (MultiHookProcessor). On the first model turn per task, appends one real
  idempotent Bash `ToolCall` (`python3 -m pip install --quiet ... requests
  || true`) to `event.tool_calls`; resets on task_start/task_end. Fires
  exactly once/task, never replaces the model's own first action.
- `config.yaml` — copied R0, registered the new processor under `processors`
  with an absolute `file://` target; system_prompt.txt copied byte-for-byte
  for SiblingSystemPromptBuilder resolution.

### Evidence

- rc=2 `ModuleNotFoundError: No module named 'requests'` in R2 trajectories
  for tasks 000020, 000028, 000661, 000689, 000958, 002160 (000010 is a
  circular-import variant of the same shape). All HTTP/service tasks.
- pip is reachable in these containers: passing tasks show flask, certifi,
  charset_normalizer installing successfully → `pip install requests` will
  succeed and persist into the verifier phase.
- Runloop-verified injection path: `on_after_model` output replaces
  `model_event` (runloop L438), recorded as assistant message with its
  tool_calls (L479), and each tool_call executed with id-matched results
  (L497+) → provider-safe N calls / N results.

### Uncertainty

Two open risks. (1) `requests` may not be the only missing verifier dep —
if a task's test imports another lib, that task stays at 0; the fix is
scoped to the observed cluster. (2) The turn-0 injection touches all 50
tasks; the 12 passers should see only a fast idempotent no-op, but if any
turn-0 flow is disrupted or pass-rate drops vs R0, revert.

## Round 4 — verifier CLI tool primer

<!-- journal:frontmatter
round: 4
timestamp: 2026-08-21T03:26:41Z
hypothesis_id: h_verifier_cli_primer_v1
levers: [control]
predicted_affected: [task_001046_eccbf294, task_000300_7d6b511c]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=17/50; +6/-2 gained=task_000020_eb7b8782,task_000028_7fe033ac,task_000212_46a770d4,task_000661_f26aec97 lost=task_001035_5a64a9e8,task_001832_dd672877; score 0.3400 >= incumbent(mean) 0.2700 - tol 0.0400
expected_global_gain: "Closes the verifier-shells-out-to-absent-CLI failure class (FileNotFoundError: 'curl'/'ss') — the OS-CLI sibling of the R3 requests pip primer that was accepted; flips at least task_001046 (clean single-test curl failure) and unblocks the ss test in task_000300"
regression_risk: "Idempotent command-v guard makes it a no-op on the ~48 tasks where curl/ss already exist; timeout 90 + || true means it cannot stall or fail the turn; writes only to system dirs, never task output paths"
cost_shift: "+1 turn-0 tool call/task; sub-second no-op on tasks that already have the tools, one bounded (<=90s) apt fetch on the two that don't; output suppressed so negligible tokens"
rollback_trigger: "Revert to R3 if global pass-rate < R3 (13/50), OR synthetic replay errors on the injected turn-0 call, OR any currently-passing task flips T->F"
-->

### Why

A recurring structural failure shape appears in R3 trajectories that is not a
model reasoning error: the external verifier runs in a separate phase after the
agent exits and several tests shell out to a standard networking CLI tool via
`subprocess.run(["curl", ...])` / `subprocess.run(["ss", ...])`. Those binaries
are absent from the base image, so the test raises `FileNotFoundError` at call
time and the task scores 0 regardless of the correctness of the agent's work.
The agent cannot pre-empt this: during its own phase it hits
`curl: command not found`, works around it with wget/python, and correctly
concludes it does not need curl for its phase — only the hidden verifier does.
This is exactly the structural class of the R3 `requests` pip primer (accepted,
flipped 3 tasks), one layer down: OS CLI layer instead of the Python-import
layer.

### Changes

- `processors/verifier_cli_primer.py` — new `VerifierCliPrimerProcessor`
  (MultiHookProcessor). On the first model turn per task, appends ONE Bash
  `ToolCall` that (guarded by `command -v curl && command -v ss`) does a
  quiet, `timeout 90`-bounded, `|| true` best-effort
  `apt-get update && apt-get install -y --no-install-recommends curl iproute2`.
  Fires once per task; resets on task_start/task_end; never replaces the
  model's own first action.
- `config.yaml` — copied R3 byte-for-byte, registered the new processor at
  `_order=33` (just after the R3 pip primer at 32), `apt_packages: curl iproute2`.
  `system_prompt.txt` copied byte-for-byte for SiblingSystemPromptBuilder.

### Evidence

- `task_001046_eccbf294` result.json `final_pytest.output_tail`: full CPython
  `subprocess.py:1863` traceback ending
  `FileNotFoundError: [Errno 2] No such file or directory: 'curl'` on
  `test_end_to_end_pipeline`; `1 failed, 4 passed` (curl is the ONLY blocker).
- `task_001046_eccbf294` messages.json: agent's Bash returned
  `bash: line 1: curl: command not found (exit 127)`; assistant reasoned
  "curl is not available. Let me try with wget or python." → binary genuinely
  absent in-container; agent worked around it but the verifier cannot.
- `task_000300_7d6b511c` result.json `final_pytest.output_tail`: identical
  `subprocess.py:1863` traceback ending
  `FileNotFoundError: [Errno 2] No such file or directory: 'ss'` on
  `test_haproxy_running_and_listening` (`ss` = iproute2).

### Uncertainty

Two open risks. (1) apt may require network access that is blocked in the
agent container — if so the install is a bounded no-op and the two tasks stay
red (flat round, not negative). The R3 pip primer being accepted implies a
usable package channel exists, but apt is a different transport than pip.
(2) task_000300 has two further independent failing tests (an AssertionError
and a backend-not-running check) that are likely capability gaps, so the `ss`
fix is necessary-not-sufficient there; a confident flip is only claimed for
task_001046. If pass-rate drops vs R3 or a passing task regresses, revert.

## Round 5 — no-progress repeat breaker

<!-- journal:frontmatter
round: 5
timestamp: 2026-05-02T00:00:00Z
hypothesis_id: h_noprogress_repeat_breaker_v1
levers: [control]
predicted_affected: [task_000118, task_000358, task_000408, task_000686, task_001035, task_001017]
cited_candidates: [C-001]
gating_outcome: reverted
gating_attribution: score=14/50; +1/-4 gained=task_001035_5a64a9e8 lost=task_000020_eb7b8782,task_000028_7fe033ac,task_000661_f26aec97,task_001776_abf6bf58; score 0.2800 < incumbent(mean) 0.3400 - tol 0.0400 -> revert to R4
expected_global_gain: "Reclaim step/wall-clock budget on a ~6-task zero-progress death-spiral cluster and cap the worst cost outlier (001017, 3830s); redirects the model off identical-command loops so it can attempt a different action within budget."
regression_risk: "A too-aggressive block could refuse a legitimately-repeated command; mitigated by thresholds set strictly above the passing ceiling (max passer = 3 consecutive identical pairs) and by disarm-on-any-different-command. R4 sim: 0 passers touched."
cost_shift: "Strongly negative — fewer wasted identical re-executions; timeout-streak block (thr=3) caps repeated exit-124 brute-force that burned ~3800s in 001017."
rollback_trigger: "If R5 pass_rate < R4 (17/50) OR any previously-passing task regresses to F with the breaker firing on it (check step_snapshots for [NoProgressRepeatBreaker] BLOCKED on a reward=1->0 task), revert."
-->

### Why

A dominant, harness-addressable pathology in the R4 trajectories is the agent
re-issuing the *exact same* Bash command and getting back the *exact same*
result many times in a row — zero progress until the step budget is exhausted
(`budget_exceeded`) or, worst case, the run crashes (`error`) after the same
timed-out command is re-run a dozen+ times. This is a control gap: the harness
never intercepts the loop. R3/R4 nudge-only guards (EditDetection) were ignored
by the model in `task_001017`. The reverted R1 guard (`h_repeated_command_guard_v1`)
over-fired because it counted *global* command occurrences and swept up benign
echo-spam on passing runs; this round uses a strictly safer signal.

### Changes

- `processors/noprogress_repeat_breaker.py` — new `NoProgressRepeatBreaker`
  (`MultiHookProcessor`): two-stage circuit breaker on consecutive identical
  (normalized command, normalized result) Bash pairs. Stage 1 soft nudge at
  `soft_threshold=4`; stage 2 hard block (`approved=False` + `synthetic_result`)
  at `hard_threshold=6`; separate consecutive-timeout streak blocks repeated
  `exit 124` of the same command at the lower `timeout_block_threshold=3`.
  `_norm_result` strips sibling-processor advisory suffixes so intermittent
  nudges don't break the identical-result match.
- `config.yaml` — copied R4 byte-for-byte, registered the new processor at
  `_order=32` (just after `CustomEditToolProcessor` at 30), before
  `CustomSelfVerifyProcessor`. Kept R3/R4 primers and `apt_packages` intact.
  `system_prompt.txt` copied byte-for-byte for SiblingSystemPromptBuilder.

### Evidence

- Simulation over all R4 trajectories: breaker fires only on reward=0 tasks —
  000015, 000118, 000185, 000358, 000408, 000686, 001017, 001035, 001229.
  **Zero passing tasks touched.**
- No R4 passing task exceeds 3 consecutive identical (cmd,result) pairs
  (max passer = task_000020 at 3). Failing budget_exceeded tasks reach
  5/6/10/19/24 → thresholds (4/6) sit strictly above the passing ceiling.
- `task_001017_9f6adf16`: `exit_reason=error`, ~3830s wall-clock, 15 consecutive
  `(exit 124, ...)` timeouts of a brute-force compile/run; timeout-streak block
  at 3 caps this at 9 refusals in sim.
- Block counts in sim: 000118 (20), 000686 (24), 000358 (18), 000408 (15),
  001035 (15) — the death-spiral cluster.

### Uncertainty

The retroactive sim shows zero passer impact, but blocking changes the live
trajectory: a redirected model may or may not find a productive next action
within the reclaimed budget (some of these are also genuine capability gaps —
scientific_computing, namedtuple circular-import — so a budget flip is not
guaranteed). The safe claim is (a) zero regression on passers and (b) a large
cost/wall-clock reduction on the thrash cluster; pass-rate upside is plausible
but bounded by the underlying capability gaps. If pass-rate drops or a passer
regresses with the breaker firing on it, revert.

## Round 7 — source-scrub refactor discipline

<!-- journal:frontmatter
round: 7
timestamp: 2026-08-21T05:00:00Z
hypothesis_id: h_source_scrub_refactor_v1
levers: [instruction]
predicted_affected: [task_000059_e4b842f5, task_000431_fabf4ad3]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=15/50; +4/-6 gained=task_000020_eb7b8782,task_000431_fabf4ad3,task_001653_c4cafa73,task_001832_dd672877 lost=task_000028_7fe033ac,task_000123_aa456f4d,task_000185_46261088,task_000212_46a770d4; score 0.3000 >= incumbent(mean) 0.3200 - tol 0.0400
expected_global_gain: "Flips the naive-substring-check cluster: verifier does `assert '<old_token>' not in source` and the agent's own explanatory comment echoes the removed token. Two tasks (SWE + debugging) had functionally-correct fixes and failed ONLY that test. Generalizes to any unseen refactor/migration/removal task under the same TB2 verifier idiom."
regression_risk: "Adds ~4 lines to the 5-line shared system prompt across all 50 tasks. Narrowly scoped to removal/replacement refactors; could in principle nudge an agent to strip a legitimately-required comment, but the rule targets only the *removed* token. No currently-passing task relies on echoing a removed token in a comment."
cost_shift: "Negligible: ~40 extra prompt tokens/task, no extra tool calls. May slightly reduce cost on the two targets by giving a clean pass instead of a wasted verify loop."
rollback_trigger: "Revert to R4 if global pass-rate < 17/50, OR if either task_000059/task_000431 fails to flip AND any currently-passing task regresses T->F."
-->

### Why

R6 (incumbent R4 config) = 17/50. Failure taxonomy: 7 budget_exceeded
(one pure exact-command loop task_000408 x36; rest varied iterative
debugging = capability gaps), 25 done-fails (mostly wrong algorithm =
capability gaps), 1 error. The exact-loop Control shape was reverted
twice (R1, R5) — not re-proposed. Within the done cluster, one small,
CLEAN, harness-addressable sub-cluster stands out: the external
verifier does a naive `assert "<old_token>" not in file_content`
substring check, and the agent's own explanatory comment echoes the
token it was asked to remove — failing an otherwise-correct fix. This
is an awareness gap (Instruction), not a missing capability (Action)
or a mechanical hook (Control — a processor cannot know which token a
post-agent-phase verifier will grep for, and rewriting the agent's
comments would destructively edit correct code).

### Changes

- `system_prompt.txt` (sibling of config.yaml, read by
  SiblingSystemPromptBuilder) — appended a general source-hygiene rule:
  when asked to remove/replace/migrate a construct, remove it from the
  whole file (comments/docstrings/notes included), describe the change
  in the chat reply not inside the edited file, and grep the final file
  for the old token to confirm it is gone before stopping. No task
  literals embedded.
- `config.yaml` — copied R4 byte-for-byte (all processor `file://`
  targets unchanged and still resolve); the only functional change is
  the sibling prompt.

### Evidence

- `task_000059_e4b842f5` result.json final_pytest: `3 passed, 1 failed`;
  sole failure `test_verify_mac_py_python3_compatible` traceback:
  `'xrange' is contained here: (replaces xrange)` — token survives ONLY
  in the agent's comment `# (replaces xrange)`; the Py2->Py3 code
  migration itself was correct.
- `task_000431_fabf4ad3` result.json final_pytest: `2 passed, 1 failed`;
  sole failure `test_go_source_fixes` traceback: `'len(lines)-1' is
  contained here: ... changed i < len(lines)-1 to i < len(lines) ...` —
  token survives ONLY in the agent's fix-describing comment; the actual
  loop fix, `close(results)`, and EWMA fix were all correct.
- Regex sweep over all 33 failing trajectories: exactly these two tasks
  carry a "NOT-IN-SOURCE" assertion — a real 2-task, 2-domain cluster.

### Uncertainty

The two targets each had the substring test as the ONLY failing test,
so retroactive check A is `yes` — a clean comment + confirming grep
flips both. Risk is that the model ignores the prompt guidance under
its own habit of annotating changes; then the round is flat, not
negative. Instruction lever is untried on this run (neutral posterior).
If pass-rate drops or a passer regresses, revert to R4.

## Round 8 — deliverable-path fidelity

<!-- journal:frontmatter
round: 8
timestamp: 2026-08-21T14:53:26Z
hypothesis_id: h_deliverable_path_fidelity_v1
levers: [instruction]
predicted_affected: [task_000010_644ab1c2]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=14/50; +4/-5 gained=task_000123_aa456f4d,task_000185_46261088,task_000689_a143f1c9,task_001046_eccbf294 lost=task_000029_80a5350d,task_000431_fabf4ad3,task_001653_c4cafa73,task_001716_c1f2ac56; score 0.2800 >= incumbent(mean) 0.3120 - tol 0.0400
expected_global_gain: "Flips the 'renamed/relocated deliverable -> exact-path existence gate fails' class. TB2 verifiers front-load os.path.isfile(<exact path>)/executable-at-exact-path gates; any unseen task naming an explicit output path benefits from 'path is authoritative + confirm before stop'."
regression_risk: "~30 extra prompt tokens on the shared prompt across all 50 tasks; rule only tells the agent to honor the path the task itself states and to verify it. No passing task relies on renaming/relocating a deliverable — passers already honor exact paths (task_000041/000577/000911)."
cost_shift: "Negligible (+~30 prompt tokens/task, no extra tool calls); may reduce cost on affected tasks by turning a wasted verify loop into a clean pass."
rollback_trigger: "Revert to R7 if global pass-rate < 15/50 (R7 baseline), OR task_000010 fails to flip AND any currently-passing task regresses T->F."
-->

### Why

R7 = 15/50 (incumbent R4 config, mean ~16). Failure taxonomy on R7
trajectories: 24 done-fails (mostly wrong-algorithm capability gaps),
9 budget_exceeded (varied iterative debugging; the exact-command loop
Control shape was reverted twice — R1, R5 — not re-proposed), 2 error.
Within the done cluster one small, CLEAN, harness-addressable case
stands out that is NOT a capability gap: task_000010's agent produced a
complete, working script but wrote it to the WRONG filename. The task
requires `/home/user/operator.py`; the agent deliberately renamed it to
`k8s_operator.py` "to avoid conflict with Python's standard library
`operator` module", re-read the task, PROBED and SAW the required file
was missing, and still chose to keep the renamed file. This is an
awareness/judgement gap (Instruction), not a missing capability (Action)
or a mechanical hook (Control — the verifier's isfile() target paths do
not exist during the agent phase, so a processor cannot know which paths
to check, and auto-renaming files risks clobbering correct work on the
other 49 tasks). The playbook flags "correct logic, wrong path" as a top
structural failure class.

### Changes

- `system_prompt.txt` (sibling of config.yaml, read by
  SiblingSystemPromptBuilder) — prepended a general deliverable-fidelity
  rule: the task's stated output path/name is authoritative; never rename
  or relocate a required deliverable on your own judgement (resolve name
  clashes internally instead); before stopping, `ls` each explicitly named
  output path to confirm it exists at the exact location. Kept the R7
  source-hygiene rule verbatim. No task literals embedded.
- `config.yaml` — copied R7 byte-for-byte (all processor file:// targets
  unchanged and still resolve); the only functional change is the sibling
  prompt.

### Evidence

- `task_000010_644ab1c2` result.json final_pytest: `1 failed, 2 passed`;
  sole failure `test_operator_script_exists`:
  `AssertionError: Operator script /home/user/operator.py does not exist.
  You must create it.` — the exact-path existence gate is the ONLY blocker.
- `task_000010` messages.json: agent reasoning "The task says the script
  should be at `/home/user/operator.py`, but I renamed it to
  `/home/user/k8s_operator.py` to avoid the conflict with Python's standard
  library `operator` module."; its probe returned `operator.py not found at
  /home/user/operator.py -rwxr-xr-x ... /home/user/k8s_operator.py`; final
  message "The task is complete. The script is at `/home/user/k8s_operator.py`
  (renamed ...)". Functional work (backup, port-forward, manifest apply) was
  done — only the filename deviated.
- Regression baseline (passers already honor exact paths): task_000041
  writes `/home/user/filter_billing.py` + `/home/user/billing_backup.tar.gz`;
  task_000577 writes `/home/user/recovered_evidence.txt`; task_000911 writes
  `/home/user/enforce_policy.py`. The rule codifies a habit passers already
  have.

### Uncertainty

The acute flip is confidently a single task (task_000010) — the "renamed
deliverable" symptom is idiosyncratically acute there, though the exact-path
existence gate is a verifier-wide idiom (task_000212 also opens with an
executable-at-exact-path gate, but its root cause is a reverse-engineering
capability gap, so it is not claimed as a flip). Instruction lever has
landed once this run (R7 accepted). Risk: the model ignores the rule under
its own habit of renaming to dodge name clashes, in which case the round is
flat, not negative — the rule cannot make a passer write to a wrong path.
If pass-rate drops or a passer regresses, revert to R7.

## Round 9 — explicit no-op (loop-guard family exhausted)

<!-- journal:frontmatter
round: 9
timestamp: 2026-08-21T16:00:00Z
hypothesis_id: h_noop_v1
levers: []
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=17/50; score 0.3400 >= incumbent(mean) 0.3167 - tol 0.0400 (final-round scoring)
expected_global_gain: "None claimed — no positive-EV, low-regression harness fix is supported by the R8 evidence; the incumbent (R4 processors + R7/R8 accepted prompt) is preserved byte-for-byte."
regression_risk: "None — config and system_prompt.txt are byte-identical to R8."
cost_shift: "Zero."
rollback_trigger: "N/A (no-op)."
-->

### Why

R8 = 14/50 (0.28). Incumbent is R4's processor pipeline + the R7
source-scrub and R8 deliverable-fidelity prompt rules (both accepted).
Failure taxonomy on the R8 trajectories: 24 done-fails, 10
budget_exceeded, 2 error. I swept every failing trajectory and
confirmed the clean, cross-task, harness-addressable structural
clusters have already been harvested by prior rounds (R3 verifier
requests primer, R4 verifier CLI primer, R7 source-scrub, R8
deliverable-path). What remains is dominated by wrong-algorithm /
wrong-content capability gaps (e.g. task_000029 `rolling_change_count
3 != 1`; task_000958 query result `[3,4]` vs `[3,2,1]`; task_002160
HTTP 401 vs 200; task_000015/000358 numeric-accuracy asserts) — out of
scope per SOUL ("model capability gaps — NOT the harness's job").

The only remaining *structural* signal was a degenerate repetition
loop: two tasks exited `error` (task_001653, task_000358) after the
model emitted a byte-identical assistant message + re-ran a
same-effect Bash write 30x / 10x with no state change, plus several
budget_exceeded cost outliers (task_001017 1738s, task_001229 1226s,
task_000661 1153s, task_001716 1139s) driven by the same shape. A
loop-breaker keyed on identical-assistant-message content was the
candidate intervention.

### Changes

- `config.yaml` — copied R8 byte-for-byte (diff = empty).
- `system_prompt.txt` — copied R8 byte-for-byte (diff = empty).

### Evidence

- Repetition-loop discriminator is UNSAFE: a scan of consecutive
  identical assistant-message runs over all 50 R8 trajectories shows
  the signal fires on PASSING tasks too — `task_000758_92ca434c`
  (reward=1) emitted "Let me try a different approach..." **26 times
  consecutively** while its tool calls varied and it ultimately
  passed; `task_001046_eccbf294` (reward=1) hit 6. Any guard on this
  signal reproduces exactly the over-fire that got the R1 command-
  count guard (`h_repeated_command_guard_v1`) and R5 (cmd,result)-pair
  breaker (`h_noprogress_repeat_breaker_v1`) reverted.
- Retroactive check (Variant A) on the two `error` tasks is `no`:
  task_001653 (`is_na_double` C bug) and task_000358 (moving-avg/std
  window logic) are correctness/capability gaps — breaking the loop
  reclaims budget/prevents the crash but does NOT flip them to pass.
- R6→R8 lost 5 / gained 2 on a 17 baseline, but every lost task
  (000028, 000029, 000212, 000661, 001716) failed on independent
  content/logic asserts unrelated to the prompt; combined with the R4
  repeat spread (17,17,15,15,14) this is run-to-run noise, not a
  systematic prompt regression — so reverting the R7/R8 prompt is not
  evidence-supported either.

### Uncertainty

Risk of the no-op is opportunity cost: if a future round finds a
genuinely different discriminator that separates stuck-loop failures
from productive-repeat passers (e.g. identical (assistant text AND
tool result AND no filesystem delta) simultaneously, or context-token
growth with zero test-state change), a bounded circuit-breaker
converting `error` crashes into graceful stops could be worth ~2
cost-outlier tasks — but only as a cost/stability play, not a pass
flip. Shipping the naive version this round would be a high-regression
local bet with a `no` retroactive check, which the Pareto rule says to
reject.
