# Two closed loops: TB2 vs Tmax

This repo keeps **both** coevolve pipelines. They share `harnessx/`, vLLM helpers,
and LoRA SFT (`scripts/train_sft.sh`), but live in separate script trees.

```text
scripts/
  _common.sh, serve*, doctor, train_sft.sh   # shared
  tb2/     # Terminal-Bench 2 Harbor loop (+ slime replay-GRPO)
  tmax/    # Tmax taxonomy loop (+ open-instruct DPPO/GRPO)
  slurm/
    tmax/  # H200 sbatches for Tmax
    tb2/   # (reserve for TB2 sbatches)
```

Compatibility symlinks under `scripts/*.sh` still point at `tb2/` or `tmax/`
so older commands and the live coevolve job keep working.

Task lists:

```text
recipe/tb2_evolver/tasks/tb2/    # TB2 Harbor task JSONs
recipe/tb2_evolver/tasks/tmax/   # Tmax evolve-50 / holdout-102
```

Flat names like `recipe/tb2_evolver/tasks_tmax_only200.json` are symlinks into
those folders.

## TB2 loop

```bash
REPLICATE=1 bash scripts/tb2/run_loop_step3.sh
# or: bash scripts/run_loop_step3.sh   # symlink
```

Stages (see script header): evolve → eval harness → route+SFT corpus → SFT →
eval SFT. Optional offline replay-GRPO: `scripts/tb2/train_grpo.sh` + `recipe/slime/`.

Baseline harness: `configs/baseline_harness.yaml`.

## Tmax loop

```bash
sbatch scripts/slurm/tmax/h200_tmax_coevolve.sbatch     # 8x H200
sbatch scripts/slurm/tmax/a100_tmax_coevolve.sbatch     # 8x A100-40GB (p4d)
# with GRPO after SFT (H200 only — 40GB cards cannot hold trainer + vLLM):
sbatch --export=ALL,ENABLE_RL=1,RL_N_TASKS=100,REPLICATE=2 \
  scripts/slurm/tmax/h200_tmax_coevolve.sbatch
# harness evolve + RL, no SFT:
sbatch scripts/slurm/tmax/h200_tmax_coevolve_evolve_rl.sbatch
```

Stages: rotate evolve set → evolve(50, inner gate 0.04) → holdout(102)
harness ratchet (ties on pass count) → **SFT-gen top-up to 100 unique
non-holdout tasks** under the eval harness → winner-only corpus → SFT →
optional GRPO(≤100) → holdout(102) model ratchet.

`ENABLE_SFT=0 ENABLE_RL=1` skips B2/C/D and RLs the base (or last accepted
full ckpt) on the evolve-set tasks. Launcher:
`scripts/slurm/tmax/h200_tmax_coevolve_evolve_rl.sbatch`.
See `docs/COEVOLVE_PIPELINE.md` and `recipe/tb2_sft/RL_AFTER_SFT.md`.

Baseline harness: `configs/baseline_tmax_harness.yaml`.

### Incumbent pair, not just the model

Each iteration starts from the **best model AND best harness** measured so far on
holdout-102 (`outputs/tmax_coevolve/rep<N>/incumbent.tsv`, with
`best_model.tsv` / `best_harness.tsv` as the per-artifact history). The harness
is ratcheted the same way the model is: iteration k's evolved harness is
measured with the incumbent model (stage B) and only becomes the new incumbent
if it wins; otherwise it is discarded and the next iteration re-seeds from the
harness that did win. `HARNESS_RATCHET=0` restores unconditional carry-forward.

### Relaxed ratchet: ties on pass count

`ACCEPT_TIES=1` (default) accepts a candidate that *equals* the incumbent's
holdout pass count. `agent_error` / `error` statuses already contribute 0 to
`n_passed`, so a second sys_err gate on ties double-counts the same tasks and
blocks legitimate parity (this is what stalled the 4B chain at 64).
`ACCEPT_TIES=0` goes back to requiring a strict improvement. The unused env
`TIE_MAX_SYSTEM_ERRORS` is kept only so old sbatches do not error.

### Rotating evolve set

Instead of re-evolving on the same 50 tasks forever, iteration k retires tasks
the model has **mastered** — solved in an earlier iteration *and* already
harvested into an SFT corpus (`selected_tasks` in the corpus summary) — and
refills to `EVOLVE_SET_SIZE` with a domain-stratified draw from the 2.2k
taxonomy pool. holdout-102 is excluded at every step.

```text
recipe/tb2_sft/src/tmax_mastery.py               # which tasks are mastered (+ evidence)
recipe/tb2_sft/src/build_tmax_evolve_task_set.py # keep + refill -> task_ids.json + envs jsonl
outputs/tmax_coevolve/rep<N>/mastered_i<k>.json  # cumulative retire list
outputs/tmax_coevolve/rep<N>/task_sets.tsv       # per-iteration kept/new/retired
recipe/tb2_sft/data/tmax_coev_rep<N>_i<k>_evolveset/
```

Knobs: `ROTATE_EVOLVE_TASKS=1`, `ROTATE_FROM_ITER=2`, `EVOLVE_SET_SIZE=50`,
`MASTERY_MIN_SUCCESSES=1`, `MASTERY_MIN_SUCCESS_RATE=0`,
`MASTERY_REQUIRE_CORPUS=1`, `ROTATE_SEED=42`, `TAXONOMY_PARQUET=...`.
Set `ROTATE_EVOLVE_TASKS=0` for the old fixed-50 behaviour.

The set for iteration k is written once and reused on resume — re-sampling would
evolve on a different 50 tasks than the trajectories already on disk came from.
Because the SFT corpus is cumulative, `CORPUS_PREFER_CURRENT=1` (default) fills
the traj budget from this iteration's rollouts first so older high-quality demos
cannot crowd out the fresh material the rotation exists to collect.

### SFT-gen plane (100 unique non-holdout tasks)

Harness evolve and SFT harvest are **not** the same set. After stage B, stage B2
reuses this iteration's evolve-set evals and tops up with new taxonomy tasks
until `SFT_GEN_TASKS` (default 100) unique non-holdout ids have been attempted
under the **eval** harness (`harness_used` — incumbent if B rejected the
candidate). `PER_TASK=1`. Concurrent `SFT_GEN_CONCURRENT=8`. If `(H*, M)` is
unchanged since the last extra rollout, B2 is skipped. Corpus `winner_only`
keeps only dirs whose sidecar YAML+prompt fingerprint matches the eval harness;
losing `fe-c*` tournament siblings never enter SFT.

Knobs: `SFT_GEN_ROLLOUT=1`, `SFT_GEN_TASKS=100`, `SFT_GEN_CONCURRENT=8`.
`SFT_GEN_ROLLOUT=0` restores evolve-set-only harvest.

### Inner gate, holdout concurrency, quality, prompt chaining

- Tournament sbatches pass `--regression-tolerance 0.04` (not `-1`). A candidate
  that drops more than 4% of the evolve-set vs the parent is reverted.
- Single-harness holdout default is `HOLDOUT_CONCURRENT=8`. Tournament *evolve*
  still uses `TMAX_CONCURRENT=4` because five harnesses share the node.
- SFT quality prefers compact, fast successes (sweet spot ~8–20 tool turns);
  long loops and slow wall-clock are penalized.
- Seeding iteration k+1 from iteration k's winner copies sibling
  `system_prompt.txt` with the YAML (`_materialize_config_bundle` /
  `_copy_config_as_round`). Standalone `run_eval` writes `harness_config.yaml`
  + `system_prompt.txt` sidecars into the traj dir so `winner_only` can match.

Full stage-by-stage walkthrough: `docs/COEVOLVE_PIPELINE.md`.
