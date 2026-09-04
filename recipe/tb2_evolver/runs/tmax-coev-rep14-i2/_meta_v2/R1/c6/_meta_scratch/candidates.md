# Candidates — R2 (c6)

Assigned focus: `task_000140_01c78b42` (F).

## Diagnosis

`task_000140` is a service-lifecycle task: fix a Go HTTP service, fix its
init/supervisor script (`start_service.sh`), and author a CI/CD pipeline
(`test_pipeline.sh`) that starts the service, exercises it, and **gracefully
shuts it down**. The agent's solution was functionally correct — `vm_setup.log`
contained the expected `PROVISIONED_VM_FOR: admin_alice`, and 2 of 3 verifier
tests passed. The single failing test is `test_no_lingering_service_processes`,
which asserts `pgrep -f vm_service` returns nothing. At verification there were
lingering `vm_service` PIDs (`['348', '610', '808']`) — the agent started the
service (during its own testing and via the pipeline) and left processes alive
at session end.

Reading the stock TB2 self-verify checklist
(`benchmarks/terminal_bench_2/harness.py::_SELF_VERIFY_MSG`, read-only) reveals
a **one-sided bias**: item 5 says *"For running services: confirm they are
still alive and reachable right now"*. It only ever nudges toward KEEPING a
service up. For lifecycle / init-script / CI-pipeline tasks — where the whole
point is a clean start→exercise→graceful-stop cycle and the verifier checks for
NO lingering processes — this steering is actively counterproductive. The agent
re-verified files after self-verify but never considered tearing down the
processes it had spawned.

This is a harness deficiency (a one-sided prompt mechanism owned by the
harness), not purely a model capability gap. The fix is a **two-sided**
reminder that makes the agent reconcile its final process state with the task's
requirement, without the harness deciding the intent for it.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a one-shot `ServiceLifecycleReminder` `MultiHookProcessor` that, the first
time the agent launches a long-lived background service (`&` / `nohup` /
`start_service` / server-listen patterns), appends a two-sided lifecycle
reminder to that command's tool result: re-read the task and either confirm the
service stays up (if required) OR stop it and leave no lingering processes (if
it's a lifecycle/CI task or was only started for testing).

- Tasks affected: `task_000140_01c78b42` (assigned focus, F on
  `test_no_lingering_service_processes`).
  - Honest scope note: only this task in the R0 set carries a literal
    "no lingering process" verifier assertion (task_001979 matched the grep on
    an unrelated content test). So by the strict ≥2-task systemic filter this
    is a **1-task literal cluster**. It is shipped anyway because the *root
    cause is a global harness mechanism* — the self-verify checklist's
    one-sided "keep services alive" nudge touches **every** service task in the
    benchmark (the R0 set has ~14 service/server tasks), and the reminder is
    strictly additive/neutral on the "leave running" direction it does not
    contradict.
- Signal: `final_pytest.output_tail` →
  `test_no_lingering_service_processes ... AssertionError: Lingering vm_service
  processes found: ['348', '610', '808']`; `reward=0` despite
  `initial_pytest.passed=true` and the correct log write.
- Verified (Read, task_000140 messages.json):
  - step (assistant, ~line 90) authored `start_service.sh` doing
    `./vm_service &\necho $! > /home/user/service.pid` — backgrounds the
    service each invocation.
  - step (assistant, ~line 110) authored `test_pipeline.sh` ending with
    `kill -TERM $PID` but never waits for / confirms termination, and never
    checks for strays.
  - final assistant turn after `_tb2_self_verify` fired (line ~230): the agent
    re-`ls`'d output files and re-`cat`'d the log, but issued **no** process
    check or teardown — consistent with the self-verify checklist steering it
    only toward "files exist" + "services still alive".
- Why Control not Instruction: the biased text lives in a **read-only** harness
  file (`benchmarks/terminal_bench_2/harness.py`), so I cannot edit the prompt
  directly, and the sibling `system_prompt.txt` edit would fire on every task
  unconditionally (noisy, and can't be tied to the actual service-launch
  moment). A Control hook fires *exactly when* a background service is launched,
  is contract-safe (mutates only the triggering tool's result string, the same
  shape as the existing `CustomEditToolProcessor`), adds zero messages, and
  fires at most once — so it is both narrower and safer than a blanket prompt
  edit.
- Why Control not Action: the agent already has `Bash` and can `pgrep`/`kill`;
  it lacks the *prompt to reconcile state*, not a capability. No new tool is
  warranted (and TB2 exposes only `Bash` anyway).
- Retroactive check (A-corrective): yes — had the reminder been present, it
  fires on the `bash /home/user/start_service.sh` / pipeline launch, and the
  self-verify turn (which the agent DID act on) would have had an explicit
  instruction to `pgrep -af vm_service` and terminate strays before exiting.
  The agent demonstrably responds to injected reminders (it re-verified files
  on self-verify), so a teardown nudge at the decisive exit window plausibly
  flips `test_no_lingering_service_processes`.

### Pareto framing
- expected_global_gain: Flips the 1 literal lingering-process task and, more
  broadly, corrects a one-sided self-verify bias across the ~14 service/server
  tasks — reducing the chance any service task fails a "clean teardown"
  criterion while leaving the "keep alive" direction fully intact.
- regression_risk: Low. The reminder is two-sided and additive: for tasks that
  DO require the service alive, it explicitly says "confirm it is still
  listening" — so it cannot push those toward wrongly killing a needed service.
  Worst realistic case is a few extra `pgrep`/`ls` Bash calls near the end of a
  service task. No message-history mutation, fires ≤1×/task, only on Bash calls
  matching an explicit background-launch pattern.
- cost_shift: Negligible-to-slightly-positive tokens: one appended reminder
  string on a single tool result per armed task, possibly prompting 1–2 extra
  short verification Bash calls. No extra model turns forced.
- rollback_trigger: Revert if any previously-passing service/server task
  regresses F attributable to the reminder (e.g. the agent kills a service the
  verifier needed alive), or if the reminder fires on non-service commands.
