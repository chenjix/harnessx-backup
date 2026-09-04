# Candidates — R2 / c4

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Author a `FinalStateHygieneProcessor` that appends a task-agnostic final-state
process/resource-hygiene checklist to the exit-time `_tb2_self_verify`
acknowledgement, so the agent reconciles lingering / misnamed processes and
resource footprints against the task's required END state before exiting.

- Tasks affected (assigned focus + same-mechanism cluster):
  - `task_000140_01c78b42` — verifier `test_no_lingering_service_processes`
    fails: `Lingering vm_service processes found: ['338', '591', ...]`.
  - `task_001090_c61c71f2` — verifier fails
    `Process name is 'bash', expected 'monitor'` — wrong process *identity* in
    the final state (`/proc/<pid>/comm`); a wrapper `bash` left in front of the
    real binary instead of `exec`-ing it.
  - (related resource-footprint shape, same verifier family:
    `task_000118_3043e92d` peak log dir size exceeded threshold;
    `task_000748_c9807703` leaked-bytes mismatch — these read on the same
    "final container state" verifier axis, so the resource-hygiene clause is
    additive, not the primary target.)
- Signal: `final_pytest.output_tail` on the focus task contains
  `Lingering vm_service processes found` from `pgrep -f vm_service`; the agent's
  own trajectory (step 14) ran `test_pipeline.sh` which starts `vm_service &`,
  and the verifier re-runs the pipeline — SIGTERM on the single recorded PID is
  racy and leaves survivors (338, 591, 790). `task_001090` tail:
  `AssertionError: Process name is 'bash', expected 'monitor'`.
- Verified (Read of `task_000140_01c78b42.messages.json`):
  - Task prompt (msg 0): "gracefully stop the Go service by reading
    `/home/user/service.pid` and sending a SIGTERM kill signal" — the required
    END state is *no service running*.
  - Step 14 tool call: `bash /home/user/test_pipeline.sh` — spawns `vm_service`.
  - Step 20 `_tb2_self_verify` fired; the agent's follow-up (msg 22) only
    re-checked file existence and text content, **never** ran `pgrep`/`ps` to
    confirm no `vm_service` lingered, then declared done at msg 26.
  - `result.json` `final_pytest`: `FAILED test_no_lingering_service_processes -
    AssertionError: Lingering vm_service processes found`.
  - `task_001090_c61c71f2.result.json` tail: `assert comm == "monitor" ...
    'bash' == 'monitor'` — final process-identity mismatch, same "reconcile the
    running process against the required end-state" gap.
- Why Control not Instruction: the stock `CustomSelfVerifyProcessor` already
  owns the exit-time handshake and its checklist message; a sibling
  system-prompt edit (the only Instruction surface here) would compete with that
  mechanism and fires on every step regardless of exit intent. The narrowest fix
  is a Control hook that piggy-backs on the *existing* exit gate, appending
  guidance to the synthetic self-verify result exactly when the agent is about
  to leave — the same `on_after_tool` append pattern `CustomEditToolProcessor`
  already uses. It adds no messages and cannot change message counts, so it is
  contract-trivial and only costs tokens at the single exit turn.
- Why Control not Action: there is no missing capability — `Bash` can already
  run `pgrep`/`kill`/`ss`/`du`. The gap is that the agent doesn't *reach for*
  the cleanup check at exit; a new tool would not fix a habit gap.
- Retroactive check (A-corrective): yes — had the hygiene checklist been in the
  self-verify ack on `task_000140`, the exit gate that already fired at step 20
  would have prompted `pgrep -f vm_service` and a terminate-until-empty loop,
  leaving zero survivors and flipping `test_no_lingering_service_processes`. For
  `task_001090` it would have prompted a `/proc/<pid>/comm` identity check
  before exit.

- expected_global_gain: Flips the "final-state process hygiene" failure cluster.
  Directly targets `task_000140` (lingering processes) and plausibly
  `task_001090` (process identity); the resource-footprint clause is additive
  cover for `task_000118` / `task_000748`. Generalises to any service/daemon/
  worker task where the verifier inspects the final container process/resource
  state — a recurring TB2 verifier family ("final state" tests) not addressed by
  the current file-content-only self-verify checklist.
- regression_risk: Low. The reminder only appends text to the one-shot exit
  acknowledgement; it never injects messages, never changes control flow, and
  fires at most once per task. On tasks with no service, the extra text is a
  few hundred tokens the model reads once and ignores. Worst case: the agent
  spends 1-2 extra Bash calls confirming `pgrep` is empty on a non-service task —
  cheap and harmless. No currently-passing task depends on leaving processes
  running unless the task asked for it (the clause explicitly branches on
  "stopped vs left-running").
- cost_shift: +~350 tokens on the single exit turn per task (the appended
  checklist) plus, on service/resource tasks only, 1-3 extra Bash verification
  calls near the end. Negligible aggregate; no per-step growth.
