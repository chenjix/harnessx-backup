# Candidates — R3

Mechanism note (carried from R2, re-verified this round): the tmax eval loop
(`recipe/tmax_eval/run_eval.py::_resolve_system_prompt` + `agent_loop.py`) does
NOT execute the `config.yaml` processor pipeline. It resolves ONLY a system
prompt (sibling `system_prompt.txt`, else first `templates/*.j2`, else built-in
default) and runs a fixed single-`Bash`-tool loop at `temperature=0`,
`max_steps=80`. The processor entries in `config.yaml` are inert. The ONLY live
evolvable lever on this benchmark is the system prompt text. Therefore this
round ships a system-prompt refinement and copies `config.yaml` byte-for-byte.

## Candidate C-003
[lens: failure | lever: instruction | intent: corrective]

Sharpen the R2 VERIFY-BEFORE-STOPPING phase from generic
"re-check each requirement" into an ADVERSARIAL check that re-derives the
grader's *exact* test from the literal task wording — process/service names
(via `/proc/PID/comm` or `pgrep -a`, not just "a PID is alive"), forbidden /
"must not" substrings (grep the produced artifact itself, comments included),
and exact counts / formats / ordering (line counts, decimal places, column
counts) — and forbids treating a self-run happy-path as proof of correctness.

- Tasks affected (>=2 distinct, same mechanism — self-verified optimistically,
  failed on exact grader semantics):
  - task_000760_76ba653c (no_tool_calls): grader `assert "/app/model_predict"
    not in content`; agent's script works and does not *call* the binary, but a
    leftover comment `# (reverse-engineered from /app/model_predict)` trips the
    forbidden-substring check. Agent verified functional output, never grepped
    its own artifact for the forbidden literal.
  - task_001090_c61c71f2 (no_tool_calls): grader reads `/proc/{pid}/comm` and
    `assert comm == "monitor"`; agent verified "server running (PID 553)" and
    the `/status` endpoint, but process `comm` is `bash`, not `monitor`. Agent
    checked existence + behavior, never the required process NAME.
  - task_000140_01c78b42 (no_tool_calls): grader `pgrep -f vm_service` must be
    empty; agent stopped with lingering `vm_service` PIDs `['333','585','777']`
    — never ran the exact "no lingering process" check the task states.
  - task_001032_1adaccb9 (no_tool_calls): grader requires the extraction log to
    have exactly 2 lines / zip-slip prevented; agent produced 5 log lines and
    left the slip unhandled — no exact-count / negative-constraint check.
- Signal: `agent.finished == no_tool_calls` with `reward==0`; message bodies end
  in confident `✅`/"All requirements verified" checklists that assert
  functionality while the verifier fails on an exact literal / name / count /
  forbidden-substring the agent never tested against its own artifact.
- Verified (Read of messages tails):
  - task_001090 final steps: agent prints "=== Checking compiled monitor ===",
    "Server running (PID 553)", "/status endpoint returns ..." and a 13-item ✅
    list — but never `cat /proc/<pid>/comm`; grader fails on `comm=='bash'`.
  - task_000760 final steps: agent's own verification harness printed
    "✗ ... binary=3.5 script=3.50" (bc missing) yet the agent concluded
    "predictions are correct ... task is complete"; the produced script text
    still contains `/app/model_predict` in a comment; agent never grepped it.
- Why Instruction not Control: the eval loop does not run processors, so a
  Control hook is physically inert on this benchmark (see mechanism note). Even
  in a live-pipeline world, the correctness criterion is task-specific ("which
  literal / name / count matters") and cannot be derived mechanically by a
  generic post-hook — it must be re-derived from the task text, which is an
  agent-reasoning step. Instruction is the only lever that both (a) reaches the
  running agent here and (b) can shape *how* it verifies without hardcoding any
  one task's answer.
- Why an evolution of R2, not a re-proposal: R2 (`h_verify_before_exit_v1`) was
  ACCEPTED, not reverted, so re-touching this lever is permitted. R2 added a
  verify phase; the residual failures show the agent performs it *loosely*
  (happy-path confirmation). This candidate changes the *kind* of verification
  (adversarial / literal-grader-mirroring), a distinct shape with new body
  evidence — not the same hypothesis re-shipped.
- Retroactive check (A-corrective): yes. If the agent had, before stopping,
  translated each stated constraint into the strict grader-style check —
  `cat /proc/<pid>/comm` (→ would show `bash`, prompting a re-exec so
  `comm==monitor`), `grep -n /app/model_predict reproduce.sh` (→ would flag the
  comment for removal), `pgrep -f vm_service` (→ would show lingering PIDs to
  kill), `wc -l` on the log (→ would show 5≠2) — each decisive failure surfaces
  a fixable delta while step/time budget remains (these tasks stopped at
  8–27 steps, far below the 80 cap). It cannot help genuine value-computation
  gaps (e.g. task_001937 grid=60 vs 50, task_000536 wrong rows) — those stay
  failed but are unaffected.
- expected_global_gain: targets the `no_tool_calls`+`reward=0` cluster
  (10 tasks in R2), of which >=4 are exact-semantic near-misses recoverable by
  adversarial verification; generalizes to any future task whose grader keys on
  a name / forbidden-substring / exact-count/format the agent would otherwise
  self-certify past.
- regression_risk: adds a few Bash verification steps. All 28 passing tasks
  stopped well under the 80-step cap (median ~13); verification cannot turn a
  correct artifact incorrect, and the prompt explicitly says "if every strict
  check passes, stop" to avoid over-exploration. Residual risk: an agent burns
  extra steps re-checking on a task it was already right about — bounded by the
  step cap and small relative to headroom. The 4 `max_steps` failures are
  already at the cap; a longer verify routine will not push a passing task over.
- cost_shift: +1 to ~5 extra Bash steps per task for the adversarial pass;
  modest token increase bounded by `max_steps=80`. Net expected-positive if it
  flips even one task from `reward=0`.
- rollback_trigger: if R4 pass_rate is flat/down vs the 28–30 band while the
  median step count climbs materially toward the cap, revert to the R2 prompt.
