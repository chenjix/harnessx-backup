# Evolve Brief

## Assigned focus for THIS proposal

**Stopping and verification.** Find where the agent declared success without checking, or burned its budget after the work was already done. Change the harness's completion criteria.

Use failing task `task_000140_01c78b42` only as the initial evidence anchor. Read its trajectory, then search for the same failure class in other trajectories before editing.

**Generalization contract.** Treat the named task as evidence, not as the
target. First state the reusable failure class in terms observable by the
agent (tool result, context state, progress, or verification state). Inspect
at least two other trajectories for supporting or counter-evidence when they
exist. The harness edit must trigger from that general state: do not mention
task IDs, benchmark paths, filenames, expected answers, or task-specific
content in the prompt, processor, or tool. Explain why the mechanism should
help unseen tasks and identify which already-solved task class it could hurt.
If the evidence supports only one task, prefer a no-op over a special case.

Other proposals in this batch are assigned different focuses. Stay on yours: a batch is useful only when its members differ. If your focus turns out to be unsupported by the trajectories, say so in `candidates.md` and make the smallest defensible edit rather than drifting onto another proposal's territory.

## Pivot harnesses (read these BEFORE proposing)

Several harnesses are live at similar scores with different per-task coverage. Treat each as a branch point: read THAT harness's `config` and `trajectories_dir`, note which tasks only that lineage uniquely solves, then propose. A synthesis that keeps complementary unique solves is better than copying one parent wholesale. Ties are allowed to stand — do not discard a lineage just because its pass rate matches another.

| id | score | config | trajectories | unique solves | unique losses | this proposal starts from |
|----|------:|--------|--------------|---------------|---------------|---------------------------|
| `n0_8743947a` | 0.500 | `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep25-i1/R0/config.yaml` | `/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep25-i1-r0-traj` | — | — |  |

- `current_config`: `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep25-i1/R0/config.yaml`
- `trajectories_dir`: `/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep25-i1-r0-traj`
- `output_dir`: `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep25-i1/_meta_v2/R1/c4`
- `memo_path`: `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep25-i1/learnings.md`
- budget: None USD, 200 steps, 14400s wall-clock

## Deliverables (all under `output_dir`)

- `config.yaml` — a HarnessConfig YAML that canonicalizes (required)
- `tools/<name>.py` — optional new `@tool` modules
- `processors/<name>.py` — optional new `MultiHookProcessor` classes
- `templates/<name>.j2` — optional new system-prompt Jinja templates
- `_meta_scratch/candidates.md` — when you change the config, list
  candidates as `## Candidate C-N` sections (see orchestrator
  evidence gate below)
- `_meta_scratch/` — your own notes; this brief already lives here

Read-only context (if present in `_meta_scratch/`):

- `pareto_archive.json` — per-task pass/fail history across all rounds (tasks marked stuck/fragile/solved)
- `history/OVERVIEW.md` — round-by-round score table
- `history/R{i}_per_task.json` — per-task results for round i
- `history/R{i}_to_R{j}.diff` — config diff between rounds
- `env_probe.md` — which external sites/APIs are reachable (OK/BLOCKED/TIMEOUT) from this environment
- `task_catalog.md` — all task questions being evaluated (read this to understand WHAT the agent needs to solve)

## Global optimization constraint (Pareto-style)

Do not optimize a narrow local win at the expense of global benchmark health. Prefer candidates that improve failing clusters while protecting already-passing clusters.

For each shipped candidate, explicitly state:
- `expected_global_gain`: which failing cluster(s) and why this can generalize
- `regression_risk`: what could break outside `predicted_affected`
- `cost_shift`: expected token/cost movement if the change lands

A local improvement with likely net global degradation is not acceptable unless you provide unusually strong evidence and a clear rollback trigger.

## Self-validation before `end_turn`

No retry loop. If you end your turn with a broken artifact the
round fails. `Read validate` for the CLI commands — at minimum
run `canonicalize` on your new config before stopping, and run
`dry_fire` / `contract` / `literals` when you authored anything
under `tools/`, `processors/`, or `templates/`.

## Decision contract (required)

Before `end_turn`, make exactly one explicit decision:
1) **Ship change**: write ALL of the following, then run validation:
   - `output_dir/config.yaml` (required)
   - `_meta_scratch/candidates.md` (REQUIRED when config changes —
     at least one `## Candidate C-NNN` section with lens/lever/intent
     tag, signal, verified body evidence, retroactive check, and
     'Why X not Y' lever argument; the evidence gate hard-fails without it)
   - a new `## Round N` section appended to `memo_path` journal with
     `cited_candidates` frontmatter referencing your C-NNN IDs
   - optional: `tools/<name>.py`, `processors/<name>.py`, `templates/<name>.j2`
2) **Explicit no-op**: copy `current_config` byte-for-byte to
   `output_dir/config.yaml`, then stop. No candidates.md needed.
Analysis-only `end_turn` is invalid and is treated as a failed round.

## Orchestrator post-flight

After you stop, the orchestrator runs:
1. **canonicalize** (always) — config.yaml must parse and every
   template must render.
2. **novelty** — a journal hypothesis_id marked `reverted` in
   a prior round cannot be re-proposed.
3. **evidence** — when the changeset is non-empty, the round
   must produce `_meta_scratch/candidates.md` with at least
   one `## Candidate C-N` section, and the journal entry's
   `cited_candidates` frontmatter must reference ≥1 of those
   IDs. Prevents shipping config changes without linked
   evidence.
4. **replay** — by default runs a synthetic smoke task
   (`replay_mode=synthetic_task`): executes one tiny fixed
   task through the run loop and fails on exception / timeout /
   `exit_reason=error`. Optional `replay_mode=config_only`
   keeps bind-only checking.
