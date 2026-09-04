# Candidates — R1/c3

Focus: `task_000118_3043e92d` (`system_administration`) failed with
`exit_reason=budget_exceeded` after 80 steps / 924s.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepetitionLoopBreaker` processor that detects near-identical
repeated assistant narration across consecutive tool-less turns and
hard-breaks the loop by pruning the poisoned duplicate assistant
messages from context and injecting a single decisive "one command
only" redirect; also lower `LengthTruncationRecoveryProcessor.head_chars`
1200→700 so the collapsed runaway narration re-primes the next
completion less strongly.

- Tasks affected: `task_000118_3043e92d` (primary). Mechanism is
  generic (content self-similarity across turns), so any task that
  enters a verbatim narration loop is covered — this is a harness-wide
  failure class, not a task-specific fix.
- Signal: `agent.finished=budget_exceeded`, `steps=80`, `elapsed_s=924`;
  the message log shows the assistant emitting the identical block
  "The user is right - I've been stuck in a loop. Let me take a concrete
  action..." followed by the same "In the earlier test: - Second 1..."
  narration on ~10 consecutive turns, each cut off by the token limit,
  interleaved with passive "Your previous response was cut off by the
  token limit. Please continue..." user nudges.
- Verified (Read messages.json):
  - Turns at lines 10-13, 18-21, 26-29, 38-41, 46-49, 54-57, 62-65,
    70-73, 78-81, 86-89, 94-97, 102-104 are byte-for-byte the same
    narration prefix ("The user is right - I've been stuck in a loop...
    In the earlier test: - Second 1: Disk size 4096 bytes, 3 workers...").
  - The passive continue nudge at lines 15-16, 22-24, 30-32, 42-44,
    50-52, ... re-primes the same runaway generation each time.
  - The one turn (line 104) that *did* break out and issue a Bash call
    only happened after ~12 wasted turns, then the agent got a real
    tool result (line 218: `209719296 /home/user/logs/`) but immediately
    fell back into the identical narration loop (lines 244, 252, 260,
    268...) and never recovered before budget_exceeded.
- Why Control not Instruction: the loop is a *mechanical* context
  pathology — the model's own prior verbatim narration is in the context
  window and outweighs any prompt rule or soft nudge (the existing
  `LengthTruncationRecoveryProcessor._NUDGE_REPEAT` escalation was
  already present and was ignored on every looping turn). No prompt
  instruction can win against stale context it cannot see; only a hook
  that *removes* the poisoned messages and redirects can break it. This
  needs to fire uniformly across every task, which a per-call tool
  cannot express.
- Why Control not Configuration: the existing length-recovery processor
  already had `repeat_threshold=2` and an escalating nudge — tuning its
  knobs further does not add the missing capability (context pruning +
  loop detection that is independent of `finish_reason`). Several looping
  turns ended without `finish_reason=length`, so the existing processor
  never fired on them; a new detection mechanism is required. The
  `head_chars` lowering is a complementary config tweak bundled in, not
  the primary fix.
- Retroactive check (A-corrective): yes — had the breaker been active,
  the 3rd identical narration turn would have tripped it, pruned the
  duplicated narration, and forced a single concrete Bash call. The
  agent demonstrably *can* act correctly when it breaks out (it did
  issue real commands at lines 104-231 and observed the 200MB state);
  the blocker was purely the loop consuming all 80 steps, not a missing
  capability to fix the monitor script. Freeing ~60+ wasted steps gives
  the agent room to debug and correct the truncation-timing bug.
- expected_global_gain: closes the `budget_exceeded`-via-repetition-loop
  failure class across the benchmark, not just this task. Any task where
  the model degenerates into verbatim narration (a known Qwen-family
  failure mode) recovers step budget instead of dying.
- regression_risk: low. The breaker only fires on ≥3 consecutive
  tool-less turns whose normalised 400-char prefix is identical AND each
  is ≥200 chars — normal short retries and legitimate multi-turn
  reasoning differ turn-to-turn and never trip it. Pruning preserves the
  first (task-anchor) message and only removes exact-duplicate assistant
  turns plus passive continue nudges. Worst case on a false positive: one
  extra redirect message and a slightly shorter context, which is benign.
  Lowering `head_chars` to 700 keeps enough tail context for genuine
  continuation while trimming re-priming prose.
- cost_shift: net negative (cheaper). Tasks that previously burned 80
  steps looping now terminate the loop early and either finish or fail
  fast, reclaiming tokens. No added cost on non-looping tasks (the hook
  is a cheap fingerprint comparison per turn).
