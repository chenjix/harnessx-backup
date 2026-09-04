# Candidates — R4

## Round context

Prior rounds R1–R3 shipped three **control**-lever processors that fully
neutralised the max_tokens runaway, degenerate tool-call loops, and
identical-command echo traps. R3 result: 31/50. The residual failure
population has changed shape:

- **17 of 19** failing tasks now exit `done` / `no_tool_calls` — the agent
  finishes cleanly and *commits a wrong answer*.
- Only **1** `error` (task_001031) and **1** `budget_exceeded`
  (task_001032) remain — both capability-adjacent, not loop-shaped.

The control lever is exhausted. The **instruction** lever has never been
tried (scoreboard: instruction = 0 attempts). The live system prompt is the
bare 5-line `DEFAULT_TMAX_PROMPT` (verified in
`R3/system_prompt.txt`) — no environment-survey, no plan-before-implement,
no rigorous pre-exit artifact verification. The tb2-playbook names
"upfront environment survey", "explicit plan before implementation", and
"double-confirmation before exit" as the biggest score levers on this
benchmark, and the weakest domains are exactly the multi-file / multi-service
classes those habits target: system_administration 1/5, software_engineering 2/5.

---

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Replace the bare 5-line default system prompt with a concise strategy prompt
that (a) mandates an upfront environment/requirements survey, (b) requires an
explicit written plan enumerating every distinct output artifact and its exact
path for multi-file / multi-service tasks, and (c) hardens the pre-exit check
to physically `ls` + `cat` each required output at its exact spec'd path and
re-derive computed values rather than asserting success from a clean exit code.

- Tasks affected (failing cluster, same "commits wrong/unverified artifact" mechanism):
  - task_000933_1f27096a — claimed the tarball contained `graph_math_double`
    binary; verifier `isfile(.../bin/graph_math_double)` failed. Agent's final
    turn asserted "✅ Creates tarball with both binaries" without ever `tar tzf`-ing it.
  - task_001264_9f4ca84a — wrote `Subdirectories: 46`; verifier expects `45`.
    Agent "verified" but re-affirmed its own off-by-one instead of cross-checking
    against the spec's counting rule.
  - task_000140_01c78b42 — left lingering `vm_service` PIDs [339,590,797];
    the task explicitly requires a graceful SIGTERM shutdown. Requirement was
    enumerated in the task but dropped at exit; no final "confirm no lingering
    process" check.
  - task_000748_c9807703 — Rust memory-profiling output wrong; agent exited on
    a compile that only emitted warnings, never validated the profiling numbers.
- Signal: `exit_reason=done` + `finished=no_tool_calls` on 17/19 failures;
  `final_pytest` assertions are exact-path / exact-value / exact-state mismatches,
  not crashes. The existing `CustomSelfVerifyProcessor` fires (confirmed in
  task_001264 msgs 22–24) but its checklist lives only in a transient
  `on_before_model` injection and the model rubber-stamps its own output.
- Verified (Read):
  - task_000933 last assistant msg: bulleted "✅ ... Creates tarball with both
    binaries / ✅ release.tar.gz - Contains both compiled binaries" — pure
    self-assertion, no `tar tzf` in the trajectory.
  - task_001264 msgs 15–21: agent computed 46, reasoned about `.`/`..`, still
    wrote 46; msg 23 "verification" re-listed files without recomputing the count.
  - task_000140 msg 0 enumerates requirement "gracefully stop the Go service by
    reading service.pid and sending SIGTERM"; final state has 3 live PIDs.
- Why Instruction not Control: a Control hook already exists
  (`CustomSelfVerifyProcessor`) and mechanically injects a verify checklist —
  adding another mechanical hook duplicates it and cannot make the agent
  *re-derive* a value or *enumerate* artifacts up front; that scoping is
  agent-authored reasoning. The gap is the agent not knowing, from step 0, to
  (i) survey + plan artifacts and (ii) physically inspect each output against
  spec. That is a knowledge/ordering gap → Instruction. The prompt is currently
  near-empty, so there is a large, cheap headroom the mechanical layer cannot fill.
- Why Instruction not Configuration: no existing knob encodes "plan the
  artifact set before coding" or "survey the toolchain first" — this is new
  strategy, not a threshold tweak.
- Retroactive check (A-corrective): partial-yes. For the *verification-gap*
  subset (task_000933 tarball contents, task_000140 lingering PIDs) an
  up-front artifact enumeration + a real `tar tzf` / `pgrep` confirmation at exit
  would have surfaced the mismatch while the agent still had steps to fix it —
  these flip. For the pure-numeric subset (task_001264 count, task_000748
  profiling values, task_001653 centroid, task_001937 grid) the value is simply
  wrong and re-derivation may not save them — those are capability gaps and are
  NOT claimed. Net expected flips concentrate in the multi-file/service cluster.
- expected_global_gain: Targets the two weakest domains
  (system_administration 1/5, software_engineering 2/5) which are dominated by
  multi-artifact tasks where "planned + verified every output path" is the exact
  missing habit. Plausibly flips 2–4 tasks. Generalises because it encodes a
  benchmark-agnostic discipline (survey → plan → build → verify-against-spec),
  not any task literal.
- regression_risk: Low-moderate. 31 tasks currently pass on the bare prompt;
  a longer prompt could (a) add tokens and (b) over-encourage exploration on
  simple tasks. Mitigated by keeping the prompt short, strategy-only, and
  explicitly scoping the "write a plan" step to multi-file/multi-service tasks
  ("skip formal planning for a single-file task"). No task-specific content, so
  no passing task can key off a literal. Rollback trigger: if R5 pass_rate is
  flat/down AND ≥2 currently-passing tasks regress or mean step count inflates
  materially, revert to the bare prompt.
- cost_shift: Mildly positive (small increase) on token/step count from the
  survey+plan discipline; partially offset by fewer wasted steps on
  wrong-path/thrash tasks. Net expected near-neutral, skewed to gain if flips land.
