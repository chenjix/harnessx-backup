# HarnessX — TB2 + Tmax coevolve

Two closed loops share one codebase:

| Loop | Scripts | Eval | Train extras |
|------|---------|------|--------------|
| **Tmax** | `scripts/tmax/` | Docker Tmax (`recipe/tmax_eval`) | LoRA SFT + optional open-instruct GRPO |
| **TB2** | `scripts/tb2/` | Harbor TB2 | LoRA SFT + optional slime replay-GRPO |

See **[docs/PIPELINES.md](docs/PIPELINES.md)** for the split and launch commands.
Data notes: **[docs/DATA.md](docs/DATA.md)**.

## Layout

```text
harnessx/                 agent runtime + meta-harness
benchmarks/               TB2 Harbor adapter (still used by some processors)
configs/
  baseline_harness.yaml       # TB2
  baseline_tmax_harness.yaml  # Tmax
recipe/tb2_evolver/       gated harness evolve (both loops)
  tasks/tb2/  tasks/tmax/
recipe/tb2_sft/           SFT / RL dataset builders + LoRA trainer
recipe/tmax_eval/         Tmax Docker eval
recipe/slime/             TB2 offline replay-GRPO
tmax/training/open-instruct/   Tmax online GRPO/DPPO
scripts/{tb2,tmax,slurm/tmax}/
```

## Quick start

```bash
cp .env.example .env   # never commit
bash scripts/doctor.sh
```

**Tmax coevolve:**

```bash
sbatch scripts/slurm/tmax/h200_tmax_coevolve.sbatch   # 8x H200
sbatch scripts/slurm/tmax/a100_tmax_coevolve.sbatch   # 8x A100-40GB (p4d)
```

Each iteration resumes from the best (model, harness) pair so far, accepts a tie
when the eval run was clean, and rotates mastered tasks out of the evolve set —
see [docs/PIPELINES.md](docs/PIPELINES.md).

**TB2 coevolve:**

```bash
REPLICATE=1 bash scripts/tb2/run_loop_step3.sh
```

**Tmax RL smoke:**

```bash
sbatch scripts/slurm/tmax/h200_rl_smoke.sbatch
```

## Not in git

`outputs/`, `logs/`, `.benchmarks/`, built `recipe/tb2_sft/data/`, weights,
parquet, `.env`, `tmax/evaluation_assets/`.
