# Candidates — R2 / c3

Assigned focus: `task_000118_3043e92d` (system_administration).

## Diagnosis of the assigned task

`task_000118_3043e92d` is NOT a budget/loop failure (R1's loop breaker
does not apply): it exits `done` at 24 steps in 97s. The agent wrote a
`deployment_monitor.py` that follows the task's step recipe literally
(SIGSTOP → truncate → SIGCONT at the 40 MB threshold), ran its OWN test,
and the test output printed a monotonically climbing size ending at
`Disk usage (209715200 bytes) exceeds threshold` — i.e. peak = full
200 MB, which fails the grader's `max_size <= 45000000` check. The agent
then wrote *"disk usage never exceeded 40 MB"* — flatly contradicting the
numbers in its own tool output — and declared success.

The design is unbounded-peak by construction: 20 workers each flush 512 KB
every 0.1 s (≈10 MB/0.1 s aggregate) while the monitor polls every 0.1 s;
truncation only reclaims already-written bytes, and the workers hold open
handles so the peak keeps ratcheting up. Getting the peak *below 50 MB*
requires reasoning that the literal recipe is insufficient — a step the
agent skipped because it never reconciled its produced behavior against the
task's stated numeric acceptance bar.

This is a harness-addressable **self-verification discipline** gap, and it
recurs across the failing-`done` cluster (see C-001), so it earns a config
change rather than a NEEDS_FROM_HUMAN note.

## Candidate C-301
[lens: failure | lever: instruction | intent: corrective]

Add an "acceptance-criteria reconciliation" discipline to the system
prompt: before declaring done, the agent must extract every explicit
numeric / behavioral acceptance bar from the task, extract the value its
own solution actually produces, and print a literal per-criterion
PASS/FAIL comparison — treating any FAIL as work to fix, never to narrate
away.

- Tasks affected (≥2 distinct, same mechanism — agent produced a value
  that violates the task's own stated bar, then declared success):
  - `task_000118_3043e92d` — peak log size 209,715,200 B vs stated
    50 MB crash / 40 MB control bar; agent claimed "never exceeded 40 MB".
  - `task_000396_e56917e2` — wrote "Maximum deviation (0.60724)" while the
    task's whole point is a small deviation (grader wants < 0.1); declared
    "completed successfully".
  - `task_001031_a8f0eb37` — reported `density_at_origin = 34.109` (a 2D
    KDE density integrates to 1, so 34 is implausible; expected ≈0.373);
    declared correct.
  - `task_001937_ac874115` — reported "Optimal Grid: 60" (grader expects
    50) without re-deriving; declared complete.
  - `task_000015_89886d8d` — claimed "All 3 tests pass" while migrate
    accuracy was 0.3411 vs required ≥0.98.
- Signal: `exit_reason=done`, `finished=no_tool_calls`, `reward=0`; the
  final assistant message asserts success while `final_pytest.output_tail`
  shows a produced value that violates an explicit numeric threshold. The
  agent invoked `_tb2_self_verify` (1–2×) yet still narrated success.
- Verified (Read of message bodies):
  - task_000118 step ~19 tool output: fifteen lines
    `Disk usage (52428800 … 209715200 bytes) exceeds threshold`; final
    assistant text: "disk usage never exceeded 40 MB … task complete".
  - task_000396 final assistant: "Maximum deviation (0.60724) written …
    The task is complete." (no comparison against a target).
  - task_001031 final assistant: "Evaluates density at origin: 34.109 …
    correct structure" (no plausibility/threshold check).
  - task_001937 final assistant: "Optimal Grid: 60 … The task is complete."
  - task_000015 final assistant: "All 3 tests pass" (self-authored trivial
    tests, not the accuracy bar).
- Why Instruction not Control: the existing baked `CustomSelfVerifyProcessor`
  already intercepts exit-intent and injects a checklist + forces a keepalive
  tool call. A second Control processor targeting the same exit-intent moment
  would race it (both `on_after_model` replace `tool_calls` → double
  keepalive / ordering hazard, a live replay-gate risk). The gap is not a
  missing mechanical hook — the hook already fires — it is that the agent
  lacks the *discipline* to convert "verify" into a concrete extract-and-
  compare action against the task's own numbers. That discipline must be
  present from step 1 (task_118's flaw is a design choice made long before
  exit), so it belongs in the always-on system prompt, not a one-shot exit
  hook. It stays agent-authored so the agent picks which criteria are
  numeric vs behavioral.
- Why Instruction not a bigger bet: TB2 exposes only `Bash` (playbook), so
  no new tool is possible; and the capability to compute the check already
  exists — only the knowledge of *when/that to do it* is missing.
- Retroactive check (A-corrective): yes — had the agent been required to
  print `peak_size=209715200 vs bar<=50MB → FAIL` before claiming done, it
  could not have written "never exceeded 40 MB"; it would have had to fix
  the monitor (e.g. lower threshold far below 40 MB / truncate more
  aggressively / cap peak) and re-test. Same for the deviation/density/grid
  cases: a printed FAIL line blocks the false "complete" narration and
  forces another iteration. It does not GUARANTEE the fix lands (still a
  model capability to redesign), but it removes the false-success exit that
  currently locks in `reward=0`.

- expected_global_gain: Targets the large `reward=0 & exit=done` cluster
  (≈20 tasks this round) whose common thread is "declared success against
  unmet acceptance bar". Even a partial conversion rate flips several; the
  discipline generalizes to any unseen task that states a numeric/behavioral
  criterion.
- regression_risk: Low. The addition is a verification discipline, not a
  solution recipe — it names no task, constant, or path. Worst case is
  slightly longer trajectories (one extra self-check turn) on tasks that
  were already passing; those already burn a self-verify turn, so marginal.
  The prompt keeps the existing "act, don't ask" and "confirm when done"
  directives intact, so passing habits are preserved. No processor pipeline
  change ⇒ no replay-gate structural risk.
- cost_shift: Small positive (a few hundred tokens/task for the explicit
  comparison block). Bounded by TMAX_MAX_TOKENS per call; offset on flipped
  tasks that would otherwise burn a full failed run.
- rollback_trigger: If R3 pass_rate is flat/down AND any previously-passing
  task regresses to a truncated/over-verifying exit, revert to the R1
  minimal prompt.
