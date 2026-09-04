# Candidates — R2 / c1

Assigned focus: `task_000015_89886d8d` (software_engineering) fails —
`migrate.py` and `test_parser.py` never created.

## Root-cause of the assigned task

The agent needed to OCR `/app/routing_schema.png` with `tesseract`. It
issued a `tesseract IMG -c tessedit_char_whitelist=... --psm 6 -o raw.txt`
call whose CLI syntax is wrong (tesseract takes the OUTPUT BASE as a
positional arg and has no `-o`), so tesseract printed
`read_params_file: Can't open ...` every time. The 4B model then
re-issued the *same* invocation (raw, then via `python3 -c`, then via
heredoc) ~13 times, interleaved with `finish_reason=length` truncations,
and burned all 39 steps without ever writing the two required files.

This is a **harness deficiency, not a capability gap**: the pipeline
already has two SOFT guards for loops (`LengthTruncationRecoveryProcessor`,
`RepeatedCommandRecoveryProcessor`) and both *fire*, but the small model
ignores their text nudges and keeps looping. Two mechanical gaps let the
loop persist: (1) interleaved length-truncation turns reset the strictly-
consecutive counter in the existing repeat guard; (2) a text nudge has no
teeth — the model still gets the failing command executed.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `LoopBreakerProcessor`: after the SAME normalised (command, result)
signature recurs `block_threshold` (6) times within a rolling window
(interleave-tolerant), REFUSE the next attempt at that exact signature
(`approved=false`) and inject a synthetic result demanding a materially
different command or writing the required deliverable.

- Tasks affected (>=2 distinct, same mechanism — stuck identical-result loop,
  reward=0):
  - `task_000015_89886d8d` — same `tesseract` invocation repeated ~13x
    (`read_params_file: Can't open ...`), files never written.
  - `task_001818_b251e5ea` — same `cat > src/main.rs << 'EOF'` heredoc
    repeated ~33x with the same result.
  - `task_000118_3043e92d` — same `ps aux | grep -E "worker_sim|python3"`
    repeated ~26x with the same result.
  - `task_000010_644ab1c2` — same `cat > operator.py << 'EOF'` repeated ~10x
    (also 19 length-nudge messages seen and ignored).
  - Also present: `task_001031_a8f0eb37` (~18x), `task_001321_658ce4a8`
    (~10x), `task_000344_e265c898` (~9x) — all reward=0.
- Signal: per-task scan of `.messages.json` — max count of a single
  normalised tool-result within the trajectory is 9-34 on these failing
  tasks vs <=3 on passing tasks; `notool_asst` (finish_reason=length turns)
  interleave the loop, resetting the existing consecutive counter.
- Verified (Read):
  - `task_000015` steps 5-37: 17 assistant turns, each re-issuing the same
    tesseract call, tool results all `read_params_file: Can't open
    tessedit_char_whitelist=...`; steps 41/51/55/59/65/67/71/73 are
    `finish_reason=length` "your previous response was cut off" turns that
    interleave and reset consecutive counting. Final files absent.
  - `task_001818`: top repeated command `cat > .../main.rs << 'EOF'` count
    33; 4 nudge/limit messages present and ignored.
  - `task_000118`: top repeated command `ps aux | grep -E "worker_sim..."`
    count 26; 2 nudge messages present and ignored.
  - `task_000010`: top repeated command `cat > operator.py << 'EOF'`
    count 10; 19 nudge/limit messages present and ignored.
- Why Control not Instruction: the Instruction/soft-nudge layer already
  exists (both LengthTruncation and RepeatedCommand recovery inject text
  telling the model to change approach) and is demonstrably ineffective on
  this model — the nudges appear in-context 2-19 times and are ignored. The
  missing piece is a *mechanical* refusal to execute the loop, which only a
  Control hook (`on_before_tool` + `approved=false`/`synthetic_result`, the
  same mechanism `BgInstallGuard` uses) can express.
- Why Control not Configuration (retune the existing guard): the existing
  guard keys on strictly-consecutive identical results; interleaved
  length turns break the run, so lowering its threshold would not fix the
  interleave problem, and it would still be a toothless text nudge. A new
  mechanical hook is required, not a knob change.
- Retroactive check (A-corrective): yes — had the loop been refused after 6
  identical failures, the agent would have been forced off the broken
  `tesseract` invocation (and off the repeated heredoc / ps loops) with
  ~30+ steps of budget still remaining, giving it room to try the correct
  positional-arg syntax or a different route and write the deliverables.
  The refusal is targeted (only the offending signature) so it cannot
  block a genuinely different next command.
- expected_global_gain: the stuck-identical-result loop is the dominant
  failure shape (7+ reward=0 tasks with max-repeat >=9). Even flipping a
  fraction is net positive; the mechanism generalises to any task where the
  small model perseverates on a failing command.
- regression_risk: a legitimate poll/retry loop could be blocked IF its
  output is byte-identical across >=6 attempts. Mitigations: threshold is
  high (6); polling that waits for a state change produces *changing*
  output so it never accumulates; only the exact offending signature is
  refused (any different command passes); block is one-shot per signature.
  Rollback trigger: if R3 pass_rate is flat/down AND any previously-passing
  task that relies on a repeated identical command (idempotent re-run /
  fixed-output poll) regresses T->F, revert.
- cost_shift: net *reduction* — blocking a 10-34x identical-command loop
  early saves the tokens those repeated executions + re-primed narration
  would have consumed. Negligible added cost on non-looping tasks (one dict
  lookup per tool call).
