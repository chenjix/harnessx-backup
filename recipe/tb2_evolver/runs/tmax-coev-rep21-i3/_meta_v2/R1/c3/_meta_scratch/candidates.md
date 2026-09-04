# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the stock `CustomSelfVerifyProcessor` with a `StrictSelfVerifyProcessor`
whose one-shot verification message forces the agent to (1) enumerate the task's
LITERAL requirements verbatim (exact output formats, exact counts, behavioural
verbs like "append"/"idempotent", thresholds) and (2) prove EACH one with a
command whose output is compared against the spec — explicitly rejecting a single
happy-path run as proof.

- Tasks affected (assigned focus + same-mechanism cluster):
  - task_000069_41f1682c — deploy script; "redirect (**append**)" misread as `>`
    (truncate). Its own idempotency test saw 1 log line after a 2nd run and
    declared success; verifier expected the log to grow to 2 lines.
  - task_001979_a1e24b6f — CSV task requiring values "rounded to 2 decimal
    places"; agent emitted `33.0` instead of `33.00` and asserted format
    correct without diffing against the literal spec.
  - task_000409_ff1246d3 — output byte-mismatch vs oracle
    (`41225,0.00,0.00,CAT` expected vs `99614,-100.00,-100.00,CAT` produced);
    agent committed on a happy-path run without comparing to the exact spec.

- Signal: `exit_reason=done` / `finished=no_tool_calls` with `reward=0` — the
  agent believed it was finished. `final_pytest` assertions are all
  literal-mismatch shapes (exact count, exact decimal format, exact byte
  output), not crashes or missing files.

- Verified (Read, body-quoted):
  - task_000069 last assistant turn (after `_tb2_self_verify` fired at msg with
    id `sv-30fa0f1f`): agent asserts "The deployment script is idempotent -
    running it again produces the same result and doesn't append duplicate lines
    to the log file" and its script uses `... /home/user/edge_router >
    /home/user/deployment.log` (truncate). It never tested the literal "append"
    requirement — final_pytest: "deployment.log should have exactly 2 lines
    after running twice, but has 1."
  - task_001979 last assistant turn (after `_tb2_self_verify` at msg 15): agent
    writes "✅ Rolling statistics ... rounded to 2 decimal places" and "The
    expected output matches the actual output exactly", yet emitted `33.0`.
    final_pytest: "At index 4 diff: '33.0' != '33.00'." The self-verify pass
    accepted a prose assertion instead of a byte-comparison.

- Why Control not Instruction: the missing behaviour is a *mechanical* one that
  must fire uniformly on the exit boundary of every task — intercept the first
  no-tool-call exit attempt and inject a verification turn. The system prompt
  (`system_prompt.txt`, 306 chars) is static and read once; a generic prompt
  rule cannot re-fire at the decisive commit moment the way the existing
  keepalive-driven processor does. This is not a new capability (Action): the
  agent already has Bash and can run every check — it just skips the literal
  comparison. It is an existing mechanical hook (`CustomSelfVerifyProcessor`)
  whose injected content is too weak, so the fix is a re-parameterised /
  replaced Control processor, not a new tool. Chose a replacement class over a
  Configuration knob because the stock processor hardcodes its message string
  with no constructor parameter to tune.

- Retroactive check (A-corrective): yes — had the strict checklist been present,
  task_000069's STEP-1 enumeration would have surfaced the literal word
  "append", and STEP-3 (run twice, inspect the log delta) would have shown the
  log NOT growing, forcing a `>>` fix before exit. task_001979's STEP-3
  byte-compare of decimals would have caught `33.0` vs the "2 decimal places"
  spec. The agent already had Bash to perform these checks; the only thing
  missing was the directive to actually compare output to the literal spec
  rather than assert compliance in prose.

- expected_global_gain: targets the large `no_tool_calls`/`done`+`reward=0`
  cluster (majority of this round's 42 failures are premature commits, not
  budget/crash). The shared mechanism — committing on prose-level self-assessment
  instead of a literal-spec comparison — recurs across format, count, and
  behavioural-verb tasks. Even a modest flip rate across this cluster is net
  positive.
- regression_risk: low. The processor keeps the exact one-shot keepalive
  mechanics and singleton group of the stock version, so it cannot double-fire
  or block a genuine exit (fires at most once; silent on later no-tool turns).
  Worst case it adds one extra verification turn to already-passing tasks
  (currently 8 passing) — those tasks' final answers already match, so a stronger
  re-check will not flip a pass to a fail. No change to loop control, budgets,
  compaction, or tool registry.
- cost_shift: mildly positive per task on tasks that would otherwise exit early
  — one additional verification turn plus a few Bash checks (bounded, one-shot).
  Negligible on budget_exceeded tasks (they never reach the no-tool exit). Net
  expected cost increase is small and bounded by the one-shot guard.

- Rollback trigger: if the next round shows the passing cluster (currently 8
  tasks) shrinking or overall `reward=0`/`no_tool_calls` count not improving,
  revert to the stock `CustomSelfVerifyProcessor`.
