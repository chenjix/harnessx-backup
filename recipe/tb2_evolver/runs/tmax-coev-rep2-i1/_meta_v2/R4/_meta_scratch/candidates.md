# Candidates — R4

## Candidate C-004
[lens: success | lever: instruction | intent: preservative-transfer]

Replace the 5-line stub system prompt (via a TemplateSystemPromptBuilder
pointing at an authored .j2 so the change is a real templates_added
changeset) with the same task-approach discipline the passing cluster already exhibits — upfront enumeration
of ALL success criteria (including non-file / state / accuracy /
absence conditions) and a per-criterion concrete-command verification
gate before declaring done — generalized to every task.

- Tasks affected:
  - passing (habit fired): task_000578_cebe85a5 (data_science),
    task_001536_acfe6c35 (file_operations) — both open by enumerating
    the full requirement list at step 1 then verify concretely.
  - failing (habit absent / incomplete at decisive step):
    task_000140_01c78b42 (system_administration),
    task_000933_1f27096a (software_engineering),
    task_000748_c9807703 (software_engineering).
- Signal: dominant failure shape is `agent.finished=no_tool_calls` /
  `exit_reason=done` (11 of 18 fails), many at low step counts (9-34),
  with `final_pytest` showing `1 failed, 2-3 passed` — i.e. the agent
  voluntarily declared done having satisfied MOST criteria but missing
  one specific, checkable condition it never verified. The current
  base `system_prompt.txt` is 5 trivial lines; the `instruction` lever
  has 0 prior attempts (all 3 prior rounds were `control`).
- Verified (Read of message logs):
  - PASS task_000578 step 1 body: "Let me break down this task: 1.
    Initialize a Go module ... 2. Read all CSV files ... 3. For each
    CSV: parse ... compute mean ... 4. Write ..." — full criterion
    enumeration up front; task passes.
  - PASS task_001536: reaches 98 msgs of methodical build+verify and
    passes (long-horizon, non-truncating — see R1 journal).
  - FAIL task_000140 final turns: agent's self-check enumerated ONLY
    file/content requirements ("Go service ✓, supervisor script ✓")
    and `ls`-verified files, then declared done — but the verifier's
    `test_no_lingering_service_processes` found PIDs 346/592/796 still
    running. The binding "no lingering processes" (state/absence)
    criterion was never in the agent's checklist or verified.
  - FAIL task_000933 final turns: agent verified files exist and even
    listed the tarball contents, declared done — but the verifier
    extracted the tarball and found `.../home/user/bin/graph_math_double`
    absent (archive path-layout criterion never exercised by the
    agent's own check).
  - FAIL task_000748: Rust builds (only warnings) and 2/3 tests pass,
    but `test_memory_profiling` fails — a behavioral criterion the
    agent never re-ran/measured before stopping.
- Why Instruction not Control: the existing `CustomSelfVerifyProcessor`
  ALREADY injects a strong file-centric exit checklist on the first
  no-tool-call turn (verified in harness.py `_SELF_VERIFY_MSG`), yet
  these tasks still fail — so the gap is not "no verification hook
  exists" (Control already covers that mechanically). The gap is that
  the agent's *model of what to verify* is incomplete from step 1: it
  never enumerated the non-file/state/behavioral criteria, so no
  mechanical exit hook can make it check a criterion it never
  identified. Fixing the agent's up-front requirement modeling and
  verification scope is an instruction-shaped change (how to approach
  a class of problems), not a new mechanical hook. A Control processor
  cannot enumerate task-specific criteria for the agent without
  embedding task knowledge.
- Why not Configuration: no existing knob encodes "enumerate all
  criteria and verify each"; this is a strategy, not a threshold.
- Retroactive check (C-preservative-transfer): yes — both ends
  grounded. Passing tasks demonstrably enumerate+verify (578, 1536).
  Failing tasks (140, 933, 748) each miss exactly one criterion that a
  per-criterion "check state/behavior with a concrete command, not
  just file existence" discipline applied up front would have surfaced
  before the voluntary exit. The prompt describes the discipline in
  general terms (survey → enumerate every criterion type → verify each
  concretely) with zero task-specific literals, so it transfers to
  unseen tasks.
- expected_global_gain: the largest failing cluster is voluntary-exit
  partial-completion (no_tool_calls, 11/18 fails). Even a modest lift
  in criterion coverage plausibly flips the subset whose missing
  condition is checkable-and-fixable within budget (140 lingering
  procs, 933 tarball layout, and similar state/format misses). This is
  the first pull of the untried `instruction` lever after 3 plateaued
  `control` rounds.
- regression_risk: rewriting the base prompt could destabilize the 32
  passing tasks. Mitigated by (a) KEEPING the original 5 lines verbatim
  as the head and only APPENDING general discipline — no removed
  guidance; (b) the added discipline (survey, enumerate, verify each
  criterion) is exactly what the passing cluster already does, so it
  reinforces rather than redirects them; (c) no hard directives that
  force extra tool calls on trivial tasks — verification is scoped to
  "each criterion in your checklist", which is small for simple tasks.
  Rollback trigger: revert to the R3 5-line prompt if R5 net pass-rate
  drops below the R1 incumbent (32/50) OR any currently-passing
  short-horizon task regresses T->F without a compensating flip.
- cost_shift: mildly positive on tokens/steps — a few extra
  verification commands on tasks that currently under-verify, and a
  longer system prompt (~40 lines vs 5) added once per task. Bounded:
  no per-turn injection, no loops. The reclaimed passes justify the
  small per-task overhead; budget_exceeded tasks are untouched by this
  change (they never reach the voluntary-exit path).
