# HarnessX × Tmax coevolve (+ SFT + GRPO)

Pipeline for **Qwen3.5-9B** on Tmax:

```
evolve(50) → corpus → SFT(LoRA) → [optional GRPO/DPPO on ≤100] → holdout(102) → ratchet
```

Holdout-102 is **eval-only** (never used for SFT corpus filter targets beyond exclusion, never for RL train).

## Layout

```text
harnessx/                 agent runtime + meta-harness
benchmarks/               TB2 Harbor helpers still used by processors
configs/                  baseline_tmax_harness.yaml + prompts
recipe/tb2_evolver/       gated harness evolve (Tmax tasks)
recipe/tb2_sft/           SFT / RL dataset builders + LoRA trainer
recipe/tmax_eval/         Docker Tmax eval (evaluate_tmax)
tmax/training/open-instruct/   official-style grpo_fast / DPPO
scripts/                  launchers
scripts/slurm/            sbatch wrappers
docs/                     data setup notes
```

## Splits

| Split | Tasks | Path |
|-------|------:|------|
| Evolve | 50 | `recipe/tb2_evolver/tasks_tmax_evolve50_list.json` + `recipe/tb2_sft/data/tmax_evolve50/` (local) |
| Holdout | 102 | `recipe/tb2_evolver/tasks_tmax_only200.json` |
| RL train | ≤100 | taxonomy parquet → `build_tmax_rl_dataset --from-taxonomy` (excludes holdout; prefers evolve-50) |

See `docs/DATA.md` for taxonomy / HF download.

## Quick start

```bash
cd /path/to/harnessx-backup
cp .env.example .env   # secrets stay local; never commit
# install deps into your venv (vLLM, peft, ray for RL, …)
bash scripts/doctor.sh
```

### Coevolve (8× H200)

```bash
sbatch scripts/slurm/h200_tmax_coevolve.sbatch
# with online GRPO after each SFT attempt:
sbatch --export=ALL,ENABLE_RL=1,RL_N_TASKS=100,RL_EPISODES=512,REPLICATE=2 \
  scripts/slurm/h200_tmax_coevolve.sbatch
```

### RL smoke (2× H200)

```bash
sbatch scripts/slurm/h200_rl_smoke.sbatch
```

### Key scripts

| Script | Role |
|--------|------|
| `scripts/run_loop_tmax_coevolve.sh` | outer harness ↔ SFT (+ optional RL) loop |
| `scripts/evolve_tmax.sh` | evolve-50 |
| `scripts/evaluate_tmax.sh` | holdout-102 |
| `scripts/train_sft.sh` | LoRA SFT |
| `scripts/train_rl_grpo.sh` | open-instruct `grpo_fast` + Vanillux sandbox |
| `scripts/merge_sft_adapter.sh` | LoRA → full weights for RL init |
| `scripts/smoke_rl_grpo.sh` | tiny end-to-end RL check |

More RL notes: `recipe/tb2_sft/RL_AFTER_SFT.md`.

## Agent stacks

- **Evolve / holdout / SFT trajs**: HarnessX + vLLM
- **Online RL rollouts**: `swerl_vanillux_sandbox` (official Tmax-style); not bit-identical to HarnessX

## What is not in git

`outputs/`, `logs/`, `.benchmarks/`, built `recipe/tb2_sft/data/`, weights, parquet, `.env`, and heavy `tmax/evaluation_assets/` (local only).
