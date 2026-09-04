# R2 Candidates

## Candidate C-002 — stray/zombie background-process cleanup in self-verify

**Three-axis tag:** lens=final-state-hygiene / lever=instruction / intent=close-failing-cluster

**Signal.** R1 scored 43/50. Of the 7 remaining failures, exactly one is a
recurring, structural, harness-fixable pattern: **lingering background
processes at container final state**. The verifier phase runs pytest against
the final container state after the agent exits; some verifiers assert "no
lingering `<service>` processes" via `pgrep -f <name>`.

**Verified body evidence — `task_000140_01c78b42`** (system_administration, F):
- `verifier`: `test_no_lingering_service_processes` →
  `AssertionError: Lingering vm_service processes found: ['353','587','889','1114','1307']`.
- Agent messages `[36]` (its own `ps` output):
  `root 353 ... Z ... [vm_service] <defunct>` / `587 ... [vm_service] <defunct>` —
  **zombie (`Z`/defunct)** instances left over from the agent's repeated dev
  runs of `./vm_service &` in its persistent Bash session.
- Agent message `[37]/[39]`: it literally wrote "There are some zombie
  processes from previous runs. Let me clean those up" — then **exited without
  doing so**, scoring 0 even though the deliverable scripts (`main.go`,
  `start_service.sh`, `test_pipeline.sh`) and `vm_setup.log` were correct
  (2 of 3 verifier tests passed).

Root cause is a harness deficiency, not a capability gap: the agent has no way
to know from the task description that the final-state check forbids leftover
processes, and it develops by launching the service repeatedly in a persistent
shell that never reaps the children. The one existing exit-time intervention
(the self-verify checklist) is the correct, already-wired place to add an
explicit, actionable cleanup step.

**Change.** Additive item #7 to the one-shot self-verify checklist inside the
existing R1 `ServiceVerifyDepsProcessor` (same singleton group `tb2_self_verify`,
same `_order=90`, same keepalive mechanism — pure text extension). The item
tells the agent to: list processes it spawned (`ps -ef | grep <name>`), kill
ordinary strays (`pkill -f`), and — because a `<defunct>` process cannot be
signalled and is reaped only when its **parent** exits — terminate the parent
PID for zombies, then re-check the list is empty. It also guards against
over-killing: if the task requires a service to stay alive, leave exactly the
one required instance.

**Retroactive check (would-it-have-fired / would-it-have-helped).**
- *Would it have fired?* Yes — `task_000140` reached the no-tool-call exit
  intent (`finished: no_tool_calls`), which is exactly the trigger for the
  one-shot checklist; the agent even had `ps` output in hand.
- *Would it have helped?* High likelihood — the only failing verifier test was
  the lingering-process assertion (the other 2 passed); an explicit "kill the
  parent to reap the zombies, then confirm empty" instruction directly targets
  the exact command the agent knew it needed but skipped.

**Why instruction, not action/control.** A `control`-lever processor cannot run
sandbox commands (only `Bash` exists to the agent), so it cannot itself reap
processes. The only mechanism that can effect a cleanup is nudging the agent to
run the cleanup commands — an `instruction` delivered through the already-present
exit-time keepalive. No new tool is possible (benchmark exposes only `Bash`).

**Tasks affected (predicted_affected):** `task_000140_01c78b42` (direct);
generalizes to any future service/daemon task with a "no lingering process"
final-state assertion.

**expected_global_gain.** Flips the lingering-process failure in the
system_administration cluster (2/5 failing) and hardens every already-passing
service/daemon task against the same silent final-state trap. Same class of
structural verifier fact as the R1 `requests` fix, which flipped both its
predicted tasks.

**regression_risk.** Low. Purely additive checklist text on the once-per-task
self-verify turn; no change to the keepalive/singleton mechanism that R1 already
validated (both R1 predicted service tasks — 000028, 000958 — flipped to pass
and no service task regressed). The item is explicitly conditional ("if you
launched a background process") and warns against killing a service the task
needs alive, so non-service tasks and keep-alive-service tasks are unaffected.

**cost_shift.** Negligible — ~120 extra tokens in the single self-verify turn,
and at most 2–3 extra `Bash` calls (a `ps|grep`, a `pkill`, a re-check) on tasks
that actually spawned background processes.

**rollback_trigger.** If `task_000140` stays F AND any previously-passing
service/daemon task (e.g. 000028, 000958, 001090) flips to F — i.e. the added
cleanup nudge caused an agent to kill a service the verifier needed alive —
revert to the R1 checklist.
