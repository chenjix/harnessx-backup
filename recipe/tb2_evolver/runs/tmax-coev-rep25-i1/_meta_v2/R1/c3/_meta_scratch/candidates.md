# Candidates — Round 1 (c3, focus: Tool surface)

Assigned focus: **Tool surface** — find a task where available tools forced
an awkward/many-step workaround; add/remove/re-describe a tool so the direct
path exists. Anchor: `task_000118_3043e92d`.

## Focus-support finding

The playbook claims TB2 locks the agent to Bash. **That is not true on the
Tmax evolve path.** `recipe/tmax_eval/harness_runner.run_harness_agent` builds
the registry from `HarnessConfig.from_yaml_file(...).tool_registry` and runs
through `harness.run()`. Builtin tools resolve by name via
`build_default_tools()` and every filesystem builtin (`Write`, `Edit`, `Read`,
`Glob`, `Grep`) is **sandbox-aware** — they call `sandbox.write_file` /
`read_file`, which `TmaxDockerSandbox` inherits from the base `Sandbox`
(base64-encode + pipe through the container's `base64 -d`). Verified the base
implementation in `harnessx/sandbox/base.py:65-100`. So the tool surface *is*
live and evolvable here. The focus is supported.

The only tool currently registered is `Bash` (`tool_registry.builtin: [Bash]`).
Every file the agent authors goes through `cat > f << 'EOF'` / `tee f << EOF`
heredocs — a token-heavy, quoting-fragile, whole-file-rewrite path.

## Candidate C-001
[lens: failure | lever: action | intent: corrective]

Register the sandbox-aware `Write`, `Edit`, and `Read` builtins alongside
`Bash` so file authoring/inspection has a direct path instead of heredoc
whole-file rewrites.

- Tasks affected (file-rewrite-loop sub-cluster, all FAIL, all heredoc-heavy):
  `task_000015_89886d8d`, `task_001818_b251e5ea`, `task_000740_59416444`,
  `task_001673_86224c91`, `task_001032_1adaccb9`. Anchor `task_000118_3043e92d`.
- Signal: cross-task scan (`_meta_scratch/scan.py`) — FAIL cluster totals
  158 heredoc writes / 190 duplicate-consecutive commands / 28 EditDetection
  warnings vs PASS cluster 80 / 96 / 1. The failing tail is dominated by tasks
  that re-emit an entire file through a heredoc over and over.
- Verified (Read):
  - `task_000118_3043e92d` steps 25→42: the agent re-writes the *entire* 3.7 KB
    `deployment_monitor.py` via `cat > ... << 'EOF'` six+ times to make small
    logic tweaks; the `EditDetection` guard misfires on phantom paths `=` and
    `SIZE_THRESHOLD:` (heredoc-body tokens matched by the redirect regex), not
    the real file.
  - `task_000015_89886d8d` steps 22→282: 33/33 tool calls are the identical
    `tee /tmp/ocr.py > /dev/null << 'PYEOF'` heredoc; the model cannot make an
    incremental change so it loops on a full rewrite until it exhausts steps
    (6 EditDetection warnings).
- Why Action not Control/Instruction: the workaround is structural — there is
  no non-Bash way to author a file, so the agent is *forced* into whole-file
  heredoc rewrites. An `on_after_tool` Control hook cannot create a new action;
  an Instruction rule ("prefer small edits") is useless when the only tool that
  can edit is Bash-heredoc (which has no partial-edit affordance). The missing
  capability is a targeted-edit / atomic-write primitive → Action. These are
  first-party, sandbox-aware, already-validated builtins (not authored code),
  so regression surface is minimal.
- Retroactive check (A-corrective): **partial.** For the pure repetition loops
  (000015) a Write/Edit tool does not *guarantee* the model breaks the loop —
  that is partly a 4B capability limit. But for the file-rewrite tasks it
  removes the whole-file-rewrite substrate: `Edit` lets the agent change one
  line without re-emitting the file, and `Write` авoids heredoc quoting bugs,
  which both (a) cut the per-edit token cost that pushes long tasks into
  max_steps and (b) stop the EditDetection guard from firing on phantom
  heredoc-body paths. Net: plausibly flips the heredoc-heavy tail and is
  strictly cheaper on the file-authoring path everywhere.

- expected_global_gain: shrinks the file-rewrite-loop failure tail
  (5 FAIL tasks + anchor) by giving an incremental-edit + atomic-write path;
  reduces tokens on every task that authors files (both FAIL and PASS clusters
  used heredocs heavily).
- regression_risk: the model may occasionally misuse `Edit` (old_string not
  found → error string) and retry; but Bash remains available so no capability
  is lost. Larger tool schema slightly increases prompt size (3 extra tool
  defs). Low risk overall — these are the standard first-party builtins used by
  other HarnessX benchmarks.
- cost_shift: expected **down** on file-authoring — a targeted `Edit` replaces
  a full-file heredoc rewrite; fewer duplicate whole-file writes. Small
  fixed +tokens from 3 extra tool schemas in the system context.
- rollback_trigger: if R2 pass_rate is flat/down AND duplicate-command /
  max_steps counts do not fall on the heredoc-heavy cluster, revert to
  `builtin: [Bash]`.
