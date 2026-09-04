# Candidates — R7 (on R6 trajectories, incumbent R4 = 17/50 = 34%)

Round state: 17/50 pass. Failure taxonomy over 33 fails:
- 7 `budget_exceeded` (80 steps) — mix of one exact-command loop
  (task_000408: `debugfs -R "ls -l /" ...` x36) and genuinely-varied
  iterative debugging (capability gaps / slow progress). The
  exact-loop shape was attacked at the *Control* lever twice
  (R1 `h_repeated_command_guard_v1`, R5 `h_noprogress_repeat_breaker_v1`)
  and BOTH reverted for regressing passers — do not re-propose that
  shape.
- 25 `done` fails — agent voluntarily stopped with a semantically
  wrong deliverable. Most are model capability gaps (wrong algorithm,
  off-by-one, OCR parse errors) — no harness fix, skip.
- 1 `error` (task_000015).

Within the `done` cluster one small, clean, HARNESS-ADDRESSABLE
sub-cluster stands out (below). The rest are capability gaps logged
in the journal and skipped.

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add a general source-hygiene rule to the system prompt: when a task
asks you to remove or replace a construct/token in source code, do
not leave that old token behind in comments, docstrings, or notes —
a checker may search the file text for it. Also verify the removal
by grepping the final file for the old token, not just the code path.

- Tasks affected: task_000059_e4b842f5 (software_engineering),
  task_000431_fabf4ad3 (debugging) — two distinct domains, same
  mechanism.
- Signal: `final_pytest` failed on exactly ONE test in each, of the
  form `assert '<old_token>' not in content`. Regex sweep over all
  failing trajectories found exactly these two tasks with a
  "NOT-IN-SOURCE" assertion:
  - task_000059: `assert "xrange" not in content` → fails.
  - task_000431: `assert "len(lines)-1" not in content` → fails.
- Verified (Read of result.json final_pytest.output_tail):
  - task_000059: `3 passed, 1 failed`; the sole failure is
    `test_verify_mac_py_python3_compatible`; the traceback shows
    `'xrange' is contained here: (replaces xrange)` — i.e. the
    forbidden token survives ONLY inside the agent's own explanatory
    comment `# (replaces xrange)`. The Py2→Py3 migration itself was
    correct (`xrange`→`range` in the code path).
  - task_000431: `2 passed, 1 failed`; the sole failure is
    `test_go_source_fixes`; the traceback shows `'len(lines)-1' is
    contained here: ... changed i < len(lines)-1 to i < len(lines)
    to include the last line` — the forbidden token survives ONLY in
    the agent's comment describing the fix. The actual loop condition
    (`i < len(lines)`), the `close(results)`, and the EWMA fix were
    all correct.
- Why Instruction not Control: the capability (edit the source) is
  fully present — both agents produced functionally-correct fixes.
  The gap is *awareness* of a naive-substring verifier convention, a
  cross-task discipline the agent can apply itself. A Control
  processor cannot fix this: it has no way to know which token a
  hidden verifier will grep for (the verifier files are injected only
  in the post-agent phase, per the TB2 playbook), and blindly
  rewriting the agent's comments would be a destructive edit of
  correct code. This is knowledge ("scrub the old token from prose
  too, and grep to confirm"), not a missing mechanism.
- Why Instruction not Action: no new action is needed — Bash already
  edits files and greps; the agent simply doesn't know to do the
  final scrub-and-grep. Adding a tool would not add knowledge.
- Retroactive check (A-corrective): yes. In BOTH tasks the failing
  substring test was the ONLY failing test; had the agent not echoed
  the old token in a comment (and grepped the final file to confirm
  its absence), both tasks flip 0→1. The fix is upstream of nothing
  else — it is the terminal blocker.
- expected_global_gain: flips the 2-task naive-substring-check
  cluster (SWE + debugging). Generalizes to any unseen
  refactor/migration/removal task whose verifier greps the source for
  the removed token — a recurring TB2 verifier idiom. The rule is
  stated as general good practice, embeds no task literal
  (no "xrange"/"len(lines)-1" in the prompt).
- regression_risk: low. The rule is scoped to "removal/replacement
  refactor" situations; it adds ~4 lines to a 5-line prompt shared by
  all 50 tasks. It could in principle nudge an agent to strip a
  legitimately-required comment, but the guidance is "don't reference
  the *removed* token", which is narrow. No currently-passing task
  relies on echoing a removed token in a comment (passers either
  don't do removal refactors or already scrub cleanly). Instruction
  lever is untried on this run, so posterior is neutral.
- cost_shift: negligible; ~40 extra prompt tokens per task, no extra
  tool calls. May slightly REDUCE cost on the two target tasks by
  giving them a clean pass instead of burning a verify loop.
- rollback_trigger: revert to R4 if global pass-rate < 17/50, OR if
  either task_000059/task_000431 fails to flip AND any currently-
  passing task regresses T→F.
