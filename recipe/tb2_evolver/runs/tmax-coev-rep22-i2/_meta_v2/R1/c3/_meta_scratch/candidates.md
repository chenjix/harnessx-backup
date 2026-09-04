# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatActionBreakerProcessor` that detects consecutive identical
tool calls (same name + same arguments) and injects an escalating
corrective nudge before the next model turn, redirecting the agent out
of a no-progress loop.

- Tasks affected (assigned target + cluster, all reward=0):
  - task_000028_7fe033ac (assigned) — 20 consecutive identical calls
  - task_000118_3043e92d — 31 consecutive identical calls
  - task_000958_4bb2b05d — 26 consecutive identical calls
  - task_001207_44e97fe1 — 25 consecutive identical calls
  - task_000313_1dce9844 — 16; task_000010_644ab1c2 — 12;
    task_001857_24daeef3 — 11; task_001098_f5acdd79 — 33 (exit=error);
    task_001979_a1e24b6f — 42 (exit=error)
- Signal: `exit_reason ∈ {budget_exceeded, error}` correlated with a long
  run of byte-identical `assistant.tool_calls[].function.arguments`.
  Programmatic sweep of all 50 trajectories: every task with
  maxconsec_identical >= 10 has reward=0; no reward=1 task has
  maxconsec_identical > 2.
- Verified (Read of messages.json bodies):
  - task_000028: 20x `{"command":"wait 2>/dev/null; sleep 1; ps aux |
    grep server | grep -v grep"}`, each returning the identical two
    `<defunct>` lines; assistant repeats the same narration
    ("The zombie processes are still there. Let me try a different
    approach - use `wait`...") then re-issues the same command. Never
    completes objectives → budget_exceeded at step 80.
  - task_000118: 31x `{"command":"rm -f /home/user/logs/*; ls -la
    /home/user/logs/"}` — identical no-op loop → budget_exceeded.
  - task_000958: 26x identical `pkill -9 -f server ...; ./server &; ps`
    loop → budget_exceeded.
  - task_001207: 25x identical `log_sanitizer.elf "test/../../.."` loop
    → budget_exceeded.
- Why Control not Instruction: the failure is mechanical and cross-task —
  the agent cannot see that its command output is unchanged turn over
  turn (the summary/compaction machinery even re-primes the same
  narration). A prompt rule ("don't repeat commands") does not fire at
  the decisive moment because the model already believes each attempt is
  a "different approach." A processor that watches the actual tool-call
  fingerprint and injects a state-aware nudge exactly when the loop is
  detected is the mechanism that closes it, uniformly across all tasks.
- Why Control not Configuration: no existing knob detects identical-call
  loops. `LengthTruncationRecoveryProcessor` only fires on
  `finish_reason=length`; `ParseRetryProcessor` only on malformed calls.
  These loops are well-formed calls that finish normally — a new hook is
  required, not a tuning.
- Retroactive check (A-corrective): yes — in every cited task the loop
  begins well before the step budget is spent (task_000028 loops from
  ~step 50 with 30 steps still available; task_000118/958/1207 similar).
  Injecting a "you are looping, take a different action" message at the
  3rd identical call gives the agent 20-70 remaining steps to pursue the
  real fix (reap zombies via a spawned reaper, fix the actual socket/log
  bug, etc.). The agent had already located the relevant files earlier in
  each trajectory, so it had the context to act once unstuck.
- expected_global_gain: closes a 9-task failing cluster (all currently
  reward=0, most burning the full 80-step budget) that spans multiple
  domains (process management, log rotation, path sanitization). Any of
  these that had a solvable root cause becomes reachable once the loop is
  broken; even partial recovery reclaims budget.
- regression_risk: very low. The processor is advisory-only: it never
  blocks, kills, or rewrites a tool call — it only appends a user message
  after >=3 byte-identical consecutive calls. No passing task in the
  round has maxconsec_identical > 2, so the threshold of 3 never fires on
  the currently-passing cluster. It replaces (not stacks) a trailing user
  message to stay contract-safe.
- cost_shift: net negative-to-neutral. Tasks that currently spin 20-40
  identical no-op calls to budget_exceeded will either break out earlier
  (fewer steps) or, at worst, spend the same budget with a few extra
  tokens per nudge. Passing tasks are unaffected (never fires).
