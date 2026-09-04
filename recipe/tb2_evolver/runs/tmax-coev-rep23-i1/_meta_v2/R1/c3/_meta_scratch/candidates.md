# Candidates — Round 1, proposal c3

Assigned focus: `task_000118_3043e92d` (system_administration) fails —
`reward=0`, `final_pytest` peak log dir size = 209,715,200 bytes vs
threshold 45,000,000. Agent wrote a `deployment_monitor.py` daemon but
it never truncated logs during the grader's run.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the stock `CustomEditToolProcessor` with a heredoc-aware variant
that strips heredoc bodies before extracting Bash write targets, so a
single `cat > file << EOF ... EOF` counts as one edit of one file and the
body's `>` characters no longer manufacture phantom "written files" and
spurious `[EditDetection]` over-edit warnings.

- Tasks affected (corrective focus + generalization cluster):
  - primary: `task_000118_3043e92d` (fail)
  - same phantom-path mechanism observed: `task_001937_ac874115`
    (fail; phantom paths `$ERROR_THRESHOLD)}` and `0.02,`),
    `task_000118_3043e92d` (phantom `SIZE_THRESHOLD:`, `=`)
- Signal: trajectory `messages.json` contains
  `[EditDetection] File \`SIZE_THRESHOLD:\``, `File \`=\``,
  `File \`$ERROR_THRESHOLD)}\``, `File \`0.02,\`` — none of these are
  real files. They are fragments of heredoc / awk bodies containing `>`
  comparisons. `_REDIRECT_WRITE_RE` in
  `benchmarks/terminal_bench_2/harness.py::_extract_written_files` scans
  the whole command string, heredoc body included.
- Verified (Read):
  - `task_000118_3043e92d/messages.json` — after the agent runs
    `rm -f .../deployment_monitor.py && cat > .../deployment_monitor.py << 'EOF' ... SIZE_THRESHOLD = 40 * 1024 * 1024 ... if size > max_size: ... EOF`,
    the tool result appends THREE simultaneous warnings:
    ``[EditDetection] File `/home/user/deployment_monitor.py` ...``,
    ``File `SIZE_THRESHOLD:` ...``, ``File `=` ...``. The very next
    assistant turn reads: "I keep making the same mistake ... The system
    is detecting excessive edits ... decided to delete the file and
    create it fresh" — i.e. the phantom warnings actively drove the
    model into an unproductive delete/recreate loop instead of debugging
    the real (test-timing) bug.
  - `task_001937_ac874115/messages.json` — same shape: phantom
    ``File `$ERROR_THRESHOLD)}`` and ``File `0.02,`` warnings alongside
    the real `report.txt`.
  - Reproduced offline: fixed `_extract_written_files` on the exact
    task_000118 heredoc yields only `['/home/user/deployment_monitor.py']`
    (stock yields that PLUS `max_size:`); plain `>`/`>>`/awk redirects on
    non-heredoc commands are extracted identically to stock.
- Why Control not Instruction: the defect is a mechanical
  false-positive in a processor's command parser, not missing agent
  knowledge. No prompt rule can suppress a warning the harness itself
  injects into tool results; the fix must live in the parser that
  produces the warning. And it is not Configuration — no existing knob
  (only `threshold`) can distinguish real writes from heredoc-body
  fragments; the extraction logic itself is wrong.
- Why not Action: no new agent capability is needed; `Bash` already
  writes files fine. The gap is a harness signal corrupting the
  conversation, addressed entirely inside an `on_after_tool` hook.
- Retroactive check (A-corrective): partial-yes. Removing the phantom
  `[EditDetection]` warnings removes the specific derailer that pushed
  task_000118 into the delete/recreate loop and consumed steps it needed
  for the real bug (its self-test used `sleep 1` before deploy and saw a
  clean 4K result, masking the 209MB grader outcome). The underlying
  monitor-timing bug is a model reasoning gap the harness should not
  encode — but the harness was ACTIVELY MISLEADING the agent here, and
  removing that noise materially raises the odds the agent debugs the
  real issue. This is a genuine harness deficiency regardless of whether
  it alone flips the task.
- expected_global_gain: removes a false-signal class that fires on ANY
  task authoring multi-line files via heredoc (the dominant TB2
  file-creation idiom under a Bash-only tool) whose body contains `>`
  (Python comparisons/pipes, awk, shell here-strings). ≥2 distinct tasks
  observed already; the true population is larger since most code-writing
  tasks use heredocs. Reduces wasted steps + confusion on the whole
  code-authoring cluster.
- regression_risk: very low. On non-heredoc commands the extractor is
  byte-for-byte identical to stock (same 3 regexes, same skip filter),
  so legitimate over-edit detection on genuinely repeated `>`/`sed -i`/
  `tee` writes is unchanged. The only behavioural delta is that heredoc
  bodies stop generating write targets — which is strictly correct. Edge
  cases (unterminated heredoc, multiple heredocs per line) are handled
  and unit-tested.
- cost_shift: neutral-to-slightly-negative (cheaper). Fewer spurious
  warnings → fewer confused recovery turns → fewer tokens on tasks that
  previously thrashed on phantom over-edit alerts. No added per-step
  work beyond a linear body strip.

Rollback trigger: if global pass_rate drops or the code-authoring
cluster regresses, revert to
`benchmarks.terminal_bench_2.harness.CustomEditToolProcessor`.
