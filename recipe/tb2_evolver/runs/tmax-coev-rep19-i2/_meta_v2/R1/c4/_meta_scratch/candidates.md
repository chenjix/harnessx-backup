# Candidates — R1 / c4

Assigned focus: `task_000118_3043e92d` (system_administration) fails.

## Diagnosis of the assigned task

Task: write `/home/user/deployment_monitor.py`, a daemon that pauses 20
`worker_sim.py` processes (SIGSTOP), truncates `/home/user/logs/*.log`, and
resumes them (SIGCONT) whenever the log dir exceeds 40 MB — keeping peak
usage under the verifier's 45 MB ceiling.

The failure has two layers:

1. **Root harness-actionable layer (what I fix):** the agent burned ~40% of
   its step budget in a degenerate loop. It issued the byte-identical command
   `ls -la /home/user/logs/ | head -20` **26 times** (verified: Counter over
   all Bash tool_calls in the messages log = `x26`). The existing
   `RepeatedCommandBreaker` fired its advisory hard-warning ("...has now run 7
   times...", visible in the tool result at msg 3) and the model ignored it on
   every subsequent turn, re-narrating the same "I see the issue... let me
   check the logs directory" text (msgs 2,4,6,...,52 are byte-identical). Only
   an unrelated PostCompaction event (msg 54) broke the loop. The agent then
   rebuilt the monitor from scratch with the budget it had left.
2. **Residual model-capability layer (NOT the harness's job):** after
   recovering, the agent's monitor was timing-flaky (40 MB threshold vs 45 MB
   ceiling = 5 MB headroom against 20 fast writers) and it "verified" success
   with a weak oracle — final `ls` showed 0-byte files (msg 70) so it declared
   done, but the grader measures *peak* size and got 200 MB. Choosing a safer
   threshold / measuring peak is domain reasoning; a harness prompt patch that
   embedded "lower the threshold / measure peak" would be task-specific
   knowledge injection and is explicitly out of scope. Logged as a capability
   note in the journal.

The layer-1 fix does not by itself guarantee this one task flips, but it is
the generalizable harness deficiency the failure exposes and it gives this
(and every looping) task back the budget it needs to iterate. That is the
correct harness intervention.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Upgrade the identical-command loop-breaker from advisory-only to a hard
**block**: past `block_threshold` (8) identical repeats, refuse to execute
the command (`approved=False` + synthetic redirect) instead of merely
appending warning text the weak model ignores. Warn(3)/hard(5) advisory text
is preserved unchanged.

- Tasks affected (primary): `task_000118_3043e92d` — identical command run
  26×, advisory ignored every turn, ~40% of budget lost in the loop.
- Signal: Counter over Bash `tool_calls` normalised by whitespace — one
  command hashes to 26 occurrences; `exit_reason=done` at 65 steps with the
  first ~26 turns being the dead loop. The R0 processor's `on_after_tool`
  only appends text; nothing blocks execution.
- Verified (body-quoted):
  - `task_000118` msgs 2,4,...,52 assistant text byte-identical
    ("I see the issue - the deployment completed without the monitor
    running... Let me check the logs directory"); msg 3 tool result already
    carries `[RepeatedCommandBreaker] STOP. This identical command has now
    run 7 times...`; the loop nonetheless continues ~25 more turns until the
    msg-54 PostCompaction event breaks it.
  - Cross-check that this is the pathological tail, not normal behaviour:
    swept all 50 trajectories for max identical-command repeat. Only
    `task_000118` (fail) reaches the pathological 26; the next-highest are
    three **passing** tasks (`task_000187`, `task_000313`, `task_001098`)
    that cap at exactly 5 (benign re-reads such as `cat <output>.csv`).
- Why Control not Instruction: the model already received escalating
  Instruction-shaped text (the advisory) and demonstrably ignored it 20+
  times. More prompt text cannot stop a model that ignores prompt text; only
  a mechanical refusal (the same `approved=False`/`synthetic_result` path the
  benchmark's own `CustomSelfVerifyProcessor` uses) breaks a hard loop on a
  weak model.
- Why not just lower the existing hard_threshold: the hard threshold only
  changes *when the text appears*, not *whether the command runs* — it would
  not have helped here (the text was already firing). The missing mechanism
  is enforcement, not earlier advice.
- Retroactive check (A-corrective): partial-yes. Had the block been in place,
  the 26× loop would have been cut at repeat 8 (~18 fewer wasted turns),
  returning budget the agent used, post-compaction, to rebuild and test the
  monitor. It does not on its own fix the residual timing/oracle capability
  gap, but it removes the budget drain that is the harness-owned half of the
  failure and applies to every future looping task.
- expected_global_gain: closes the "advisory-ignored runaway loop" failure
  mode for the weak eval model across all domains (command-agnostic). The
  processor's own docstring already claims this pattern recurs broadly; this
  round supplies a concrete 26× instance and makes the guard actually bite.
- regression_risk: LOW. Block fires only at 8 identical repeats; the three
  passing tasks that legitimately repeat cap at 5, so none are touched. A
  synthetic redirect at repeat 8 is strictly less budget-harmful than 18 more
  dead turns. Only conceivable regression: a task that genuinely needs to run
  the identical command ≥8× (e.g. polling for an external event) — not
  observed in any of the 50 trajectories, and such a task should use a
  changing poll command anyway.
- cost_shift: net **negative** (cheaper). Blocking a runaway loop early
  removes wasted model turns/tokens; no new work is added on the common path
  (advisory behaviour unchanged for counts 3–7).
