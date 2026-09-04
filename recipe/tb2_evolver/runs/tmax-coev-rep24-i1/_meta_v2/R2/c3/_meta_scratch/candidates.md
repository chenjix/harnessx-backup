# Candidates — R2/c3

Assigned focus: `task_000118_3043e92d` (system_administration) — FAIL, `exit_reason=budget_exceeded`, 80 steps.

## Candidate C-002

**Three-axis tag:** lens=execution-model / lever=control / intent=unblock-recurring-failure-mode

**Title:** Stateless-shell background-process guard — tell the agent that `&`-backgrounded processes do not survive across separate Bash tool calls, and how to test concurrent processes.

### Signal (verified from trajectory body)

`task_000118_3043e92d.messages.json` — the task requires writing a daemon
`deployment_monitor.py` that must run *concurrently* with a deployment that
launches 20 short-lived `worker_sim.py` workers, keeping `/home/user/logs`
under a size threshold.

Verified body evidence:
- Msg 74/150/278: agent runs `python3 deployment_monitor.py &` (or the deploy)
  as a **bare backgrounded command in one Bash call**, then runs the
  counterpart in a **separate later Bash call**.
- Msg 82/198/306/60/68: every attempt returns `No worker processes found.
  Exiting.` and logs at 0 bytes or 200 MB — the monitor and the deployment
  **never coexist**, because each Bash tool call is an independent
  `docker exec ... bash -lc <cmd>` (confirmed in
  `recipe/tmax_eval/docker_env.py::exec_in`): the shell — and its
  `&`-backgrounded children — tears down when the tool call returns.
- Msg 16/88/96/104/204/... : the agent never grasps this. It loops on the
  same wrong hypothesis ("the deployment runs too fast") and repeatedly hits
  `finish_reason=length`, producing 10 truncation markers and 12 passive
  "continue" nudges, burning all 80 steps → `budget_exceeded`.
- Final `final_pytest`: `max_size 209715200 > 45000000` — the graded
  invariant was never satisfied because the agent never ran a valid
  concurrency test and never converged on a working monitor.

### Root cause classification

Harness deficiency, not a pure capability gap. The agent's blocker is a
*structural fact about the execution model* — each Bash call is a fresh
shell, so `A &` in one call is not co-scheduled with `B` in the next call —
that is invisible from the task text and from any single tool result. The
existing `LengthTruncationRecoveryProcessor` fired on the *symptom* (length
loops) but could not resolve the *cause*, because its nudge ("issue one Bash
command") does not tell the agent WHY its test is structurally impossible.
Supplying that missing execution-model context at the moment the antipattern
appears is a generic harness mechanism.

### Intervention

New `StatelessShellBackgroundGuard` (Control processor). On `on_after_model`,
inspect the assistant's Bash tool call(s):
- If a command **starts a long-lived process in the background** with a bare
  `&` (heuristic: contains ` &` at end of a segment AND a persistence
  indicator like a loop/daemon/`python*.py`/`.sh` invocation) and does NOT
  already combine it with its counterpart via `; wait`, `&&`, or use
  `nohup`/`setsid`/`disown`, remember it.
- On the **next** Bash call that does NOT itself co-run the pair, set a
  one-time pending hint.

`on_before_model` injects the hint once (respecting the +1-user-insertion
contract, mirroring `LengthTruncationRecoveryProcessor`): explains that each
Bash call is a separate shell so background processes from a prior call are
gone, and that to observe/test co-running processes you must launch them in a
**single** Bash command (e.g. `A & B; wait`) or use `nohup ... &` and then
verify in the same call. Fires at most a small capped number of times per
task to avoid nagging.

### Retroactive check (variant: would-this-have-helped)

Replaying `task_000118_3043e92d`: the antipattern first appears at msg 74/151
(`python3 monitor.py &` in isolation, deploy in a separate call). The guard
would inject the shell-statelessness hint before the agent's next turn —
directly contradicting its stuck hypothesis and pointing at the one-command
fix. Even if the agent still needed to refine the monitor logic, it would
break the repetition loop that consumed the entire budget, converting a
guaranteed `budget_exceeded` (0 useful progress after msg 60) into at least a
chance of a working single-shell test and a correct final script.

### Why control (not instruction / configuration)

- **Not instruction (system prompt):** the fact is only relevant when the
  antipattern actually occurs; baking "each Bash call is a new shell" into the
  static prompt for all 50 tasks is noise for the ~90% that never background
  anything, and the R1 journal shows the batch already leans on targeted,
  content-gated control processors over prompt bloat.
- **Not configuration:** no existing knob (compaction, length-recovery
  threshold, time-reminder) encodes execution-model semantics; tuning them
  would not tell the agent why its test is impossible.
- **Control** is the right lever: intercept the exact runtime pattern and
  inject the missing dynamic context, matching the existing
  `LengthTruncationRecoveryProcessor` / self-verify style.

### Pareto statement

- `expected_global_gain`: unblocks the `system_administration`
  daemon/service/monitor cluster where the agent must reason about
  concurrent processes across the stateless Bash tool — a recurring TB2
  structural trap ("Background process dies after agent exits", playbook).
  Generalizes to any task requiring a background process + a foreground
  workload tested together.
- `regression_risk`: low. Purely additive one-shot user-message hint, gated on
  a specific backgrounding heuristic; silent on the vast majority of tasks
  that never background a persistent process. Worst case: one extra short user
  turn on a task that legitimately used `nohup`-style backgrounding — mitigated
  by excluding `nohup`/`setsid`/`disown`/`; wait`/`&&`-combined commands and by
  a per-task fire cap.
- `cost_shift`: negligible aggregate; +~1 short user message only on the
  subset of tasks exhibiting the bare-`&`-then-separate-call antipattern.
- `rollback_trigger`: if R3 shows pass_rate flat/down AND new T→F regressions
  on tasks that previously backgrounded processes cleanly, revert.
