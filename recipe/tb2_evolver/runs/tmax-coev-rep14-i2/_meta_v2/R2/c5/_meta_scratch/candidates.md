# Candidates — R2 c5 (assigned focus task_000118_3043e92d)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a one-time `CleanSlateSelfTestReminder` processor that, on the agent's first
background-process launch, warns that the verifier runs the artifact from a
cold/clean process+port state and that stale self-test processes/ports must be
cleaned up before each self-test (so the agent's own feedback matches the
verifier's cold start).

- Tasks affected (same root mechanism — dirty self-test env from accumulated
  background processes/ports):
  - task_000118_3043e92d (system_administration) — primary focus
  - task_000313_1dce9844 (system_administration)
  - task_000010_644ab1c2 (system_administration)
- Signal: background-service/daemon tasks; tool results show accumulating
  `<defunct>` python/socat processes and repeated `Address already in use`;
  the agent's self-tests report success/failure that does not match the
  verifier's cold-start result.
- Verified (Read, body-quoted):
  - task_000118 step 33 tool result: two stale `python3` monitors still alive
    (PID 603, 3242) from prior heredoc runs; the agent's monitor (with a
    `while True: if not get_worker_processes(): break` startup bug that exits
    the instant no workers exist) "appears to work" (logs truncated to 4.0K) —
    a FALSE POSITIVE. Steps 36/44 committed the broken script; verifier
    cold-start measured peak 179306496 bytes > 45000000 → reward 0. The agent
    never cleaned prior background processes before its tests, so it never saw
    the immediate-exit behaviour the cold verifier hits.
  - task_000313 step 30/36 tool result: a pile of `[python3] <defunct>` +
    `[socat] <defunct>` processes accumulated across self-test iterations;
    run ended `loop_detected` at 76 steps.
  - task_000010 steps 20/22/26/38 tool result: repeated
    `socat[...] E bind(... 0.0.0.0:9090 ...): Address already in use` and
    `OSError: [Errno 98] Address already in use / Port 9090 still in use`
    because stale socat processes held the forward port; run `budget_exceeded`
    at 80 steps chasing a phantom bind failure.
- Why Control not Instruction: the trigger is a *mechanical, harness-observable
  runtime event* (the agent just launched a background process; its environment
  is now dirty relative to the verifier) that must fire uniformly across tasks
  and only when a background launch actually happened. A static prompt rule
  would either bloat every task's prompt (including the ~30 tasks that launch
  nothing in the background) or be ignored as generic advice; a Control hook
  fires exactly on the background-launch event, once, scoped to the cluster
  that needs it. It injects *strategy* (clean-slate discipline), never a
  solution, so it does not cross into capability-injection.
- Why Control not Action: the agent already has full capability (Bash can
  pkill / free ports / reset dirs); nothing new needs to be *doable*. The gap
  is that the agent's self-test feedback is contaminated — a post-launch
  advisory closes it; a new tool would not.
- Retroactive check (A-corrective): yes (upstream-of-capability variant). For
  task_000118 the reminder does not write the daemon for the agent, but it
  removes the false-positive: had the agent cleaned prior monitors and tested
  cold, its own test would have shown the monitor exiting immediately (the real
  bug), giving it a chance to fix the startup grace period instead of committing
  a script that only "worked" because of a stale process. For task_000010 /
  task_000313 the reminder directly targets the observed blocker — freeing the
  port / killing defunct procs between tests removes the `Address already in
  use` thrash and the defunct-process contamination that burned their budgets.
- expected_global_gain: closes a background-service/daemon self-test-hygiene
  cluster (>=3 distinct system_administration tasks, different domains: disk
  quota monitor, k8s operator port-forward, proxy+health-monitor) whose common
  root cause is a dirty self-test environment unlike the verifier's cold start.
  Generalises to any unseen daemon/service task because the advisory contains no
  task-specific literals — only generic clean-slate discipline.
- regression_risk: very low. Fires <=1x/task and ONLY after the agent launches a
  background process, so the ~30 non-service tasks never see it. It appends to a
  tool-result string (same contract as CustomEditToolProcessor) — no message
  insert/drop/reorder, contract-clean. Worst realistic case: a service task that
  the verifier expects to stay *alive* — the reminder's own text says "make sure
  it survives and keeps running", so it does not push toward wrongly killing a
  needed service; and it is advisory, not enforced.
- cost_shift: negligible-to-slightly-positive. One appended string on a single
  armed tool result per task; it may prompt 1-2 short cleanup commands, which
  replace the far more expensive port-collision / defunct-process thrash loops
  observed (task_000010 and task_000313 burned toward 76-80 steps). No forced
  extra model turns.
- rollback_trigger: revert if any previously-passing service/daemon task
  regresses to F attributable to the reminder (e.g. agent over-eagerly kills a
  service the verifier needed alive), if the reminder fires on non-background
  commands, or if synthetic replay fails on CleanSlateSelfTestReminder.

Note on prior-round honesty: an earlier c5 R1 correctly observed that
task_000118's *terminal* blocker (turning a correct natural-language diagnosis
into a working daemon with a startup grace period) is partly a model capability
gap and shipped a loop-detection nudge that did not flip it. This candidate
targets a *different, upstream, harness-observable* deficiency — the dirty
self-test environment that produced the FALSE POSITIVE which let the agent
commit the broken script in the first place — and it is backed by a >=3-task
cross-domain cluster, not the single focus task.
