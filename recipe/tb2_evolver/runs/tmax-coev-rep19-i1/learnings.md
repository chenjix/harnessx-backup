# Evolve journal — tmax-coev-rep19-i1

## Round 1 — content-based loop breaker

<!-- journal:frontmatter
round: 1
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_repetition_loop_breaker_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_001321_658ce4a8, task_000118_3043e92d, task_000578_cebe85a5, task_001818_b251e5ea]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=27/50; +4/-6 gained=task_000578_cebe85a5,task_001090_c61c71f2,task_001652_86e1d185,task_001818_b251e5ea lost=task_000396_e56917e2,task_000684_1a33ef37,task_000760_76ba653c,task_000912_770802f8; gating disabled (tolerance < 0)
expected_global_gain: "Frees the 6-task budget_exceeded@80-step cluster from degenerate self-repetition loops so each can converge instead of burning the whole budget"
regression_risk: "A false-positive prune on a legitimate multi-turn retry sequence; mitigated by requiring 3 consecutive near-identical (content prefix + identical tool signature) turns, a shape healthy runs don't produce"
cost_shift: "Net negative — loops that run to 80 steps get cut short; pruning also shrinks the breaking-turn context. No added cost on non-looping tasks."
rollback_trigger: "If R2 pass_rate is flat/down AND any previously-passing task regresses to a truncated/interrupted exit, revert."
-->

### Why

Assigned focus `task_000010_644ab1c2` (system_administration) fails with
`exit_reason=budget_exceeded` at the 80-step cap after 889s. The task requires
writing `/home/user/operator.py`; running Python with cwd=`/home/user` makes the
file shadow the stdlib `operator` module and breaks the interpreter. The model
correctly diagnosed this but then entered a degenerate loop: ~15 near-verbatim
"I've been stuck ... circular import ... only real solution is to rename the
file, but the user asked for /home/user/operator.py" turns, interleaved with 16
passive "cut off by the token limit" continue nudges. It never tried the actual
fix (run Python from another cwd / set PYTHONPATH) because its own runaway prose
kept re-priming the identical completion. Six tasks total die this way at
exactly steps=80. The existing `LengthTruncationRecoveryProcessor` only fires on
`finish_reason=length AND no tool_calls` and its nudge text never appears in any
trajectory (`proc_nudge=0`); a soft one-line nudge cannot outweigh the wall of
repeated prior turns. Critically, `task_001321_658ce4a8` re-issues a
**byte-identical Bash command 33 times** — a prior-rep loop breaker that resets
on any tool call would never catch it.

### Changes

- `processors/repetition_loop_breaker.py` — new `RepetitionLoopBreaker`
  `MultiHookProcessor`. Fingerprints each assistant turn as
  (normalised content prefix + tool-call name/input signature); on 3 consecutive
  matching turns it prunes the duplicate looping turns (plus their paired tool
  results and passive nudges) from the assembled context in `on_before_model`
  and appends ONE decisive "abandon this line, take a different concrete step"
  redirect. Content-agnostic — names no task, path, command, or constant.
- `config.yaml` — register the processor after `LengthTruncationRecoveryProcessor`
  (order 6) and before compaction.

### Evidence

- `task_000010_644ab1c2` frontmatter: `exit_reason=budget_exceeded`, steps=80,
  elapsed=889s. Message log: 16 passive "cut off by the token limit" nudges,
  each preceded by a tool-less assistant turn repeating the same "rename the
  file" analysis (~15 near-identical turns).
- `task_001321_658ce4a8`: 33 assistant tool-call turns, all issuing 1 distinct
  (byte-identical) command; narration repeats "I've been stuck in a loop. Let me
  take a completely different approach" verbatim.
- Cluster shape: `task_000118_3043e92d`, `task_000578_cebe85a5`,
  `task_001818_b251e5ea` all `budget_exceeded` at steps=80 with tool calls each
  turn (evade the length-only recovery).

### Uncertainty

The breaker guarantees the loop stops and the budget is reclaimed for fresh
attempts, but it does not inject the missing solution knowledge (e.g. the cwd /
PYTHONPATH trick for task_000010) — that is a model capability. So a flip is
plausible but not certain; the sure win is eliminating the 0-progress budget
burn. If the redirect proves too aggressive and prunes a legitimate retry chain,
a previously-passing task would truncate — watch for T→F regressions with
truncated exits and revert if seen alongside a flat/down pass_rate.

## Round 2 — verifier-runtime readiness

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_verifier_runtime_readiness_v1
levers: [instruction]
predicted_affected: [task_000028_7fe033ac, task_000958_4bb2b05d]
cited_candidates: [C-201]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips the missing-verifier-dependency cluster (HTTP-service tasks whose pytest verifier does `import requests` against an absent package) and generalizes to any future externally-HTTP-verified task where the test client is not installed"
regression_risk: "Low — guidance gated on the task explicitly describing an external HTTP verifier; worst case one redundant pip install (fast warm-cache no-op) on service tasks that already have requests. No effect on non-service tasks."
cost_shift: "Negligible — at most one extra Bash import-probe + optional install on HTTP-service tasks; zero elsewhere."
rollback_trigger: "If R3 shows a previously-passing HTTP task regressing (extra install hangs/errors and burns budget) alongside flat/down pass_rate, revert the prompt addition."
-->

### Why

Assigned focus `task_000028_7fe033ac` fails with reward=0 despite a
functionally correct solution: the agent's Nginx→C++ UNIX-socket
microservice returns `HTTP/1.1 200 OK` with the frame count `150` end to end
(msg [70]). The zero comes entirely from the verifier phase — `pytest`
collecting `/tmp/test_final_state.py` aborts with
`ModuleNotFoundError: No module named 'requests'` because the verifier drives
its HTTP checks through the `requests` package, which is not installed in the
container's Python. `task_000958_4bb2b05d` (C++ SQLite HTTP microservice)
fails identically. Both passing HTTP tasks in the set
(`task_000206_a943669b`, `task_001498_df8254c9`) had verifiers that did not
import `requests`, so the discriminator is purely the missing dependency, not
solution quality. The container supports offline pip installs (warm-cache
logic in `benchmarks/terminal_bench_2/dind_environment.py`), so the agents
could have made `requests` importable — nothing told them the verifier
depended on it.

### Changes

- `system_prompt.txt` — appended one generalized "verifier readiness"
  paragraph: when the task states an external/automated verifier will make
  HTTP requests to a service you run, confirm the standard Python HTTP client
  (`requests`) is importable in the tests' interpreter and install it if
  missing. No task id, path, port, or constant embedded.
- `config.yaml` — byte-identical processor pipeline to R1; the only effective
  delta is the new sidecar prompt read by `SiblingSystemPromptBuilder`.

### Evidence

- `task_000028_7fe033ac` `result.json`: `reward=0`, `exit_reason=done`,
  `steps=54`; `final_pytest.output_tail`:
  `import requests → ModuleNotFoundError: No module named 'requests'`
  (`Interrupted: 1 error during collection`). Solution verified working:
  msg [53]/[70] `HTTP/1.1 200 OK` body `150`.
- `task_000958_4bb2b05d` `result.json`: `reward=0`, `exit_reason=done`,
  `steps=64`; same `import requests` collection crash at
  `/tmp/test_final_state.py:4`.
- Contrast passers: `task_000206_a943669b` `2 passed`,
  `task_001498_df8254c9` `4 passed` — verifiers did not import `requests`.

### Uncertainty

Instruction lever depends on model compliance: the agent must (a) recognize
the task as externally HTTP-verified and (b) actually run the import
probe/install. If it ignores the paragraph the cluster stays failed but
nothing regresses (config pipeline unchanged). A flip is plausible, not
certain. If the install step ever hangs and burns budget on a passing task,
that is the rollback signal.

## Round 2 — OCR quality advisor

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_ocr_quality_advisor_v1
levers: [control]
predicted_affected: [task_000015_89886d8d, task_000505_50b5162d]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the garbled-OCR->wrong-artifact failing cluster (000015 schema mapping @0.34 accuracy, 000505 mis-read SSH key); generalizes to any unseen task needing structured OCR (7 OCR tasks this round)"
regression_risk: "Near zero — fires at most once per task, only appends to the first OCR command's tool result (contract-safe), inert on all non-OCR tasks and on the ~5 passing OCR tasks"
cost_shift: "Negligible/positive — one ~200-token advisory only on tasks that invoke OCR; expected to REDUCE wasted tokens from the 6+ failed OCR retries the agent currently thrashes through"
rollback_trigger: "If R2 pass_rate flat/down AND neither 000015 nor 000505 flips, revert (advisory added surface without changing outcome)"
-->

### Why

Assigned focus `task_000015_89886d8d` (software_engineering) exits `done` but
fails the verifier with "Accuracy metric 0.3411 is below the 0.98 threshold".
The task requires OCRing `/app/routing_schema.png` (a small 800x400 image) to
extract URL->JSON route mappings, then building `migrate.py` against a hidden
2000-URL edge-case dataset. The agent ran `tesseract <img> stdout` ~6 times —
every run emitted `Warning: Invalid resolution 0 dpi. Using 70 instead` and
returned garbled text ("atalogyitem <item _id>Mept=<depariment>..."). It tried
contrast/threshold/whitelist tweaks but never applied the standard fix
(upscale the image, force `--dpi 300`, try `--psm` modes). It then abandoned
the image and *inferred* the schema from the 3 visible sample URLs, hard-coding
3 routes that scored 0.34 on the hidden set. The SAME root cause fails
`task_000505_50b5162d`: OCR mis-read an SSH key's base64 (`IZDIINTES` for
`lZDI1NTE5`, spurious spaces/`|`) so the trojan detector never matched — "2 of
2 evil bypassed". Two distinct tasks, one mechanism: naive OCR on a small image
produces garbage the agent trusts or works around. The existing loop breakers
never fire because the commands vary slightly each time.

### Changes

- `processors/ocr_quality_advisor.py` — new `OcrQualityAdvisor`
  `MultiHookProcessor`. On the first OCR invocation per task (`tesseract` /
  `pytesseract` / `image_to_string` in a Bash command), appends a one-time
  general OCR-quality recipe to that tool's result: upscale several-fold, force
  `--dpi 300`, try alternate `--psm` modes, and VERIFY legibility before
  trusting the text or building a parser on it — explicitly warning against
  guessing content from sample values or trusting a mis-read string.
  Content-agnostic (no task/path/schema/constant); contract-safe (only augments
  `event.result`, fires at most once per task).
- `config.yaml` — register after `RepeatedCommandBreaker` (order 32), before
  `CustomSelfVerifyProcessor`.
- `system_prompt.txt` — copied byte-for-byte from R1 (SiblingSystemPromptBuilder
  needs it as a sibling of the active config).

### Evidence

- `task_000015_89886d8d` result.json: `reward=0`, exit `done`,
  final_pytest "Accuracy metric 0.3411 is below the 0.98 threshold".
- `task_000015` msg[2] tool: garbled OCR; msg[14] agent reads 3 sample URLs and
  msg[17]/[21] infers the schema from them instead of the image.
- `task_000505_50b5162d` msg[4] tool: "ssh-ed25519
  AAAAC3NzaC1IZDIINTESAAAAIOrXQ50Bf4PZU0H9 + |J9tY +X07yG/pA3T2Xb8" (mis-read);
  final_pytest "2 of 2 evil bypassed: ls_evil, cat_evil".
- Cluster: 7 tasks invoke tesseract/OCR this round; the 2 that need the FULL
  structured content of a small image fail, the ~5 that need a short legible
  token pass — confirming the gap is OCR quality on dense/small images.

### Uncertainty

The advisor guarantees the recipe is in context at the first OCR call; it does
not guarantee the model executes the upscale correctly, so a flip is plausible
not certain. The sure win is redirecting the agent away from fabricating or
trusting garbled data. If it proves ineffective (neither task flips) it is inert
elsewhere, so downside is bounded to a small token cost on OCR tasks — revert per
the rollback trigger if pass_rate is flat/down.

## Round 2 — step-budget verification gate

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_step_budget_verify_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_001701_95e3bbcb]
cited_candidates: [C-002]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Gives every long-running task a forced convergence checkpoint at ~80pct of the step cap to confirm required outputs exist at exact paths — targets the 2-task budget_exceeded cluster and the 17-task done-but-reward-0 cluster (agent stops/dies with wrong or missing outputs)"
regression_risk: "A one-shot user message on tasks reaching the 64th step could distract a long task that was going to pass anyway; short passing tasks (most finish under 40 steps) never see it"
cost_shift: "Near-neutral: one ~250-token user message on runs that reach step 64; expected to REDUCE cost on budget_exceeded tasks by steering off stuck loops before the cap"
rollback_trigger: "If R3 pass_rate is flat/down AND a previously-passing long-running task (task_001818_b251e5ea, task_001673_86224c91) regresses to reward 0, revert"
-->

### Why

Assigned focus task_000010_644ab1c2 (system_administration) dies at
`exit_reason=budget_exceeded`, steps=80, reward=0. The final pytest fails on
`test_operator_script_exists`: `/home/user/operator.py` does not exist. The
agent hit an import error (its `operator.py` shadowed the stdlib `operator`
module), renamed the required file to `k8s_operator.py` to dodge the error
(msg 56) — permanently violating the requirement — then burned msgs 57-69
fighting a hanging pexpect/`timeout` command and never restored the required
path. It never reached a voluntary exit-intent turn, so
`CustomSelfVerifyProcessor`'s pre-exit checklist never fired. This is the
general shape of the dominant R1-round failure cluster: the R1 loop breakers
shrank budget_exceeded-at-80 from 6 tasks to 2, but now 17/23 failures exit
`done` with reward 0 — the agent stops (or dies at the cap) with wrong/missing
outputs. The pipeline has NO active budget-proximity convergence trigger:
`TaskTimeReminderProcessor` is a no-op without `timeout_seconds` (0/50
trajectories contain any time-reminder text), and the self-verify gate fires
only on voluntary exit.

### Changes

- `processors/step_budget_verify.py` — new `StepBudgetVerifyProcessor`
  (`MultiHookProcessor`). Counts steps locally; once past `warn_fraction=0.8`
  of `step_budget=80` (the 64th step) injects ONE user message: a concrete
  "confirm each required output file exists at its exact path, a rename/skip
  means the requirement is unmet, prefer a simpler correct deliverable over
  retrying a stuck sub-problem" checklist. Task-agnostic; names no
  path/command/constant.
- `config.yaml` — register at `_order=7`, right after `TaskTimeReminderProcessor`.
- `system_prompt.txt` — copied byte-for-byte from R1 (sibling required by
  `SiblingSystemPromptBuilder`).

### Evidence

- `task_000010_644ab1c2.result.json`: `agent.exit_reason=budget_exceeded`,
  `steps=80`; `final_pytest` asserts `os.path.isfile('/home/user/operator.py')`
  is False.
- `task_000010_644ab1c2.messages.json` msg 56: `mv /home/user/operator.py
  /home/user/k8s_operator.py` — the required file renamed away; msgs 57/68 show
  hanging commands (exit 124/137) consuming the rest of the budget.
- Pipeline gap: `TaskTimeReminderProcessor` has no `timeout_seconds` set →
  no-op; 0/50 trajectories contain time-reminder text.

### Uncertainty

The nudge guarantees a convergence checkpoint but does not inject the missing
capability (task_000010 still needs to solve the stdlib-shadowing hang — a
1/50 capability gap, deliberately not patched). A flip is plausible not
certain; the reliable win is converting "died at step 80 with nothing at the
required path" into "spent the tail restoring/validating required
deliverables." If the redirect distracts a long passing task, watch for a T-to-F
regression on the long-running passers and revert.

## Round 2 — acceptance-criteria reconciliation prompt

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_acceptance_criteria_reconcile_v1
levers: [instruction]
predicted_affected: [task_000118_3043e92d, task_000396_e56917e2, task_001031_a8f0eb37, task_001937_ac874115, task_000015_89886d8d]
cited_candidates: [C-301]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Targets the reward=0 & exit=done cluster (~20 tasks) whose shared shape is declaring success against an unmet, explicitly-stated numeric/behavioral bar; a partial conversion flips several and the discipline generalizes to unseen tasks with stated criteria"
regression_risk: "Low — verification discipline, not a solution recipe; names no task/constant/path. Worst case: one extra self-check turn on already-passing tasks (which already spend a self-verify turn). No processor pipeline change, so no replay-gate structural risk."
cost_shift: "Small positive (a few hundred tokens/task for the explicit comparison block); offset on flipped tasks that would otherwise burn a full failed run."
rollback_trigger: "If R3 pass_rate is flat/down AND any previously-passing task regresses to a truncated/over-verifying exit, revert to the R1 minimal prompt."
-->

### Why

Assigned focus `task_000118_3043e92d` (system_administration) fails with
reward=0, exit_reason=done at 24 steps — NOT a loop/budget failure, so R1's
loop breaker does not apply. The agent wrote a deployment_monitor.py that
followed the task's step recipe literally (SIGSTOP, truncate, SIGCONT at the
40 MB bar), ran its own test, and the test printed a size climbing
monotonically to "Disk usage (209715200 bytes) exceeds threshold" (peak = full
200 MB, failing the grader's max_size <= 45000000). The agent then wrote "disk
usage never exceeded 40 MB" — flatly contradicting its own output — and
declared success. The design is unbounded-peak by construction; clearing the
bar requires recognising the literal recipe is insufficient, a step skipped
because the agent never reconciled its produced behavior against the task's
stated numeric acceptance bar. The same shape recurs across the failing-done
cluster: agent computes a concrete value that violates an explicit bar, then
narrates success without an extract-and-compare step. Harness-addressable via
the system prompt as a verification discipline.

### Changes

- `system_prompt.txt` (sibling of config, read by SiblingSystemPromptBuilder)
  — add an acceptance-criteria reconciliation discipline: identify each stated
  numeric/behavioral bar up front and let it drive design; before declaring
  done, MEASURE the value the solution actually produces (extremum over the
  whole run for time-varying limits), print a per-criterion
  "produced vs required -> PASS/FAIL" line, sanity-check plausibility, and
  treat any FAIL (or a value contradicting on-screen output) as unfinished
  work. General strategy only — no task ids, constants, or paths.
- `config.yaml` — copied from R1 (processor pipeline unchanged, including the
  R1 loop breaker); added a header comment documenting the C-301 prompt change.

### Evidence

- `task_000118_3043e92d`: tool output shows 15 lines "Disk usage (52428800 …
  209715200 bytes) exceeds threshold"; final assistant: "disk usage never
  exceeded 40 MB … task complete". final_pytest: max_size=209715200 > 45000000.
- `task_000396_e56917e2`: final assistant "Maximum deviation (0.60724) …
  task is complete"; grader wants deviation < 0.1.
- `task_001031_a8f0eb37`: final assistant "density at origin: 34.109 …
  correct"; grader expects ~0.373 (a 2D KDE density of 34 is implausible).
- `task_001937_ac874115`: final assistant "Optimal Grid: 60 … complete";
  grader expects 50.
- `task_000015_89886d8d`: final assistant "All 3 tests pass"; grader accuracy
  0.3411 < 0.98.
- All five: exit_reason=done, finished=no_tool_calls, _tb2_self_verify fired
  1-2x yet the agent still narrated success.

### Uncertainty

The discipline blocks the false-success exit and forces another iteration, but
does not guarantee the agent then finds the correct redesign (still a model
capability) — a flip is plausible, not certain. Some failing-done tasks are
pure capability gaps (e.g. task_000028 fails on a verifier-side
ModuleNotFoundError: requests, a sandbox topology issue) and are excluded from
predicted_affected. Watch for T->F regressions where a previously-passing task
over-verifies into a truncated exit; if seen with flat/down pass_rate, revert
to the R1 minimal prompt. NOTE: this entry overlaps task_000015 with the
sibling "OCR quality advisor" proposal in the same batch — they are independent
proposals for the orchestrator to choose between.

## Round 2 — service teardown hygiene

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-01T00:00:00Z
hypothesis_id: h_service_teardown_hygiene_v1
levers: [control]
predicted_affected: [task_000140_01c78b42]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Flips service-lifecycle tasks graded on final process state (no-lingering-process asserts) — a recurring TB2/Tmax class: daemons, init/supervisor scripts, CI pipelines"
regression_risk: "Near-zero: one-shot reminder fires only when a background launch was observed AND the existing self-verify checkpoint fires; complete no-op on tasks that never backgrounded a process; advisory text cannot force a wrong kill"
cost_shift: "+~120 tokens once per run, only on runs that launched a background service; no added model turns (rides the existing self-verify turn)"
rollback_trigger: "If R3 pass_rate is flat/down AND any previously-passing service task regresses to a state where a required-alive service was killed, revert"
-->

### Why

Assigned focus `task_000140_01c78b42` (system_administration) finished cleanly
(`exit_reason=done`, 11 steps, 41s) with reward=0. The agent correctly fixed
the Go service (port 8080, `/provision`, `username` form value), the supervisor
script (PATH + PID save), and authored the CI pipeline; running it produced the
required `PROVISIONED_VM_FOR: admin_alice` log line — the functional content was
right. It still scored 0 on `test_no_lingering_service_processes`: the grader
found three orphaned `vm_service` processes. The agent started a background
service via `./vm_service &` (through `bash test_pipeline.sh`) to test its
solution and never reconciled the final process set before exiting; its single
`kill -TERM $PID` targeted one PID and SIGTERM is asynchronous. This is the TB2
structural process-lifecycle gotcha in its lingering-process polarity — a
state-hygiene failure, not a logic failure. The existing self-verify checklist
even nudges the opposite direction ("confirm services are still alive").

### Changes

- `processors/service_teardown_hygiene.py` — new `ServiceTeardownHygieneProcessor`.
  Tracks (via `on_before_tool`) whether any Bash command backgrounded a
  long-running process (trailing `&`, `nohup`, `setsid`, `disown`,
  `systemctl start`, `service ... start`). Then, `on_after_tool`, when the
  existing `_tb2_self_verify` synthetic result flows through AND a background
  launch was seen, appends a one-shot final-state process-hygiene reminder to
  that result. Contract-safe (only augments `event.result`); rides the
  existing self-verify one-shot rather than hijacking exit-intent, so it cannot
  conflict with `CustomSelfVerifyProcessor`. Content-agnostic; names no task.
- `config.yaml` — register the processor after `CustomSelfVerifyProcessor`
  (`_order=91`).

### Evidence

- `task_000140_01c78b42` `result.json.final_pytest.output_tail`:
  `AssertionError: Lingering vm_service processes found: ['336', '586', '790']`.
- `task_000140_01c78b42` message log step 9: agent runs
  `bash /home/user/test_pipeline.sh` (starts `./vm_service &`), then its final
  turn declares "task complete" after only `cat vm_setup.log` — never runs
  `pgrep`/`ps` to confirm no `vm_service` remains.

### Uncertainty

The reminder guarantees the agent is prompted to reconcile the process table at
its self-verify pass, but does not guarantee it writes a correct teardown (kill
the whole group + re-check) — that is a model capability, so a flip is plausible
not certain. n=1 in this round (only one task shows the explicit lingering
assertion), so this is a scoped bet on a generalizable class rather than a
demonstrated multi-task cluster. If a service task that *requires* a live
daemon regresses because the agent over-killed, revert. NOTE: this is one of
several independent Round-2 batch proposals for the orchestrator to choose from.
