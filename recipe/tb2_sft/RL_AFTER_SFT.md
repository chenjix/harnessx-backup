# Tmax coevolve + online RL (DPPO / GRPO-fast)

## Train vs holdout

| Split | Tasks | Role |
|-------|-------|------|
| Evolve | 50 | harness evolve + SFT corpus |
| RL train | ≤100 taxonomy (prefers evolve-50) | GRPO/DPPO rollouts |
| Holdout | 102 | ratchet only — never RL-train |

## Smoke (after SFT)

```bash
sbatch scripts/slurm/tmax/h200_rl_smoke.sbatch
# uses ADAPTER_DIR=outputs/sft/tmax_coev_rep1_i1 by default
```

Stages: deps → taxonomy dataset → merge LoRA → tiny `grpo_fast` → verify ckpt.

## Full coevolve with RL

```bash
sbatch --export=ALL,ENABLE_RL=1,RL_N_TASKS=100,RL_EPISODES=512,REPLICATE=2 \
  scripts/slurm/tmax/h200_tmax_coevolve.sbatch
```

## Agent stack

RL rollouts: `swerl_vanillux_sandbox`. Eval/evolve: HarnessX.
