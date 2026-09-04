# Candidates — R2

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add the existing `harnessx...LoopDetectionProcessor` to the pipeline in a
**warn-only** configuration (exact-repeat `warn_threshold=3`, hard-raise
`threshold` and name-only `name_warn_threshold` both set effectively off) so
that after 3 byte-identical consecutive Bash commands the agent gets an
advisory appended to the tool result telling it to change approach — without
ever terminating the task.

- Tasks affected (failing, same mechanism = no-progress identical-command loop):
  task_000015_89886d8d (21× identical OCR python heredoc),
  task_000118_3043e92d (11× identical pkill/restart),
  task_000191_578636e9 (12× identical `go build && go test`),
  task_000257_3cd35e11 (9×), task_000010_644ab1c2 (5×),
  task_000300_7d6b511c (5×), task_000396_e56917e2 (5×),
  task_000442_502566d6 (5×), task_001653_c4cafa73 (25× identical `cat > etl.c`).
- Signal: 11 of 27 R1 failures exited `exit_reason=budget_exceeded` at the hard
  80-step cap; `error` on 1 more. Extracting the Bash command stream shows the
  dominant shape is a **byte-identical command repeated consecutively** with no
  intervening change of approach (max consecutive-identical run: 015=21,
  1653=25, 191=12, 118=11, 257=9). The agent re-issues the exact same failing
  command instead of diagnosing why it fails, burning the entire step budget.
- Verified (Read of `<task>.messages.json` tool-call stream):
  - task_000015: the same 21-line `python3 << 'EOF' … pytesseract …` heredoc
    appears 21 times verbatim, output unchanged each time.
  - task_001653: `cat > /home/user/etl.c << 'EOF' …` written 37 times, 25 of
    them in an unbroken consecutive run.
  - task_000118: `pkill -9 -f python3 …; sleep …` repeated 11× consecutively.
  - task_000191: `cd …/log_analyzer && go build -o … && go test -bench=. …`
    repeated 12× consecutively.
- Why Control (add existing processor) not Action/Instruction: the fix is a
  mechanical cross-task guard that must fire uniformly on every task's tool
  stream — a per-call tool cannot observe consecutive-call history, and a
  prompt rule ("don't repeat commands") is not enforced and the R0/R1 prompt
  already implies iterative refinement. The mechanism already exists as a
  tested harnessx processor; the deficiency is purely that it is absent from
  the pipeline. This is the Configuration/Control boundary — reusing a shipped
  component with tuned knobs beats authoring a new one.
- Why warn-only (threshold=999) not the default hard-raise (threshold=5): 3
  currently-PASSING tasks also repeat identical commands consecutively
  (task_000878 11×, task_000890 9×, task_000505 3×) — they deliberately
  re-run an inspection/verify command and still pass. A hard `LoopDetectedError`
  raise at 5 would convert those passes into `loop_detected` failures (net
  regression). Warn-only appends one advisory line to the tool result; a task
  making genuine progress can ignore it and continue. Name-only Strategy 2 is
  disabled (`name_warn_threshold=999`) because every tool call here is `Bash`,
  so name-only would fire an unwanted warning on essentially every task.
- Retroactive check (A-corrective): yes — at run length 3 the agent still has
  ~60+ steps of budget left (loops start early: 015/118/191 loop well before
  step 40). An advisory to "stop re-running the identical command and try
  something fundamentally different" applied at the 3rd identical call gives
  the agent the remaining budget to change approach and produce a correct
  deliverable, instead of exhausting all 80 steps on the same dead command.
- expected_global_gain: the largest single failing cluster (11 budget_exceeded
  + 1 error = 12 tasks) shares one root cause — identical-command no-progress
  loops. Even flipping a subset (2-4) is a net gain, and the mechanism
  generalizes to any future task that thrashes.
- regression_risk: bounded. Warn-only never terminates a task, so no pass can
  become a loop_detected failure. The only cost to the 3 passing repeaters is
  one advisory line in a tool result they can ignore. Small risk the advisory
  distracts a task that was legitimately re-running to poll a slow process;
  mitigated by requiring 3 *byte-identical consecutive* calls (polling loops
  usually vary sleep/target or interleave other commands).
- cost_shift: near-neutral to positive. Adds a few tokens per warning-bearing
  task; but converting 80-step budget_exceeded runs into earlier, shorter
  completions should *reduce* median steps/cost on the affected cluster.
- rollback_trigger: if R3 pass_rate is flat/down while budget_exceeded count is
  unchanged, or if any previously-passing repeater (878/890/505) flips to F,
  revert this processor.
