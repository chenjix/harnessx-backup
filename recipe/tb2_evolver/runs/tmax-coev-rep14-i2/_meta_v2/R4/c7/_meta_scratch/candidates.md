# Candidates — R4 c7

## Candidate C-001 — cumulative-truncation loop breaker

**Three-axis tag:** lens=budget-drain / lever=control / intent=fix-failing-cluster

**Assigned focus:** task_000133_20c45b39 (data_science / C + libcsv anomaly detector).

### Signal (verified from trajectory bodies)

- `task_000133_20c45b39.result.json`: `agent.exit_reason=budget_exceeded`,
  `steps=80`, `initial_pytest.passed=true`, `final_pytest.passed=false`
  (clean1.csv rejected AND evil1.csv bypassed — detector never got correct).
- `task_000133.messages.json`: 13 `finish_reason=length` truncation nudges at
  indices [7,10,20,22,24,26,34,36,38,40,50,54,67]. The truncated-vs-toolcall
  turn sequence is `CCTTCCCCTTTTCCCTTTTCCCCTCTCCCCCTC` — an ALTERNATING
  truncate/act pattern. The assistant re-narrates the identical sentence "The
  CSV parser is reading the file incorrectly … 15,1,0,1,0 … parsed as
  15,15,05,15,05" at msgs 17,21,33,35,53,62,64,68 — a chronic re-narration loop
  that consumed the whole 80-step budget.
- The stock `LengthTruncationRecoveryProcessor` IS wired (R1 config,
  `repeat_threshold=2`) but escalates on *consecutive* runs only; a tool call
  resets the counter, so on the alternating pattern the hard escalation never
  fires and the collapsed "I'm stuck" narration keeps re-priming the loop.

### Cross-task cluster (Pareto evidence — not a single-task win)

All reward=0, all `budget_exceeded`, all with ≥4 cumulative length-truncations
whose *max-consecutive* run is ≤4 (so the stock consecutive escalator is
defeated):

| task | domain-ish | cumulative trunc | max consecutive |
|------|-----------|-----------------:|----------------:|
| task_000133_20c45b39 | data_science C | 13 | 4 |
| task_001032_1adaccb9 |            | 13 | 2 |
| task_000958_4bb2b05d | http service | 7 | 2 |
| task_000683_7c966a71 |            | 5 | 2 |
| task_000747_424c178b |            | 4 | 2 |

Four of the five have max-consecutive ≤2, i.e. the stock processor barely
escalates at all; the cumulative escalator fires on all five.

### Change

Swap the stock `LengthTruncationRecoveryProcessor` for
`CumulativeLengthTruncationRecovery` (same `_singleton_group`
`"tmax_length_recovery"` and `_order=5`, so it replaces cleanly). It tracks
cumulative (not just consecutive) truncations; a tool call resets only the
consecutive run. Once cumulative ≥ `chronic_threshold=4` it (a) emits a terminal
"STOP narrating, ONE minimal command, do something different / write the required
output at the exact path" nudge and (b) collapses the runaway turn to a short
stub so the repeated narration stops re-priming context. Below chronic it behaves
like the stock processor (head+tail collapse + escalating first/repeat nudge).
Contract-safe: `on_after_model` rewrites only its own event content;
`on_before_model` replaces the trailing passive continue nudge (net length 0);
never removes messages; never force-exits.

### Retroactive check (variant: "would the mechanism have changed the run?")

For task_000133: at cumulative=4 (msg ~26) the chronic nudge fires and the
runaway narration is stubbed. Instead of 40+ more turns of the same re-narration
(msgs 33/35/53/62/64/68) the agent is repeatedly pushed to "one different
concrete command", and the "I'm stuck" text stops dominating context. That
reclaims the budget it burned re-narrating and gives it real turns to attack the
genuine parse bug. Honest caveat below.

### Why control-lever (not instruction / configuration)

The corrective nudge already reaches the model every truncated turn and is
ignored — so an *instruction/prompt* rule cannot help (the model reads the
existing "please continue" and re-narrates anyway). No *configuration* knob on
the stock processor switches it from consecutive to cumulative accounting;
`repeat_threshold` only sets the consecutive escalation point. The fix requires a
new mechanism = control lever.

### expected_global_gain

Reclaims the ~half-budget spent on repeated 4096-token re-narration across a
5-task `budget_exceeded` cluster with a shared, harness-shaped mechanism
(alternating-truncation loop). Generalizes to any unseen task that falls into a
chronic re-narration loop; zero task-specific literals. Plausibly flips the
members whose only remaining blocker was running out of budget before finishing.

### regression_risk

Low. Never force-exits, never removes messages (contract-clean). A task that
truncates a few times early then recovers is untouched below chronic_threshold;
above it, the only effect is a shorter collapsed turn plus a "do one concrete
command" directive — which is what a recovering agent does anyway. R2-c0 probe
found the only heavy-truncating PASS task (task_001832, 7 truncations
front-loaded) still had 40+ productive turns afterward; the stub only shrinks its
late collapsed turns. task_000011 (3 truncations) stays under chronic_threshold.

### cost_shift

Net negative (lower). Chronic-loop tasks stop emitting repeated 4096-token
re-narration turns and their forwarded context shrinks (stub vs head+tail). No
forced extra model turns.

### rollback_trigger

Revert if next-round pass_rate drops, OR any previously-passing heavy-truncating
task (task_001832, task_000011) regresses to F attributable to the chronic
directive, OR synthetic replay fails on the processor.

### Honest uncertainty

task_000133's terminal blocker is also a genuine libcsv/callback parse bug the
agent never cracked (`15,1,0,1,0` misparsed as `15,15,05,15,05`). If reclaiming
the budget is not enough for the model to solve that bug, the residual is a model
capability gap — logged, not harness-fixable. The defensible value here is
budget reclamation + de-priming across a 5-task cluster (all already failing, so
near-zero regression), with a plausible-but-not-guaranteed flip on the members
whose blocker was purely running out of budget.
