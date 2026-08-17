# Tmax coevolve + online RL (DPPO / GRPO-fast)

## Train vs holdout (do not mix)

| Split | Tasks | Role |
|-------|-------|------|
| **Evolve** | 50 (`tasks_tmax_evolve50_list.json`) | harness evolve + SFT corpus source |
| **RL train** | ≤100 from taxonomy (prefers evolve-50) | GRPO/DPPO rollouts |
| **Holdout** | 102 (`tasks_tmax_only200.json`) | ratchet / report only — **never RL-train** |

Matches official Tmax: RL on a train union, eval on an external measurement set.

## Pipeline stage

```
evolve(50) → corpus → SFT(LoRA) → [optional RL/DPPO on ≤100] → holdout(102) → ratchet
```

Enable inside coevolve:

```bash
sbatch --export=ALL,ENABLE_RL=1,RL_N_TASKS=100,RL_EPISODES=512,REPLICATE=2 \
  scripts/slurm/h200_tmax_coevolve.sbatch
```

Smoke:

```bash
sbatch scripts/slurm/h200_rl_smoke.sbatch
```

Standalone:

```bash
python -m recipe.tb2_sft.src.build_tmax_rl_dataset \
  --from-taxonomy --n-tasks 100 --name tmax_rl_train100

ADAPTER_DIR=outputs/sft/tmax_coev_rep1_i1 \
  OUTPUT_DIR=outputs/rl/merged/tmax_coev_rep1_i1 \
  bash scripts/merge_sft_adapter.sh

RL_DATASET_NAME=tmax_rl_train100 \
  RL_INIT_MODEL=outputs/rl/merged/tmax_coev_rep1_i1 \
  RL_OUTPUT_DIR=outputs/rl/tmax_coev_rep1_i1 \
  RL_EPISODES=512 \
  bash scripts/train_rl_grpo.sh
```

Official multi-node reference lives under `tmax/training/open-instruct/scripts/tmax/RL/`. Coevolve defaults are smaller for an in-loop stage.

## Agent-stack note

RL rollouts use `swerl_vanillux_sandbox`; coevolve eval uses HarnessX. Do not expect bit-identical tool traces.
