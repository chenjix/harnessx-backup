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
sbatch scripts/slurm/tmax/h200_tmax_coevolve.sbatch
# with GRPO:
sbatch --export=ALL,ENABLE_RL=1,RL_N_TASKS=100,REPLICATE=2 \
  scripts/slurm/tmax/h200_tmax_coevolve.sbatch
```

Stages: evolve(50) → holdout(102) → corpus → SFT → optional GRPO(≤100) →
holdout ratchet. See `recipe/tb2_sft/RL_AFTER_SFT.md`.

Baseline harness: `configs/baseline_tmax_harness.yaml`.
