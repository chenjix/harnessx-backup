# Candidates — R2 c4

Assigned focus: `task_000140_01c78b42` (system_administration) fails.

## Diagnosis

`task_000140_01c78b42` finished cleanly (`exit_reason=done`, 11 steps, 41s,
reward=0). The agent correctly fixed `main.go` (port 8080, `/provision`
endpoint, `username` form value), fixed `start_service.sh` (added
`/home/user/bin` to PATH, saved PID), and authored `test_pipeline.sh`. Its own
run of the pipeline produced the required `PROVISIONED_VM_FOR: admin_alice`
line in `vm_setup.log` — so the *functional* content was right.

It still scored 0. The verifier's `test_no_lingering_service_processes` failed:

```
AssertionError: Lingering vm_service processes found: ['336', '586', '790']
```

The failure class: **the agent starts a long-running background service
during its own testing (`./vm_service &` via `bash test_pipeline.sh`), then
exits without ensuring the process tree is torn down.** The pipeline's
`kill -TERM $PID` targeted only the single recorded PID and did not reap the
processes the agent (and later the grader) spun up; SIGTERM is also
asynchronous, so a naive kill-then-check races. The agent never treated
"no lingering service processes" as a success criterion — the existing
self-verify checklist even nudges the *opposite* direction ("For running
services: confirm they are still alive and reachable right now"), which is
actively counterproductive for teardown-verification tasks.

This is the exact TB2 structural gotcha flagged in the playbook
("Background process dies after agent exits" / process-lifecycle) but in its
lingering-process polarity. It is a harness-visible hygiene gap: the harness
can see the agent launched a background service and can remind it, at
exit-verification time, to reconcile the final process state. It cannot (and
should not) inject the task-specific teardown code.

Scope note (idiosyncratic filter): only 1 task in this 50-task round shows
the explicit `lingering` assertion, so this is formally an n=1 signal for
THIS round. Per the brief I am assigned this task and must fix the harness
capability it exposes. I therefore ship the **smallest defensible,
content-agnostic** intervention: a conditional, one-shot reminder that only
fires when (a) a background service launch was observed AND (b) the agent is
already in the self-verify exit path. It injects no action, names no task,
and cannot regress tasks that never launched a background process.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `ServiceTeardownHygieneProcessor`: a one-shot `on_after_tool` hook that,
when the existing `_tb2_self_verify` checkpoint fires AND a background service
launch was observed earlier in the run, appends a final-state process-hygiene
reminder to the self-verify tool result.

- Tasks affected: `task_000140_01c78b42` (assigned). Mechanism generalizes to
  any service/daemon task whose grader checks final process state.
- Signal: `final_pytest.output_tail` contains
  `AssertionError: Lingering vm_service processes found: [...]`;
  `exit_reason=done` with the required output content already correct — i.e.
  a *state-hygiene* failure, not a logic failure. Body shows `./vm_service &`
  launched via `bash /home/user/test_pipeline.sh` and no reconciliation of
  the running process set before exit.
- Verified (Read):
  - `task_000140_01c78b42` step 9 (message log): agent runs
    `bash /home/user/test_pipeline.sh`; the pipeline does `./vm_service &` in
    `start_service.sh` then a single `kill -TERM $PID`. Final assistant turn
    declares "task complete" after only checking `vm_setup.log` — never runs
    `pgrep`/`ps` to confirm no `vm_service` remains.
  - `result.json.final_pytest.output_tail`: `Lingering vm_service processes
    found: ['336', '586', '790']` — three orphaned processes at grade time.
- Why Control not Instruction: a static system-prompt rule ("clean up
  background processes") would fire on every task, inflating tokens and
  risking that the agent kills a service the task *requires* to stay running.
  The gap is mechanical and conditional — the harness can *see* whether a
  background launch actually happened this run and only then remind, keeping
  the decision agent-side. It rides the existing one-shot self-verify flow
  (appends to its tool result) rather than adding a new exit-intent hijack,
  so it cannot double-fire or conflict with `CustomSelfVerifyProcessor`.
- Why Control not Action: the agent already has `Bash` (its only tool) and
  can run `pgrep -f`/`kill`/`pkill`; nothing new is needed in the action
  space — only a reminder to reconcile final process state.
- Retroactive check (A-corrective): yes — had the reminder been present, the
  agent (already in its self-verify pass) would have been prompted to
  `pgrep -f vm_service` and reap survivors before declaring done, satisfying
  `test_no_lingering_service_processes`. The functional content was already
  correct, so closing the hygiene gap is the sole remaining blocker.
- expected_global_gain: flips the assigned system_administration task and any
  future service-lifecycle task whose grader checks for lingering processes —
  a recurring TB2/Tmax class (service supervisors, init scripts, daemon CI).
- regression_risk: near-zero. Fires only when a background launch was seen
  AND self-verify already fired; on tasks with no background service it is a
  complete no-op. Worst case on a task that *wants* a service alive: the
  reminder explicitly says "if the task requires the service to keep running,
  leave exactly the intended process(es) up" — advisory, non-coercive, so it
  cannot force a wrong kill.
- cost_shift: +~120 tokens once per run, and only on runs that launched a
  background service. No added model turns (rides the existing self-verify
  turn). Net neutral-to-negative overall.
