# Candidates — R1 c4

Assigned focus: `task_000140_01c78b42` (reward=0).

## Diagnosis

`task_000140` fails on `test_no_lingering_service_processes`:
`pgrep -f vm_service` returns `['340','586','793']` at grader time — the
task's success criterion is a CLEAN final state (a CI/CD pipeline that
starts AND then SIGTERM-stops the Go `vm_service`, leaving nothing
running). The agent fixed all three files correctly, ran
`test_pipeline.sh` once during its own testing, and declared done — but
left its own test-started `vm_service` alive. The verifier then also
exercises the lifecycle, and each invocation that fails to reap the
service accumulates a lingering PID.

Root harness gap: **the agent's own test-time actions mutate the final
state the verifier inspects, and nothing in the pipeline prompts it to
reconcile leftover background processes against the task's required end
state.** The existing `CustomSelfVerifyProcessor` checklist only nudges
the *persist* polarity ("confirm running services are still alive"),
which is the exact opposite of what this task needs, and says nothing
about cleaning up processes the agent itself started.

Idiosyncrasy note: only `task_000140` has the `pgrep`/Lingering grader in
this round (other service/background tasks fail for unrelated reasons —
missing files, output format, logic bugs). So the *specific* grader is a
single witness. The *mechanism* (test-time side effects polluting final
state; persist-vs-cleanup ambiguity) is a documented TB2 structural
failure mode (playbook: "Background process dies after agent exits" /
lingering process) and spans a real service cluster
(task_000028, 000118, 000344, 001089, 001264, 001498, 001673 ...), where
the current checklist's one-sided "keep alive" wording is a latent
regression risk for any clean-shutdown grader. The intervention is
therefore scoped as a *neutral* reconciliation nudge, not a forced kill.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `BackgroundStateReconcileProcessor`: a one-shot, no-tool-call exit
nudge that fires only when the session launched a background process,
prompting the agent to reconcile leftover processes against the task's
required end state (persist vs. clean shutdown) — without killing
anything itself.

- Tasks affected: `task_000140_01c78b42` (primary witness of the
  clean-shutdown polarity). Cluster protected against the inverse
  regression: `task_000028_7fe033ac`, `task_001089_220cc46b`,
  `task_001264_9f4ca84a` (service-alive polarity — nudge is neutral, so
  these keep passing).
- Signal: `final_pytest.output_tail` =
  `AssertionError: Lingering vm_service processes found: ['340','586','793']`
  from `test_no_lingering_service_processes`; agent `exit_reason=done`,
  `finished=no_tool_calls`, 11 steps. The lingering PID set grows across
  verifier lifecycle runs → an unreaped launch.
- Verified (Read):
  - `task_000140` step (assistant, ~msg index 6): agent writes
    `test_pipeline.sh` ending `kill -TERM $PID` then runs
    `bash /home/user/test_pipeline.sh` (msg 8) — starts `vm_service` in
    background via `start_service.sh` (`./vm_service &`, `echo $! > service.pid`).
    Final assistant turn (msg 250) declares success with **no** post-run
    `ps`/`pgrep` reconciliation; the test-run service is never confirmed
    dead. Grader then reports lingering PIDs.
  - Existing `CustomSelfVerifyProcessor` checklist (harness.py L96):
    "For running services: confirm they are still alive and reachable
    right now" — one-sided persist wording, the wrong polarity here.
- Why Control not Instruction: the trigger is a *mechanical* runtime
  condition — "a background-launch command was issued this session AND
  the agent is now exiting with no tool call." A static prompt line
  cannot condition on whether a launch actually happened; it would fire
  on every task (noise on the ~60% of tasks with no background process)
  and cannot compose with the existing self-verify keepalive machinery.
  A processor gates on the real event stream and injects exactly once,
  only when relevant.
- Why Control not a forced-kill guard: killing on exit would REGRESS the
  service-alive polarity cluster (task_000028/001089/001264), which is a
  larger passing set than the single clean-shutdown witness. The nudge
  keeps the polarity decision agent-side, matched to task text.
- Retroactive check (A-corrective): yes — if, at its final no-tool-call
  turn, the agent had been prompted to `ps`/`pgrep` for its leftover
  `vm_service` and terminate it (the task explicitly wants a
  start-then-stop pipeline with nothing lingering), the
  `test_no_lingering_service_processes` assertion would have passed. The
  file logic was already correct; only the final process state was dirty.

### Pareto framing
- expected_global_gain: flips `task_000140` (clean-shutdown polarity) and
  hardens the whole service/background cluster against the latent
  one-sided "keep alive" wording by making the persist-vs-cleanup
  decision explicit at exit.
- regression_risk: Low. Processor is inert on the ~60% of tasks with no
  background launch (regex gate) and fires at most once, only on a
  genuine no-tool-call exit *after* self-verify. It injects text only —
  never kills, never changes tool schemas or knobs. Message-count
  contract stays +1 user (same pattern as self-verify). Worst case: a
  couple of extra `ps`/`pgrep` Bash calls on service tasks.
- cost_shift: Negligible. One ~250-token user message + at most a few
  short verification Bash calls, and only on tasks that launched a
  background process. No effect on non-service tasks.
- rollback_trigger: If R2 shows `task_000140` still F with no process-
  state change, OR any previously-passing service-alive task
  (task_000028/001089/001264) regresses (service killed / not reachable),
  revert the processor.
