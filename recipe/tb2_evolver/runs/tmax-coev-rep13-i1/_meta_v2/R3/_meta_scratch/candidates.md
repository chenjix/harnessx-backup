# Candidates — R3 (tmax-coev-rep13-i1)

R2 scored 31/50 (62%). The R1 `RepeatCommandGuard` was advisory-only and
keyed on the command string alone; this round replaces it with a guard that
(a) keys on the *(command, identical-output)* pair so it never touches
healthy iterate-and-test loops, and (b) escalates to a HARD BLOCK so the
model can no longer ignore it.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the advisory-only `RepeatCommandGuard` with `DeadLoopBreaker`, which
keys on `(normalised_command, output_fingerprint)` and, after `block_threshold`
byte-identical repeats, intercepts the next identical Bash call
(`approved=False` + synthetic directive) so the dead command does not execute.

- Tasks affected (failing, dead-loop shape):
  - task_001652_86e1d185 — identical read-only diagnostic run **12×** with
    byte-identical output; ended `agent_error`.
  - task_001536_acfe6c35 — identical command run **7×**, burned to the step cap.
  - task_001032_1adaccb9 — identical `od ... | grep` diagnostic run **4×**
    with identical output, alongside a 6× file-rewrite; burned to step cap.
  (Several more failures sit at 3× identical repeats near the cap:
  task_000010, task_000118, task_000264, task_000958.)
- Signal: `status=agent_error` / no natural `done`; per-task command
  histograms show one Bash command repeated many times with **byte-identical
  tool output** each time; assistant narrates "I've been stuck in a loop"
  then re-issues the same command. The R1 guard's advisory text appeared in
  ~10 tool results on task_001652 yet the model repeated regardless.
- Verified (Read):
  - task_001652 last 6 messages: assistant repeats verbatim "I've been stuck
    in a loop trying to extract text from the image. Let me take a
    fundamentally different approach..." and re-issues the SAME
    `python3 << 'EOF' ... find non-black pixels` command; tool returns the
    identical "White pixels in row 32: 54 ..." block each time (12 identical
    (cmd,output) pairs measured).
  - task_001536: measured 8× identical `cat > sanitize_docs.sh` write + a
    `rm -rf ... && ls` diagnostic returning identical "Exit code: 0 ... output
    directory contents" 2× (part of a 7× command repeat run to the cap).
  - task_001032: measured 4× identical `od -A x -t x1z -v .../docs_v1.tar |
    grep` with identical hexdump output each time.
- Regression guard verified against PASSING high-repeat tasks (measured
  max *consecutive-identical* (command,output) count):
  - task_001031_a8f0eb37 (PASS): `cat > analyze.py` run 22× by command-string
    but only **2** consecutive identical (cmd,output) pairs — the interleaved
    `mpiexec` output changes, so the dead-loop counter never approaches 5.
  - task_001089_220cc46b (PASS): the 24× "repeat" is the malformed `{}`
    empty-argument call (no `command` field) — DeadLoopBreaker ignores it.
  - task_001818 / task_000578 (PASS): max identical (cmd,output) = 2.
  block_threshold=5 sits well above every observed passing count.
- Why Control not Configuration: the R1 guard's *parameters* aren't the
  problem — its whole mechanism is wrong for this model (advisory it ignores,
  and a command-only key that mis-flags productive rewrites). Fixing it needs
  a new hook shape (output-fingerprint keying + `on_before_tool` interception),
  not a knob tweak.
- Why Control not Instruction: the model already narrates awareness of the
  loop in prose ("I've been stuck in a loop") and still repeats — a prompt
  rule telling it to stop is exactly what the R1 advisory already was, and it
  demonstrably failed. Only mechanically refusing to execute the dead command
  changes behaviour.
- Retroactive check (A-corrective): yes — on task_001652 the loop *is* the
  blocker (12 wasted steps to agent_error); blocking the 5th identical repeat
  frees ~7 steps and forces the "fundamentally different approach" the agent
  keeps promising but never takes. Flip is probabilistic (the underlying OCR
  task may still be hard), but the guard converts "0 steps left, dead loop"
  into "steps left, forced off the dead command" — strictly better.
- expected_global_gain: closes the exact-repeat dead-loop cluster (>=3 tasks
  hard, 4 more marginal) that spans security/file_operations/scientific/
  system_administration/data_querying; mechanism is domain-agnostic. The R1
  advisory-only version flipped 0/5; the failure was the *mechanism* (no
  teeth, wrong key), so pulling the same lever with a corrected mechanism and
  fresh (command,output) evidence is justified, not "pulling harder".
- regression_risk: a passing task that legitimately re-runs an identical
  command with identical output 5× would be blocked. Measured max on passers
  is 2 (task_001031/001818/000578) and the 24× case is the empty-`{}` call
  which is excluded — so no observed passer reaches the threshold. The counter
  resets on any output change, so a single differing result un-arms the block.
- cost_shift: net decrease — dead loops are cut off ~7 steps earlier instead
  of burning to the cap; below the block threshold the advisory adds only a
  few hundred bytes, unchanged from R1.
- rollback_trigger: if R4 pass_rate <= R3 AND none of task_001652/001536/
  001032 flip, OR any previously-passing high-repeat task (task_001031,
  task_001089, task_001818, task_000578) regresses, revert to the R2 config.
