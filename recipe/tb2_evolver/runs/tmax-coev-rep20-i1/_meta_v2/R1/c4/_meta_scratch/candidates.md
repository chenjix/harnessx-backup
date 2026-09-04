# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Extend the one-shot self-verification checklist with a general item that
forces the agent to verify *transient / during-execution / peak-state*
correctness properties by observing behaviour WHILE the scenario runs —
not only by inspecting the final resting state.

- Tasks affected (assigned focus + generalizable class):
  - task_000118_3043e92d (assigned): peak `/home/user/logs` size hit
    209,715,200 bytes; the verifier grades PEAK size (`max_size <= 45MB`).
    The agent verified only the FINAL resting state and passed itself.
  - Generalizes to any task whose graded property is a transient runtime
    invariant observed only during execution: rate/quota caps, memory
    ceilings, concurrent-safety, live-service backpressure. Other
    `done`+reward-0 tasks in R0 (task_000024, task_000667, task_000965,
    task_001264, task_001421, task_001761) share the same *shallow
    self-verification* meta-shape but their remaining errors are
    static-output reasoning gaps the checklist step 3 already targets —
    see "Why scoped narrowly" below.
- Signal: `result.json` `agent.finished="no_tool_calls"`, `exit_reason="done"`,
  `reward=0`, `initial_pytest.passed=true` → `final_pytest.passed=false`.
  For task_000118 the assertion is `assert 209715200 <= 45000000` — a PEAK
  metric, structurally invisible to a post-run `du -sh` check.
- Verified (Read messages.json):
  - step ~5 (tool call `chatcmpl-tool-9f74e2673ab07357` sequence): the
    agent's monitor uses
    `for proc in subprocess.Popen([WORKER_SCRIPT, "1"], ...)` inside
    `find_running_workers()` — this LAUNCHES a new worker instead of
    enumerating running ones, so SIGSTOP/SIGCONT never reach the real
    workers. (Root logic bug = model capability gap, not fixable here.)
  - final steps: after the self-verify tool fired, the agent ran
    `du -sh /home/user/logs/` → "4.0K" and `ls -la` (all logs 0 bytes),
    then declared "disk usage is now only 4KB ... working correctly."
    It measured the RESTING state after workers exited, never the peak
    reached during the run. Had it re-run the deployment while polling
    `du -sb /home/user/logs` in a loop, the climb past 40MB (and the fact
    that SIGSTOP had no effect) would have been visible before exit.
- Why Control not Instruction: the checklist is delivered mechanically by
  the existing `CustomSelfVerifyProcessor` (a Control processor that
  intercepts the no-tool-call exit and injects a one-shot user message).
  There is no editable system-prompt template in the pipeline
  (`SiblingSystemPromptBuilder` reads a static sibling file the meta-agent
  does not own), and the checklist text itself lives in read-only
  `benchmarks/terminal_bench_2/harness.py`. The correct place to add a
  verification-discipline item is therefore a Control processor that
  overrides the injected message — not a template edit. The change is
  purely additive to an already-firing hook: no new firing behaviour, no
  extra model turns beyond the one the parent already spends.
- Why scoped narrowly (not a broad "verify harder" rewrite): the other
  `done`+reward-0 failures are wrong *values* the agent believed correct
  (build_deps schema, variance 0.000326 vs 0.000267, artifact miscount).
  The existing checklist step 3 ("inspect actual contents, confirm values
  are semantically correct") already targets those; adding domain hints
  would be task-specific knowledge injection (forbidden). The transient/
  peak-state item is the one *generalizable verification-method* gap the
  current checklist genuinely lacks.
- Retroactive check (A-corrective): yes — if the checklist had told the
  agent "for tasks graded on a peak/transient runtime property, re-run the
  full scenario and observe the metric WHILE it runs, not just afterward",
  the agent would have polled `du -sb` during a re-run, seen size exceed
  40MB, and discovered SIGSTOP had no effect — surfacing the bug before it
  declared done. It does not GUARANTEE a fix (the logic bug is the model's
  to solve) but it removes the false-positive verification that let the
  agent stop with a broken monitor.
- expected_global_gain: flips task_000118-class failures (peak/transient
  runtime invariants — deployment monitors, quota/rate caps, memory
  ceilings) where the agent currently self-certifies on resting state.
  At least 1 concrete R0 task; the class recurs across system_administration
  and security domains.
- regression_risk: low. The processor fires at most once per task (parent
  behaviour unchanged), only enriches the injected checklist text, and
  adds no new turns beyond the existing self-verify turn. Worst case is a
  slightly longer verification message → marginal token cost. It cannot
  block or redirect any tool call. Rollback trigger: if R1 pass_rate on
  the currently-passing `done` cluster (task_000344, task_000587,
  task_000748, task_000912, task_001781) drops, revert to
  `CustomSelfVerifyProcessor`.
- cost_shift: negligible. +~50 tokens on the one-shot checklist message
  per task; possibly +1 short re-run turn on genuinely transient-property
  tasks (which is the intended behaviour and cheaper than a wasted round).
