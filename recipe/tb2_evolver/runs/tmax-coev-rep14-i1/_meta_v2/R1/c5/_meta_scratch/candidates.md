# Candidates — R2 / c5

Assigned focus: `task_000206_a943669b` (security incident-response Bash task).

## Diagnosis (verified from body)

`task_000206_a943669b` — `exit_reason=budget_exceeded`, `steps=80`,
`reward=0`, `initial_pytest.passed=true`. The agent needed to parse
`csp.json` with `jq`, but the environment's `jq` treats hyphenated keys
(`csp-report`, `blocked-uri`) as `report/0 is not defined` compile errors.
Instead of switching tools, the model re-issued **byte-identical** failing
`jq` commands with **verbatim assistant text** ("The issue is that jq is
interpreting `report` and `uri` as variable references ... Let me try using
`.`") — the same command/result pair repeats ~13+ times consecutively
(messages.json rows 11-533), consuming roughly the first third of the step
budget with zero progress. It only escaped after a PostCompaction refresh,
then found the right values interactively, but had no budget left to fix a
buggy `analyze.sh` — the final `findings.txt` was malformed (empty
`Attacker IP:`, doubled `Blocked URI` line) and the run hit step 80.

The harness deficiency: **there is no recovery hook for verbatim tool-result
loops.** `ParseRetryProcessor` handles only unparseable *model* output;
`CustomEditToolProcessor` counts only *write* commands, not repeated identical
*results*; `CustomSelfVerifyProcessor` fires only on a natural no-tool-call
exit, which a budget-exhausted run never reaches. So the fixation ran
unchecked.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `StuckResultBreaker` `MultiHookProcessor` that, on `on_after_tool`,
detects when a tool returns the byte-identical result `warn_threshold` times
in a row and appends an escalating corrective note to the tool result telling
the agent to stop re-issuing the command and switch mechanism/approach —
recovering the run rather than terminating it.

- Tasks affected (same mechanism — verbatim tool-result loop burning budget):
  - `task_000206_a943669b` — jq compile-error result repeated ~13x verbatim.
  - `task_000958_4bb2b05d` — one `sqlite3 SELECT ...` command issued 18x
    consecutively (max-consecutive-identical=18, per prior-round sweep).
  - `task_000118_3043e92d` — identical `wait/sleep/ps aux | grep` command
    repeated 13x consecutively, then a second loop to step 80.
- Signal: `exit_reason=budget_exceeded` at `steps=80` with a run of identical
  tool results / identical assistant text; no progress between repeats.
- Verified (Read):
  - `task_000206_a943669b` messages.json rows 11-533: assistant text
    "The issue is that jq is interpreting `report` and `uri` ..." and tool
    result "jq: error: report/0 is not defined ... (exit 3)" repeat
    identically >10 times before any tool switch.
  - Prior-round journal (R1 c3 evidence, learnings.md lines 108-114) confirms
    `task_000958` (sqlite3 x18) and `task_000118` (wait/ps x13) share the same
    consecutive-identical-command mechanism.
- Why Control not Configuration: the generic `LoopDetectionProcessor`
  (Configuration lever, being proposed separately as c3) *terminates* the run
  on a loop — it reclaims budget by killing the task, which for `task_000206`
  would still score 0 because the malformed `findings.txt` is never fixed.
  C-001 is the opposite strategy: an `on_after_tool` note that breaks the
  fixation *in place* so the reclaimed budget is spent recovering the task.
  Recovery cannot be expressed as a knob on an existing processor — no current
  component watches repeated identical *results* — so it needs a new Control
  hook, not a Configuration tweak.
- Why Control not Instruction: a static prompt rule ("don't repeat commands")
  is already implicitly violated — the model *narrates* trying a different
  approach while emitting the identical command. The corrective signal must be
  injected at the moment of repetition, tied to the concrete count, which only
  a runtime `on_after_tool` hook can do; a system-prompt sentence cannot fire
  conditionally on the live repeat count.
- Retroactive check (A-corrective): yes — had the note fired at repeat #3
  instead of the model looping to ~#13, `task_000206` would have abandoned the
  broken `jq` path ~10 steps earlier (as it eventually did via grep/sed),
  leaving ample budget to run the self-verify checklist, notice the malformed
  `findings.txt`, and fix the two `grep` bugs before step 80.

### Pareto

- expected_global_gain: closes the "verbatim-loop burns the 80-step budget"
  cluster (>=3 tasks share the mechanism) by breaking fixation early and
  returning control to the agent while budget remains — complementary to a
  terminate-on-loop guard, targeting recovery not just budget reclamation.
- regression_risk: Low. The note only appends text to a tool result *after*
  >=3 consecutive byte-identical outputs — a shape no R0-passing task exhibits
  (healthy tasks change their command or its output every step). It never
  drops/rewrites messages (append-only, +0 message count), never terminates,
  and `reset_after_nudge` re-fires the nudge only if the loop genuinely
  persists. Contract check passes (append-only `on_after_tool`).
- cost_shift: Net decrease expected. Breaking a loop at repeat #3 instead of
  letting it run to step 80 reclaims tens of dead steps per stuck task; the
  appended note is ~90 tokens and fires only inside an active loop.
- rollback_trigger: If R3 shows any previously-passing task regressing, or the
  three cited tasks still `budget_exceeded` with the loop unbroken, revert the
  processor (raise `warn_threshold` first if the nudge is simply too weak).
