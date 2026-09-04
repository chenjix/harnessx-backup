# Tmax 4B/9B experiment matrix

This document is the execution contract for the six multi-iteration settings.
Run submissions from the repository root after loading `configs/tmax.env`.
Every run needs a fresh `REPLICATE`; use `ALLOW_RESUME=1` only to resume the
same setting with the same replicate.

## Commands

| Model | Training path | SFT rollout/data condition | Command |
|---|---|---|---|
| 9B | harness evolve + SFT | single incumbent harness | `REPLICATE=40 N_ITERS=3 bash scripts/tmax/settings/run_9b_evolve_sft_single.sh` |
| 4B | harness evolve + SFT | single incumbent harness | `REPLICATE=41 N_ITERS=3 bash scripts/tmax/settings/run_4b_evolve_sft_single.sh` |
| 9B | harness evolve + SFT | cross tournament harnesses | `REPLICATE=42 N_ITERS=3 bash scripts/tmax/settings/run_9b_evolve_sft_cross.sh` |
| 4B | harness evolve + SFT | cross tournament harnesses | `REPLICATE=43 N_ITERS=3 bash scripts/tmax/settings/run_4b_evolve_sft_cross.sh` |
| 9B | harness evolve + RL | no SFT | `REPLICATE=44 N_ITERS=3 bash scripts/tmax/settings/run_9b_evolve_rl.sh` |
| 4B | harness evolve + RL | no SFT | `REPLICATE=45 N_ITERS=3 bash scripts/tmax/settings/run_4b_evolve_rl.sh` |

Replicate numbers above are examples; check `outputs/tmax_coevolve/` first.
Add `DRY_RUN=1` to print the resolved `sbatch` command without submitting it.
Developers can also execute the common sbatch locally with
`TMAX_CONFIG_ONLY=1` and all four required variables to validate its resolved
stage flags without starting Docker or training.
For a cheap control-plane smoke, override `N_ITERS=1 EVOLVE_ROUNDS=1`, and for
RL also set `RL_EPISODES=128`. This is still a real GPU experiment.

## What the SFT ablation changes

The two SFT launchers keep model size, tournament search, rotating evolve set,
holdout gate, SFT epochs, and model ratchet aligned. They change collection and
corpus routing:

| Variable | Single harness | Cross harness |
|---|---:|---:|
| `SFT_GEN_ROLLOUT` | `1` | `0` |
| `SFT_GEN_TASKS` | `100` | not used |
| `CORPUS_EVAL_HARNESS` | `1` | `0` |
| `CORPUS_WINNER_ONLY` | `1` | `0` |
| Included trajectories | incumbent/eval-harness fingerprint only | successes from tournament candidate harnesses |
| System prompt treatment | inject incumbent harness prompt | preserve source trajectory distribution |

Thus “single” asks whether clean on-policy data from one promoted harness helps
SFT; “cross” asks whether successes discovered across candidate harnesses are a
better training mixture. The number and diversity of rollouts are part of this
ablation. If a task-count-matched comparison is needed later, add a separately
named setting rather than silently changing these definitions.

## Script call chains

All six start with the same scheduler chain:

```text
scripts/tmax/settings/run_<setting>.sh
  -> scripts/tmax/settings/_submit.sh
  -> sbatch scripts/slurm/tmax/h200_tmax_experiment.sbatch
  -> scripts/tmax/run_loop_tmax_coevolve.sh
```

Harness evolve stages A/A0/B:

```text
run_loop_tmax_coevolve.sh
  -> scripts/tmax/evolve_tmax.sh
  -> python -m recipe.tb2_evolver.run --eval-backend tmax
  -> recipe/tb2_evolver/{fanout_tournament,tmax_adapter}.py
  -> scripts/tmax/evaluate_tmax.sh
  -> python -m recipe.tmax_eval.run_eval
```

SFT stages B2/C/D/E:

```text
single only: recipe.tb2_sft.src.plan_tmax_sftgen -> evaluate_tmax.sh
  -> recipe.tb2_sft.src.build_tmax_evolve_sft
  -> scripts/train_sft.sh
  -> recipe.tb2_sft.src.train_sft_lora
  -> evaluate_tmax.sh (holdout model gate)
```

RL stages D2/E (`ENABLE_SFT=0` skips B2/C/D):

```text
run_loop_tmax_coevolve.sh
  -> scripts/tmax/train_rl_grpo.sh
  -> recipe.tb2_sft.src.build_tmax_rl_dataset
  -> tmax/training/open-instruct/open_instruct/grpo_fast.py
  -> scripts/tmax/check_rl_ckpt.py
  -> evaluate_tmax.sh (holdout model gate)
```

## Resume and audit

```bash
REPLICATE=40 N_ITERS=3 ALLOW_RESUME=1 \
  bash scripts/tmax/settings/run_9b_evolve_sft_single.sh
```

The loop resumes at `.done-*` boundaries under
`outputs/tmax_coevolve/rep40/`. Inspect `STATUS`, `incumbent.tsv`, `scores.tsv`,
and `timings.tsv`. Never resume one replicate with a different model, pipeline,
or rollout mode; create a new replicate instead.

The Slurm stdout/stderr names contain model/path/mode via the job name. Record
the Git SHA, exact command, Slurm job ID, and replicate in the experiment log.
