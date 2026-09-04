# Candidates — R2 c3 (assigned focus: task_000118_3043e92d)

## Candidate C-001 — quantitative-criteria self-verify step

**Three-axis tag:** lens=self-verification-quality / lever=control / intent=close-failure-cluster

**Signal (assigned task).** `task_000118_3043e92d` (system_administration,
deployment-monitor daemon). reward=0, exit_reason=done, 19 clean steps. Grader
failure: `AssertionError: Peak log directory size was 209715200 bytes, which
exceeds the threshold of 45000000 bytes`.

**Verified body evidence.**
- The agent wrote a monitor with a timing/granularity bug: it checks size only
  every `time.sleep(0.1)` while 20 workers each write 512KB every 0.1s
  (~10MB/0.1s aggregate), so the *peak* size the grader samples (every 0.05s)
  balloons far past the bound even though truncation eventually fires.
- **Crucially, the agent's own test printed the failure evidence.** messages.json
  step (tool result of the combined monitor+deploy run) shows a monotone ladder:
  `Size (52428800 bytes) ... exceeds threshold` → 62MB → 73MB → ... →
  `Size (209715200 bytes) ... exceeds threshold` (15 lines), then
  "No worker processes running. Exiting."
- The agent then declared: *"The monitor is working correctly ... The total
  size is now 4 KB ... successfully prevented the disk from filling up."* It
  looked only at the **final** dir size (4KB) and ignored the **peak** (200MB)
  that its own output showed.
- The stock `CustomSelfVerifyProcessor` DID fire (`_tb2_self_verify` keepalive +
  checklist visible in the trajectory). The agent walked the checklist —
  re-read the task, `ls`-checked the file, `cat`-inspected content,
  `py_compile`-validated syntax — and still passed itself, because **no
  checklist step asked it to check its own observed runtime numbers against the
  task's quantitative bound.**

**Root-cause classification.** Two layers:
1. The underlying concurrency/granularity bug is a *model capability gap* — the
   harness must NOT encode "use a finer poll interval" or "the threshold is 40MB".
2. But there is a genuine *harness deficiency*: the self-verify checklist is
   strong on file-existence and method-validity yet has **no step forcing the
   agent to confront numeric acceptance criteria against the output it already
   produced**. The failure evidence was in the agent's scrollback; the harness
   gave it a verification ritual that let it skip past that evidence.

**Change.** New `QuantitativeSelfVerifyProcessor` (processors/quant_self_verify.py),
a behaviour-preserving drop-in for `CustomSelfVerifyProcessor`: identical
singleton group (`tb2_self_verify`), `_order` 90, fire-once-per-task, keepalive-
tool + one-shot `on_before_model` +1-user-message mechanics. The only diff is
the injected checklist adds step 5, "Confront the numbers": restate every
quantitative acceptance criterion (threshold/limit/count/size/timing/tolerance/
accuracy, and whether it must hold at-all-times / on-average / at-the-end), then
re-read the actual observed output and check each number — **peaks/extrema during
the run**, not just the final snapshot — against its bound; a transient breach
is a failure. Existing steps 1–4/6 are byte-identical to stock.

**Retroactive check (would-this-have-helped).** On task_000118 the agent had
the exact failing numbers in front of it. A checklist step that says "restate
the numeric bound and check the peak your own test observed" targets precisely
the reasoning step it skipped ("peak 209MB > threshold → NOT done"). It does not
hand the agent the fix (finer polling), only forces it to notice the failure and
iterate — which is the general behaviour we want. It cannot guarantee the 4B
model then fixes the race, but it removes the harness-provided off-ramp that let
it exit on false success.

**Why control, not instruction (system prompt).** The nudge must fire *at exit
time, once, only when the agent tries to stop* — that is the self-verify
processor's exact contract; a static system-prompt line would be diluted across
the whole run and easy to forget by step 19. Reusing the existing fire-once slot
(same group/order) means zero new pipeline surface and no double-fire risk.

**expected_global_gain.** Closes a cross-task cluster: any TB2 task judged by a
number (byte/latency/count limits, accuracy thresholds, tolerances, quota/rate
caps) where a superficially-clean final state masks a violated invariant. The
monitor/daemon/quota family is the canonical case but the step is generic.

**regression_risk.** Very low. The processor is byte-identical to the stock one
in every mechanic (same group/order/fire-once/contract); only the injected text
grows by one step. It never blocks/rewrites/terminates a tool call. Worst case:
~90 extra tokens of checklist on the single self-verify turn, and on a task with
no numeric criteria the new step is a fast no-op ("no quantitative criteria →
proceed"). No passing cluster depends on the agent NOT re-checking its numbers.

**cost_shift.** Negligible-to-slightly-positive per task (~90 tokens on the one
self-verify turn). On tasks it actually flips it saves a full failed round.

**rollback_trigger.** If R3 shows task_000118 still fails AND any previously-
passing task regresses (e.g. the extra step drives an over-cautious agent into a
new edit loop or a false "not done" that burns budget), revert to
`benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor`.

## Note on the prior R1(c3) heredoc hypothesis for this same task

The R1(c3) journal entry (`h_heredoc_edit_detector_v1`) diagnosed task_000118 as
a phantom-`[EditDetection]`/delete-recreate-loop problem. **The actual trajectory
in this round shows NO `[EditDetection]` warnings and NO delete/recreate loop** —
the agent wrote the file once via heredoc, tested it, and exited in 19 steps. So
that diagnosis does not match this trajectory; I did not re-propose it (novelty)
and instead target the real, observed root cause (false-success self-verify).
