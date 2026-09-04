# Candidates — R9

## Candidate C-006
[lens: failure | lever: instruction | intent: corrective]

Strengthen the system prompt's long-running-service discipline: launch servers
fully detached (own session/process group + all fds redirected to a log), then
self-verify reachability from a SEPARATE Bash call, and never end the session
with a teardown (`pkill`/`kill`/`systemctl stop`).

- Tasks affected (failing, same mechanism — service not reachable at the
  post-exit verifier phase): task_002108_a8cfbf2a, task_002138_2e85672e,
  task_000910_16cc0daf (server-cluster is 8/8 failing this run: also
  task_000796, task_001013, task_001037, task_002146, task_000348).
- Signal: `final_pytest.output_tail` on the server cluster contains
  `Connection refused` / `ConnectionError` / `Connection broken` /
  `not reachable. Did you start it in the background?` — the verifier runs in a
  separate phase after the agent session ends (TB2 playbook: sandbox topology;
  "Background process dies after agent exits").
- Verified (Read of messages.json):
  - task_002108: agent launched `nohup ./target/release/frame_server > ... &`
    and its own `ps` at end shows the process ALIVE
    (`725 ... ./target/release/frame_server`), yet the verifier tail says
    "The server on 127.0.0.1:8000 is not reachable. Did you start it in the
    background?" — a running-at-session-end but dead-at-verify shape, i.e. the
    process did not survive the phase boundary. The verifier's own error text
    names background-persistence as the scored dimension.
  - task_002138: the agent's VERY LAST Bash command is `pkill -9 -f socat`
    — it tears down its own listener as the final action; verifier then gets
    `Connection refused` on 127.0.0.1:9000. This directly violates the weaker
    rule already in the R8 prompt ("do not kill it in your final command"),
    proving the existing one-liner is insufficient and needs an explicit,
    stronger detach-and-prove-and-never-teardown rule.
  - task_000910: agent backgrounds `monitor_daemon &` (plain `&`, no setsid),
    confirms it running mid-session; verifier gets `IncompleteRead / Connection
    broken` on the HTTP endpoint.
- Why Instruction not Control: the failure is the agent not KNOWING that
  `nohup &` is insufficient across the phase boundary and that its final
  teardown kills the service — a knowledge/ordering gap, not a missing mechanism
  or a mis-parsed tool return. A Control processor that blocked the final
  `pkill` was considered and rejected: (a) legitimate final cleanup (removing
  stray temp files, killing a test-only helper) is sometimes correct, so a hard
  block adds real regression surface on passers and non-server tasks; (b) the
  harness cannot know from the command text alone which process is the required
  service vs a scratch helper; (c) a processor cannot fix the deeper "used plain
  `&` so it got reaped" case at all — only the launch strategy does. The launch
  strategy is agent-authored per task, so the right lever is teaching the
  strategy, not injecting a mechanical guard. Why not Configuration: no existing
  knob governs service persistence.
- Retroactive check (A-corrective): partial-yes. On task_002108 the process was
  demonstrably alive at session end but unreachable at verify — a proper
  `setsid` detach + post-launch reachability re-check from a fresh Bash call
  would have surfaced/fixed the phase-boundary death before exit. On task_002138
  the "never end with a teardown" rule would have stopped the agent killing its
  own listener. Honest caveat: several server tasks (task_000796 broken key
  decrypt, task_001013 401 auth logic, task_001037 server code crashes with
  returncode 1) also carry a server-CODE capability bug the rule cannot fix; the
  gain is concentrated on the persistence/teardown sub-shape, not all 8.
- expected_global_gain: The server/service cluster is the single largest
  harness-adjacent failing cluster (8/8 failing, cutting across security,
  system_administration, file_operations, scientific_computing, data_processing
  domains). A general detach+prove+no-teardown discipline can flip the subset
  whose server code is otherwise correct but was killed at the phase boundary,
  and it generalizes to any future task requiring a live listener at check time.
- regression_risk: LOW. Change is additive, strategy-only, in the sibling
  system_prompt.txt; the processor pipeline and config.yaml are byte-identical
  to R8 (md5 044420c4291e005fa03fa83dd25d6b82). No new literals, no task ids, no
  code to copy. Risk: the extra "verify reachability in a separate call" step
  adds a few Bash round-trips on server tasks; mitigated because those tasks
  already fail and the check is cheap. Non-server tasks are unaffected (the
  section is explicitly conditional on "if the task requires a process to be
  running when checked"). No processor fires on any of the 11 passers this run
  (verified: LoopTerminator=0, BLOCKED=0 on all passers; only the idempotent
  VERIFIER DEP no-op banner appears), so the stable pipeline is preserved.
- cost_shift: Neutral-to-slightly-up on the server cluster only (a couple of
  extra reachability-check Bash calls); negligible elsewhere. The
  don't-loop/bank-partial-credit rules retained from R2/R8 continue to reduce
  wasted budget on the runaway tail.
- rollback_trigger: Revert to the R8 prompt if R10 pass_rate < R9 baseline OR a
  previously-passing task regresses with over-eager server relaunching visibly
  consuming its budget.
