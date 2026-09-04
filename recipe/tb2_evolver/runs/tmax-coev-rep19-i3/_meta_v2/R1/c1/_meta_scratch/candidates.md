# Candidates

Assigned focus: `task_000017_fed73abc` (scientific_computing) fails.

## Diagnosis of task_000017

The task: fix a simulated-annealing C program, run it, then report the
"final, converged position of the optimal 15-mer binding site" and extract
that 15-mer. The C program's energy landscape has its minimum at position 42
(`score_position(pos) = (pos-42)^2 - 50`). After the temperature-decay fix the
annealer wanders around 40–44 near the minimum and the *last* recorded position
is 43.

The agent fixed the bug correctly, compiled, ran, and produced `trajectory.nc`
— all mechanically sound. It then took `positions[last] = 43` as the "converged
position" and extracted the 15-mer at index 43 (`GCTAGCGCGCTAGC` shifted by
one → `GCTAGCGCGCTAGCT`). Expected was position 42 (`AGCTAGCGCGCTAGC`). The
agent **never looked at the `score` variable at all** — it equated "converged /
optimal" with "last array element" instead of "the position that minimises the
stated objective (energy/score)". It even ran the built-in self-verify checklist
and re-verified the byte count and sed offsets many times, but never questioned
its interpretation of "optimal".

This is primarily a model reasoning/interpretation gap, not a mechanical harness
deficiency — no hook can supply the correct definition of "converged" without
task-specific knowledge. The scientific_computing failure cluster is
heterogeneous (fixed-width PDB parsing, wrong RNG/params, gradient-descent
convergence, wrong integral) — no single mechanical root cause. So per the
idiosyncratic filter the honest call is the **smallest defensible general edit**:
strengthen the existing exit-time self-verification discipline so that *selected/
optimal* results get re-derived independently, rather than embedding the
"minimise the score array" rule for this one task.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace `CustomSelfVerifyProcessor` with `EnhancedSelfVerifyProcessor`: same
one-shot exit-time control flow, but the injected checklist gains one fully
general step — re-derive any *optimal / converged / minimum / maximum / best /
final* value chosen from computed data by a second method and confirm it
satisfies the objective the task stated, instead of trusting a single endpoint
or index.

- Tasks affected (primary target): task_000017_fed73abc. Same interpretation
  hazard ("selected/optimal value taken as an endpoint rather than verified
  against the objective") is a general reasoning discipline; the edit is scoped
  to strengthen an existing all-tasks mechanism, not to this task.
- Signal: `final_pytest` value-mismatch off-by-one-index
  (`got 'GCTAGCGCGCTAGCT'` vs `'AGCTAGCGCGCTAGC'`); trajectory body shows the
  agent read only the `position` variable and never the `score` variable, and
  the existing self-verify pass (steps after `_tb2_self_verify`) only re-checked
  file existence / byte count, never the selection logic.
- Verified (Read): task_000017 tool result at `ncdump -v position` step — agent
  concludes "The last value is 43" and immediately extracts at 43; the
  self-verify turn re-runs `ls`/`cat`/`wc -c` only, never inspecting `score`
  or scanning for the true minimum. The C source in the first tool result
  defines the objective minimum at position 42, which the agent never consulted.
- Why Control not Instruction (system prompt): the checklist is delivered by an
  existing control processor at the exact exit-intent moment (one-shot, loop-safe
  via the synthetic keepalive tool). Folding the guidance into the static system
  prompt would (a) put it far from the decisive moment and (b) duplicate the
  self-verify mechanism the pipeline already owns. This is the narrowest edit:
  extend the message the existing mechanism already injects. Not Configuration
  because `CustomSelfVerifyProcessor.__init__` takes no message kwarg, so the
  text cannot be tuned via config — a subclass is required.
- Why not Action / task-specific fix: adding "read the score array and take its
  argmin" would be task-specific knowledge embedded in the harness; it would not
  generalise and would fail the literals/generalisation test. The shipped step
  contains no task IDs, constants, file paths, or domain terms.
- Retroactive check (A-corrective): plausibly yes for the target — had the
  checklist pushed "state the objective for 'best' and confirm your reported
  value satisfies it, not merely that it is the last/first entry", an agent that
  already understood the energy landscape it just fixed would have scanned the
  `score` variable and found the argmin at 42. Honest caveat: this is a nudge,
  not a guarantee — the model still has to act on it. Because it only augments
  an already-firing mechanism with general text, downside is bounded.
- expected_global_gain: strengthens the benchmark-wide "double-confirmation
  before exit" habit the playbook already flags as the biggest single lever,
  specifically for the recurring "report an optimal/selected value" shape that
  appears across scientific_computing / data_science / optimization tasks.
- regression_risk: very low. Behaviour is byte-identical to the base processor
  except for a longer checklist message on the single exit-intent turn; no
  change to control flow, ordering (`_order=90`), singleton group, or the
  synthetic keepalive. Worst case is a few extra tokens on one turn.
- cost_shift: negligible — one longer user message injected at most once per
  task (roughly +120 tokens on that single turn); may *reduce* cost on tasks it
  saves from a wasted failing run.
- Rollback trigger: if the next round shows a net pass-rate drop or the
  scientific_computing cluster does not improve while token cost on
  short/passing tasks rises materially, revert to `CustomSelfVerifyProcessor`.
