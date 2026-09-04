# Candidates — R2 (focus: task_000396_e56917e2, silent wrong-answer cluster)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the stock existence/format self-verify with a two-barrier,
correctness-focused self-verify processor that forces an adversarial value
audit and an independent cross-check before the agent may exit.

- Tasks affected (exit_reason=done, finished=no_tool_calls, reward=0 —
  agent believed it finished; verifier's value/behaviour check disagreed):
  task_000396_e56917e2, task_000505_50b5162d, task_001653_c4cafa73,
  task_000140_01c78b42, task_000015_89886d8d (cluster is ~15 tasks of this
  shape in R0; these 5 are body-verified below).
- Signal: `agent.exit_reason=done` + `finished=no_tool_calls` + `reward=0`;
  `final_pytest` fails on a VALUE/behaviour assertion (threshold, exact
  match, clean-state) not a missing file. In every cited case the stock
  `_tb2_self_verify` DID fire (`has_self_verify: True`) yet the agent
  rubber-stamped it (re-listed files, "format matches") and exited.
- Verified (Read of *.messages.json):
  - task_000396 msg 42-53: computed max deviation `0.576768`, wrote it to
    validation.log, self-verify fired at msg 53, agent re-checked only that
    files exist + bug line is `0.25`, exited. Task text: "the integration
    diverges and fails to match the analytical reference" → verifier wants
    deviation < 0.1; agent never reconciled 0.576 against "should match".
  - task_000505 msg 15-19: self-verify fired; agent only re-ran `ls` on
    detect_trojan.sh and re-narrated; verifier: "2 of 2 evil bypassed" — the
    detector never caught the trojans because the agent tested only `/bin/ls`
    (happy path), never a crafted positive.
  - task_001653 msg 11-15: self-verify fired; agent confirmed "output format
    matches exactly" but the centroid/distance VALUES were wrong
    (`36.36..` vs expected `42.00..`); never recomputed a second way.
  - task_000140 msg 20-26: self-verify fired; agent confirmed files exist;
    verifier: "Lingering vm_service processes found ['338','591','790']" —
    never checked final clean state with pgrep.
  - task_000015 msg 66-73: self-verify fired; agent ran a self-authored test
    that passed, exited; verifier accuracy `0.3389 < 0.98` — the agent's own
    test did not exercise the real golden corpus (happy-path test).
- Why Control not Instruction: the stock verify is already a processor-
  injected checklist and the model already receives it — an Instruction/
  system-prompt line saying "check your values" would be one more passive
  sentence the model narrates past, exactly as it does today. The fix needs
  a *mechanical* second barrier that detects the rubber-stamp path (exit
  again with zero new tool calls since the audit request) and forces an
  actual cross-check command. Only a hook can observe "no new real tool call
  since barrier 1" and re-inject. Content of the barrier is stronger than the
  stock message but the load-bearing part is the mechanism, not the prose.
- Why Control not Action: TB2 exposes only Bash (playbook); no new tool is
  addable and none is needed — the agent already has Bash to recompute /
  re-test; it just needs to be forced to before exiting.
- Retroactive check (A-corrective): yes for the checkable-criterion subset.
  task_000396: an independent reconciliation ("does 0.576 match a 'should-
  match-reference' goal?") flags the result as wrong → agent keeps fixing
  (interpolate the comparison / find the second integrator bug) instead of
  reporting. task_000505/task_000140: an adversarial/edge test (crafted evil
  binary; pgrep after cleanup) surfaces the failure the happy path hid.
  task_001653/task_000015 are partially capability-bound (the agent may still
  miscompute) but the "recompute a second way / test the real corpus" barrier
  gives a genuine second chance rather than a rubber stamp.

- expected_global_gain: Flips a chunk of the ~15-task done/reward=0 cluster
  (largest failing cluster in R0 not addressed by R1's budget_exceeded fix)
  by converting the passive one-shot verify into an active correctness audit
  that catches self-evidently-wrong values and happy-path-only tests.
  Generalizes to any task with a stated success criterion.
- regression_risk: Low. Same singleton group replaces the stock verify, so
  no net processor added to the pipeline. Barriers fire only on exit-intent
  turns; phase 2 fires at most once and only when the model re-narrated with
  zero new tool calls, then always yields to exit — a genuinely-finished run
  sees at most one extra confirming command. Risk of a correct run being
  pushed to "fix" a correct value is bounded by the one-shot phase-2 cap.
- cost_shift: +1 to +3 Bash round-trips on runs that would otherwise exit
  after a rubber-stamp; negligible on runs that already do real verification
  (they trip the "new tool call since phase 1" guard and skip phase 2).
  Net expected positive: turns spent confirming correctness are cheaper than
  a wasted 0-reward task.
