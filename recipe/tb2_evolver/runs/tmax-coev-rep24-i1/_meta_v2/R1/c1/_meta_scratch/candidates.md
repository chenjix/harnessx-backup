# Candidates — Round 1 (tmax-coev-rep24-i1)

Assigned focus: `task_000015_89886d8d` fails (reward 0, accuracy 0.0000).

## Diagnosis of task_000015

OCR-driven schema-migration task. On its **first** `tesseract` call the agent
got garbled-but-usable OCR that showed a flat tuple mapping
(`product_id "<item id>"`, `category "<department>"`, `order "<sort order>"`).
Instead of parsing that, the agent re-ran an essentially identical
PIL+tesseract preprocessing snippet **9 times** (all equally garbled), hit the
`max_tokens` repetition loop 3 times, and finally guessed a WRONG output schema
(nested `{"path":{},"query":{}}` with invented key names `department`/
`sort_order` instead of the OCR-visible `category`/`order`). Golden output was
flat → accuracy 0.0.

Two sub-causes: (1) **schema misreading** — a model capability gap; no harness
fix injects the right answer, logged as skip in the journal. (2) **degenerate
re-run of a near-identical command with no new information** — a generalizable
harness deficiency, addressed below.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatCommandGuardProcessor` that detects when the agent re-runs an
essentially identical Bash command `threshold` times (normalized signature,
heredoc bodies stripped) and injects a "stop repeating — re-read the output /
task or change strategy" nudge into the tool result.

- Tasks affected: task_000015_89886d8d, task_001031_a8f0eb37,
  task_000028_7fe033ac, task_000118_3043e92d (also task_001032, task_001321).
- Signal: `exit_reason ∈ {done(no_tool_calls), budget_exceeded}` on failing
  cluster; per-trajectory command histograms show one command signature
  dominating tool calls with identical results.
- Verified (body-quoted):
  - task_000015: same `python3 << 'EOF' ... PIL Image ... tesseract` snippet
    run **9×**, each returning "Legacy to V2 Schema Mapping\n1Leatalog..." —
    identical garbled OCR; agent never acted on the first result.
  - task_001031: same `python3 -c "from mpi4py import MPI ..."` snippet run
    **19×** → `budget_exceeded` at 80 steps.
  - task_000028: same `ffprobe -v error -select_streams v:0 -count_frames`
    invocation run **14×** (48 total tool calls).
  - task_000118: `ps aux | grep ... python` polled **7×**, `ls -la logs/` **6×**.
- Why Control not Instruction: the agent already "knows" not to repeat in the
  abstract (the base prompt says "act"); the failure is a runtime loop the model
  cannot see it is in. A prompt rule cannot detect "you've now run this 9 times"
  — only a stateful hook counting normalized signatures across turns can. The
  nudge is delivered exactly when (and only when) the loop is detected.
- Why Control not a Configuration tweak of CustomEditToolProcessor: that
  processor keys on *written file paths* only (via redirect/sed/tee regex), so
  it is blind to read/exec/build/test/probe re-runs — the OCR, MPI, ffprobe and
  ps/ls loops above write nothing. Retuning its threshold cannot cover them.
- Retroactive check (A-corrective): yes — in task_000015 a nudge after the 3rd
  identical OCR result ("the output already contains what you need; re-read the
  task's required format before writing") lands well before the length-loops
  and the wrong-schema commit, giving the agent budget + a prompt to re-read.
  For task_001031/028/118 the nudge fires long before budget exhaustion,
  redirecting from the dead-end loop to a different approach.
- expected_global_gain: attacks the largest shared failure shape in the round
  (degenerate command loops across ≥4 failing tasks incl. all 3 non-target
  budget_exceeded); plausibly flips 1-3 and reduces wasted steps on several more.
- regression_risk: a passing task that legitimately re-runs the same command
  (e.g. polling a service until ready) could see the nudge — mitigated by
  `max_fires=3`, re-arming only after a different command, and threshold=3 so a
  couple of legitimate retries never trip it. The nudge is advisory text in the
  tool result; it never blocks execution, so worst case is a few extra tokens.
- cost_shift: net negative to neutral — the guard curtails long identical-command
  loops (fewer steps on the failing cluster); adds ~120 tokens per fire, capped
  at 3 fires/task.
