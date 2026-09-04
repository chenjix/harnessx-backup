# Candidates — R2 c1 (focus: task_000017_fed73abc)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Extend the exit-intent `NumericCrossCheckNudge` message with an optimum-selection
clause: when the deliverable is the best/converged/min/max result of a search or
optimization run, select it by `argmin`/`argmax` of the recorded objective, NOT
by the last-iterate value the search happened to log.

- Tasks affected (primary/decisive): task_000017_fed73abc.
  Adjacent same-cluster corroboration (weaker, different mechanism):
  task_001937_ac874115 (optimal grid 60 vs 50), task_001035_26564093
  (optimal primer GCGG vs GCAT) — both "find the optimum" deliverables that
  committed a suboptimal value; the argmin/argmax-vs-recency reminder is a
  no-op for them if their search was already correct, and only helps if the
  agent likewise grabbed a non-optimal candidate.
- Signal: `result.json` for task_000017 — `exit_reason=done`, `steps=17`,
  `final_pytest` expected `AGCTAGCGCGCTAGC` (position 42, the minimum-energy
  site) but got `GCTAGCGCGCTAGCT` (position 43). All mechanical steps (fix
  `temp *= 1.05` → `0.95`, install libnetcdf, compile, run, ncdump) succeeded;
  the ONLY error was selecting the trajectory's last element instead of the
  minimum-energy element.
- Verified (Read messages.json):
  - Step (assistant) after `ncdump -v position`: model wrote "The final
    position (last value in the array) is 43 ... So the optimal position is 43
    (0-indexed)." It never inspected the `score` variable at all.
  - The C source defines `score_position(pos) = pow(pos-42, 2) - 50`, so the
    objective minimum is unambiguously at position 42; the trajectory array
    contains 42 many times, and the last-logged value 43 is a stochastic
    accepted step (temp still > 0), i.e. a wobble one index off the optimum.
  - The subsequent `_tb2_self_verify` exit-intent turn fired the EXISTING
    nudge, but that message only asks to "re-derive by a second method" — it
    gave the agent no reason to reconsider WHICH trajectory value to pick, so
    the agent re-verified file existence/format and committed 43 unchanged.
- Why Control not Instruction: the fix belongs in the mechanical exit-intent
  hook that already exists (`NumericCrossCheckNudge`, order 91, keyed on the
  `CustomSelfVerifyProcessor` exit-intent signal). It must fire uniformly and
  exactly once per task on a runtime event the system prompt cannot observe
  (the model's finish_reason without tool_calls). Baking this into the static
  system prompt would compete with the existing verification checklist for
  attention on every task from step 0, whereas the processor delivers it at the
  precise decision point (just before commit) and is a no-op on runs that never
  reach exit-intent. This is a re-parameterisation/extension of an existing
  Control component, not a new lever.
- Why not a new processor: the exact same exit-intent trigger, singleton, and
  ordering are already implemented; adding a second processor would either
  duplicate the trigger or inject a competing extra message. Extending the
  single message keeps the pre-exit context to one addendum (bounded token
  cost) instead of two.
- Retroactive check (A-corrective): yes — had the optimum-selection clause been
  in the exit-intent message, the agent (which DID reach exit-intent and re-ran
  verification commands) would have been directed to inspect the `score` series
  and take `argmin(score)` = position 42, extracting `AGCTAGCGCGCTAGC` and
  passing the exact-match grader. The agent had the data (`score` variable in
  the NetCDF) and the budget (17/80 steps used); it lacked the prompt to select
  by objective rather than recency.

### Pareto

- expected_global_gain: Flips task_000017 (assigned focus) and plausibly helps
  any "return the optimum of a stochastic/iterative search" task where the agent
  grabs the last iterate — a recognised failure shape in the
  scientific_computing cluster (7/7 red this round). Generalises via a pure
  strategy clause with no task literals.
- regression_risk: Low. The clause only adds one bullet to a message that fires
  at most once per task on the voluntary exit-intent turn; runs that die at the
  step cap never see it. On non-optimum tasks the bullet is explicitly gated
  ("If the deliverable is the OPTIMUM of a search...") and the message already
  tells the agent to ignore it when not applicable. No already-passing cluster
  depends on committing a last-iterate value, so the guidance cannot flip a
  passing task. Main residual risk is marginal added tokens on the single
  exit-intent turn.
- cost_shift: Negligible-to-slightly-up. One extra ~120-word paragraph on at
  most one turn per task; may add 1-2 verification Bash calls on genuine
  optimum tasks. Net expected cost is dominated by flips converting
  budget-neutral fails into passes.
