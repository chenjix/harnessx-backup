# Candidates — Round 1 (focus task: task_000010_644ab1c2)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatedCommandRecoveryProcessor` that detects N consecutive
byte-for-byte identical tool calls returning identical results and injects a
corrective "loop detected — diagnose and try a materially different command"
nudge before the next model call.

- Tasks affected (>=2 distinct failing tasks, same mechanism):
  task_000010_644ab1c2 (focus), task_000958_4bb2b05d, task_001089_220cc46b,
  task_001090_c61c71f2, task_001536_acfe6c35, task_001706_24462a09,
  task_001937_ac874115, task_001673_86224c91, task_001032_1adaccb9,
  task_000338_27d6a1be (15/25 r0 failures exhibit the shape).
- Signal: script over all r0 message logs — consecutive identical
  (tool_input + result) run length. 15 of 25 failing tasks show a run >= 4
  (many 15-33). Almost every task with such a run has reward=0. Existing
  guards do not catch this: `ParseRetryProcessor` only fires on parse
  errors; `LengthTruncationRecoveryProcessor` only fires on
  `finish_reason == "length"`. Identical *successful-parse, non-length*
  repeats fall through.
- Verified (Read, task_000010 body):
  - steps ~15-25: assistant repeatedly emits the identical Bash arg
    `cd /tmp && python3 -c "import sys; sys.path.insert(0,'/home/user'); exec(open('/home/user/operator.py').read())"`
    and each `tool` result is the identical `ImportError: cannot import name
    'deque' ... circular import` traceback — 9 consecutive identical
    (call, result) pairs.
  - assistant text each time is the near-identical narration "The issue is
    that Python is treating operator.py as a module ... Let me try a
    different approach" — but the *next* call is byte-for-byte the same.
  - Only at the 10th attempt does it break out (renaming the file), by which
    point the task ends without leaving the mock API listening → final
    grading pytest fails (`test_mock_api_running`, and pytest itself crashes
    on the `operator.py` shadow / circular import).
  - Cross-task confirmation via the fingerprint script:
    task_000958 run=26 (`grep -n "0.3" httplib.h`), task_001706 run=33
    (`redis-cli LLEN csv_input`), task_001089 run=27 (tcpdump), all reward=0.
- Why Control not Instruction: the agent already *knows* it should "try a
  different approach" — it says so verbatim every turn — yet re-issues the
  identical command. A prompt rule telling it to avoid loops would be
  ignored the same way its own stated intent is ignored. The fix must be a
  mechanical interception that fires *after* the loop is empirically
  observed (identical call + identical result), injecting an unmissable,
  escalating corrective message into the very next model context. That is a
  cross-task guard that must fire uniformly regardless of task — a
  `MultiHookProcessor` on `on_after_tool` / `on_before_model`, mirroring the
  existing `LengthTruncationRecoveryProcessor` which handles the adjacent
  (length-truncation) loop shape.
- Why Control not Configuration: no existing knob expresses
  "identical-call-and-result loop"; `LoopDetectionProcessor` (not in this
  pipeline) keys on step summaries, and the length guard keys on
  finish_reason — neither covers a clean-parse identical-result repeat.
- Retroactive check (A-corrective): yes — on task_000010 the loop begins at
  the 2nd identical failure; with threshold=3 the guard fires before the 4th
  attempt, injecting a message that names the repetition and directs the
  agent to diagnose the ImportError (a name collision — operator.py shadows
  stdlib). That is exactly the reasoning that eventually broke the loop
  (renaming), so pulling it forward ~6 wasted steps leaves budget to run the
  final script correctly AND relaunch the API so grading passes. Same
  mechanism unblocks the other 14 tasks that thrash on an identical failing
  command.

- expected_global_gain: Targets the single largest cross-cutting failure
  shape in r0 — identical-command loops appear in 15/25 failures across 7
  domains. Even converting a fraction of these from "burn budget on a dead
  command" to "diagnose + branch" is a broad, domain-agnostic gain,
  especially in `system_administration` (0/5) where task_000010 lives.
- regression_risk: LOW-MODERATE. The nudge only fires when a call AND its
  result are identical `repeat_threshold` (=3) times in a row. Legitimate
  polling loops (e.g. `sleep 1; check status`) usually return *changing*
  output, so they won't trip; a genuinely idempotent poll that returns the
  same bytes 3x would get one extra advisory user message (cost only, not
  harmful — it just says "try something different if not progressing"). One
  r0 passing task (task_001591) had an identical run of 11 on a
  `# Create test file...` heredoc; it still passed, and the nudge would at
  worst add a harmless message. Rollback trigger: if r1 pass_rate drops OR
  any previously-passing task with a benign repeat regresses, raise
  `repeat_threshold` (e.g. to 5) or revert.
- cost_shift: Net *reduction* expected. The guard replaces long tails of
  wasted identical calls (each a full model turn + tool exec) with an early
  redirect; the injected message is < 120 tokens. Downside is bounded to a
  few extra tokens on the rare benign-repeat task.
