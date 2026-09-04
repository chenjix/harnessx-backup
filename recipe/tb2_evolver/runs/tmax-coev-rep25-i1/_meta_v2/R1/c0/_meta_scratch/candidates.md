# Evolve Candidates — R1 / c0

Assigned focus: **Recovery from tool errors** — find where the agent hit a
failing tool call and then repeated it or gave up; surface the failure legibly
and force a different approach.

## Candidate C-001 — RepeatedCommandGuard (no-progress tool-loop breaker)

**Three-axis tag:** lens=`tool-result-stream` / lever=`control` / intent=`corrective`

### Reusable failure class (agent-observable)
The agent issues a Bash command, receives a result, then re-issues the **same
normalized command** and gets the **same result signature** — repeatedly —
with no change to workspace state or available information. This covers both
sub-cases the brief targets:
- a **failing** tool call retried verbatim (identical error output), and
- an **unchanged success** run in a tight loop (identical stdout you already saw).

Left unchecked the agent burns its step budget on a zero-information loop and
exits `budget_exceeded`. This is detectable purely from `(command, result)`
pairs in the tool stream — no task content required.

### Signal (verified body evidence, R0 trajectories)
- `task_000329_a3ac56b0` (`budget_exceeded`, 80 steps): the exact command
  `cat /home/user/v2.cfg && echo "---" && cat /home/user/v3.cfg` re-issued
  **≥8 times consecutively**, each returning byte-identical output.
- `task_000015_89886d8d` (`budget_exceeded`, 80 steps): identical
  `tee /tmp/ocr.py …` heredoc re-run ≥6 times, each returning `(exit 0, no output)`;
  the existing EditDetection warn fired at step 7 but is warn-only and the loop
  continued to budget death.
- `task_001321_658ce4a8` (`budget_exceeded`, 80 steps): `head -20 raw_dump.txt`
  re-run returning the identical `'utf-8' codec can't decode byte 0x8f` error;
  the agent itself narrated "I've been stuck in a loop trying to run the same command."
- `task_001673_86224c91` (`budget_exceeded`, 80 steps): cyclic write-server /
  identical recursive-CTE query returning the same `A|A|0.0|A`.
- `task_001701_95e3bbcb` (`budget_exceeded`, 80 steps): repeated identical
  `jshon … -u key` returning the same `parse error: type 'object' is not simple`.
- Anchor `task_000010_644ab1c2` is a *related but distinct* recovery failure
  (self-inflicted `operator.py` shadowing; recovered then re-broke). It is the
  evidence anchor, not the class — the class is the loop pattern above, which
  has ≥5 supporting trajectories.

### Retroactive check (would the mechanism have fired?)
Variant: **loop-interrupt**. Replaying the six budget_exceeded trajectories, the
guard's counter reaches `warn_threshold=3` well before step 80 in every case
(the identical command repeats 6–8+ times), appending a legible
"this is not making progress — change approach" nudge into the tool result the
model reads on its very next turn, and escalating at `hard_threshold=5`. In the
25 passing trajectories, no command+result pair repeats ≥3× consecutively
(passing agents progress), so the guard stays silent — verified by scanning that
the passing set does not contain long identical-command runs.

### Why control, not instruction/action
- Not **instruction**: a system-prompt line "don't repeat commands" is exactly
  the guidance the model already ignored — `task_001321` narrated awareness of
  the loop and still repeated. The nudge must arrive **in-band, at the moment of
  repetition**, attached to the offending result. Only a `control` processor can
  do that.
- Not **action** (new tool): TB2 exposes only `Bash`; adding tools is a no-op.
- Not **configuration** (retune existing guards): `CustomEditToolProcessor`
  only counts *writes to the same file* (blind to `cat`/`head`/query loops and
  warn-only); `LengthTruncationRecoveryProcessor` only handles
  `finish_reason=length`. No existing knob covers identical command+result loops.

### Tasks affected (corrective intent → predicted F→T flips / rescues)
`task_000329_a3ac56b0`, `task_000015_89886d8d`, `task_001321_658ce4a8`,
`task_001673_86224c91`, `task_001701_95e3bbcb`, `task_000028_7fe033ac`
(also budget_exceeded). Flipping requires the model to then act on the nudge;
the realistic near-term win is reclaiming the wasted steps so the agent has
budget to try a different approach.

### Pareto
- `expected_global_gain`: recovers step budget for the `budget_exceeded` cluster
  (6 tasks at 80/80 steps in R0) so they can attempt a different approach instead
  of dying in a loop.
- `regression_risk`: low. The guard only appends text to a tool result; it never
  blocks execution or mutates conversation structure (contract-clean, 0 violations).
  Worst case: a benign extra sentence on a legitimately-repeated idempotent probe.
  Reset-on-difference means any progressing agent is untouched.
- `cost_shift`: net **negative to flat** — cuts wasted 80-step loops short; the
  appended nudge is a few dozen tokens per fire, dwarfed by the loop steps saved.
- `rollback_trigger`: if R2 shows the budget_exceeded cluster unchanged AND
  pass-rate flat/down, or if any previously-passing task regresses to a loop-nudge
  false positive, revert.
