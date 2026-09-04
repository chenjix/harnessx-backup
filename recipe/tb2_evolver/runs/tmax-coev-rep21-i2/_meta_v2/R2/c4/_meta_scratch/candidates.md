# Candidates — R2 c4

Assigned focus: `task_000069_41f1682c` (fails).

## Diagnosis

`task_000069_41f1682c` exit_reason=`done`, finished=`no_tool_calls`,
reward=0. The agent wrote both required files, ran the deploy script,
and declared success. The verifier fails on ONE requirement: the task
says the script must **"redirect (append) the standard output ... to
/home/user/deployment.log"**, and the verifier clears the log then runs
the script twice and asserts the log has exactly 2 lines. The agent used
a truncating `>` instead of `>>`, so each run overwrites and the log has
1 line. Crucially, the agent DID "test idempotency" (msg 175: re-ran the
script and `cat`'d the log) but only *eyeballed* one output — it never
counted lines after two runs against the spec's stated multiplicity, so
its self-check gave a false positive.

This is primarily a model reasoning slip (append vs overwrite), but it
exposes a real, generalizable harness weakness: the injected self-verify
checklist (`benchmarks/terminal_bench_2/harness.py::_SELF_VERIFY_MSG`) is
too abstract to force (a) requirement-by-requirement matching against the
*literal wording* of the spec, and (b) actually reproducing a stated
repeatability invariant. Its item #4 ("validate your verification
method") is the right idea but does not tell the agent to re-run the
"multiple times" scenario and re-measure observable state.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Replace the stock `CustomSelfVerifyProcessor` with a
`StrictSelfVerifyProcessor` that injects a stronger pre-exit checklist:
enumerate each explicit requirement and confirm the implementation
matches the *literal wording* (append vs overwrite, exact
format/precision/trailing-zeros, exact counts/ordering, exact paths); and
for any "idempotent / run multiple times / does not append duplicates"
requirement, actually re-run (>=2x) and re-measure observable state
(line/row counts, contents) instead of concluding from a single glance.

- Tasks affected: task_000069_41f1682c, task_001979_a1e24b6f
- Signal: `exit_reason=done`, `finished=no_tool_calls`, reward=0, and
  `final_pytest.output_tail` shows a single explicit-requirement mismatch
  (task_000069: "deployment.log should have exactly 2 lines after running
  twice, but has 1"; task_001979: "At index 4 diff: '33.0' != '33.00'" —
  an exact 2-decimal-format requirement the agent stated it met).
  `_tb2_self_verify` fired in both (agent self-verified but missed the
  literal-wording detail).
- Verified (Read):
  - task_000069 messages.json msg 55: script uses
    `/home/user/edge_router > /home/user/deployment.log` (truncate `>`,
    not append `>>`) although the task says "redirect (append)". msg 175:
    the agent's "idempotency test" re-runs the script and `cat`s the log,
    sees one correct line, and concludes "idempotent" — never counts
    lines after two runs. msg 248 final message asserts "verified to be
    idempotent".
  - task_001979 messages.json final assistant: "Calculated rolling
    averages ... rounded to 2 decimal places" — but verifier tail shows
    `'33.0' != '33.00'`; the self-verify `cat` check accepted the output
    as "semantically correct" without matching exact precision.
- Why Instruction not Control: the failure is the agent not knowing to
  verify against *exact wording* / re-run a repeatability scenario — the
  data (spec text, ability to re-run) is fully in the agent's hands. A
  Control hook cannot mechanically know each task's per-requirement
  invariants (append count, decimal precision) without embedding
  task-specific knowledge, which the rules forbid. The right lever is the
  existing self-verify *injection mechanism* carrying a stronger general
  instruction. (Implemented as a subclassed processor only because the
  checklist text is a hardcoded module constant in read-only
  `benchmarks/`; the change is purely to the injected instruction, so its
  lever is instruction.)
- Why not Action / Configuration: no missing capability (Bash suffices to
  re-run and count); no existing knob controls the checklist text.
- Retroactive check (A-corrective): yes — had the strengthened checklist
  fired, item #1 forces the agent to point at the line satisfying
  "redirect (append)" (it would see `>` vs the required `>>`), and item #4
  forces "run twice, then count deployment.log lines" — the missing
  second line is then observable before exit. For task_001979, item #1's
  exact-precision line + item #3's byte-for-byte compare surfaces
  `33.0` vs `33.00`.
- expected_global_gain: Targets the recurring `done`/reward=0
  "declared-success-but-missed-a-literal-requirement" cluster (>=2 tasks:
  append-semantics and exact-format), a shape not covered by any existing
  processor. Generalizes to any unseen spec-detail task because the
  checklist is a strategy, not task knowledge.
- regression_risk: Low. The injection is one-shot (fires at most once per
  task on the first no-tool-call exit) and only ADDS user text; it never
  mutates the system prompt or drops history (contract-checked: 0
  violations). Already-passing tasks receive a slightly longer checklist
  but the extra steps (re-run, count) are cheap and idempotent-safe; they
  do not change correct outputs. The one non-trivial risk is a passing
  task being nudged to re-run a NON-idempotent operation — mitigated
  because the re-run instruction is explicitly gated on the spec itself
  stating a repeatability requirement.
- cost_shift: Small net up on the tasks where self-verify fires — a
  longer checklist plus a few extra confirmation Bash calls (ls / cat /
  re-run + wc -l). Bounded: still one-shot per task, and it displaces the
  wasted budget of shipping a wrong answer that scores 0.
- rollback_trigger: If R3 pass_rate is flat/down AND any previously-
  passing `done` task regresses (e.g. a re-run nudge breaks a
  non-idempotent step, or the longer checklist pushes a borderline task
  over budget), revert to stock `CustomSelfVerifyProcessor`.
