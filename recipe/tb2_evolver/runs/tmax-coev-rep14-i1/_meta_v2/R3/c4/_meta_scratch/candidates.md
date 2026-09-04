# Candidates — R4 c4 (focus: task_000140_01c78b42)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace `CustomSelfVerifyProcessor` with a behaviourally-identical
`LifecycleSelfVerifyProcessor` whose exit-verify checklist item 5 is
polarity-neutral (reconcile the required end state: persist OR teardown)
instead of the stock one-sided "confirm running services are still ALIVE".

- Tasks affected:
  - primary failing witness: task_000140_01c78b42 (clean-shutdown / no
    lingering processes)
  - latent-harm surface (any lifecycle task whose required end state is a
    STOPPED/clean state): the stock checklist wording actively nudges those
    tasks the wrong way. This is a harness-level polarity bug, not a
    per-task tweak.

- Signal: task_000140 `final_pytest` fails only on
  `test_no_lingering_service_processes` — `AssertionError: Lingering
  vm_service processes found: ['344','591']` (and `['344','591','787']` on
  the second grader run). `exit_reason=done`, `finished=no_tool_calls`,
  11 steps, `initial_pytest.passed=true`. Voluntary exit — the self-verify
  processor DID fire (msg 213 `_tb2_self_verify`).

- Verified (Read of messages.json):
  - msg 117: agent's `test_pipeline.sh` ends with
    `kill -TERM $PID` (PID from `/home/user/service.pid`), then it ran
    `bash /home/user/test_pipeline.sh` (msg 157) which starts
    `./vm_service &`. The kill is fire-and-forget with no post-kill
    `ps`/`pgrep` confirmation.
  - msg 213–230: the stock `_tb2_self_verify` checklist fired; the agent
    re-`ls`'d files and re-declared success (msg 250) but never reconciled
    the live process table — item 5 of the stock checklist only asks to
    confirm services are *still alive*, the opposite polarity, so it gave
    the agent no reason to reap the test-run service.
  - Stock checklist text (harness.py L96): "For running services: confirm
    they are still alive and reachable right now" — one-sided persist
    wording, verified in source.

- Why Control not Instruction: the checklist lives *inside* a processor
  that is mechanically wired to fire exactly once at the no-tool-call exit
  intent (synthetic keepalive + deferred +1 user message). Editing the
  sidecar `system_prompt.txt` (Instruction) cannot reach this
  decisive-moment injection — the sidecar is the up-front system prompt,
  not the exit-time nudge, and the R0 sidecar is the minimal 5-line
  default. The gap is a wrong-polarity string in the existing Control
  component; the narrowest fix is a drop-in replacement of that component
  with corrected wording, preserving every other mechanic (same singleton
  group, order 90, +1-user contract).

- Why not re-ship R1(c4) `h_bg_state_reconcile_v1` shape: that pending
  hypothesis added a *separate* new processor with regex launch-detection
  that injects an extra message on top of self-verify (more surface, more
  cost, +1 additional user message). This candidate is a smaller, different
  shape: it corrects the polarity of the message the harness ALREADY sends,
  adding zero net messages and zero new firing conditions. Distinct lever
  mechanic, distinct hypothesis_id.

- Retroactive check (A-corrective): yes — the self-verify already fired on
  task_000140 at the decisive exit turn; had item 5 asked the agent to
  reconcile the required *stopped* end state and reap any test-run process,
  a single `pgrep -f vm_service` + `kill` at that turn removes the lingering
  PIDs the grader inspects. The failure is downstream of a wrong-polarity
  nudge, not of a missing capability (the agent already knows `kill`/`pgrep`
  and used `kill` in its pipeline).

- expected_global_gain: flips the clean-shutdown witness (task_000140) and
  hardens EVERY teardown-polarity lifecycle task against the stock
  checklist's one-sided "keep alive" wording, which is a latent regression
  risk across the whole benchmark.
- regression_risk: very low — the persist branch of item 5 is preserved
  verbatim in meaning (services that must stay running are still told to
  confirm alive), so service-alive tasks (e.g. task_000028/others that
  launch background processes and must keep them up) are unaffected;
  message-count contract is +1 user, identical to stock; no new firing
  conditions.
- cost_shift: negligible — item 5 is ~2 sentences longer than stock; at
  most one extra `pgrep`/`kill` Bash call on teardown-polarity tasks that
  would otherwise fail. Net-favourable by converting a silent
  lingering-process failure into a corrected clean exit.
- rollback_trigger: if next round shows task_000140 still F on
  `test_no_lingering_service_processes` with the process table unreconciled
  (nudge ignored → residual is model reasoning, not harness), OR any
  previously-passing service-must-stay-alive task regresses (service killed
  / unreachable), revert to stock `CustomSelfVerifyProcessor`.
