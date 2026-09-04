#!/usr/bin/env bash
# Continue 9B tournament coevolve (rep22) for two more outer iters.
#
# Existing chain: i1 SFT 78→82 (stock YAML), i2 H 82→86 accepted,
# i2/i3 SFT rejected. Incumbent is i1 LoRA + i2 R2 harness @ 86.
# This job starts at iter 4 (skips evolve/SFT already on disk).
#
#   bash scripts/tmax/submit_tournament_sft_9b_continue.sh
set -Eeuo pipefail

SBATCH=/fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_tournament_sft.sbatch
REPLICATE="${REPLICATE:-22}"
START_ITER="${START_ITER:-4}"
N_ITERS="${N_ITERS:-5}"

unset INIT_LORA_PATH EVOLVE_EXTRA_ARGS MIN_IMAGE_GB PER_TASK MAX_PAIRS_PER_TRAJ \
      CORPUS_WINNER_ONLY CORPUS_MAX_PREV_FRAC CORPUS_EVAL_HARNESS MODEL_SIZE \
      TMAX_CONCURRENT HOLDOUT_CONCURRENT SFT_GEN_ROLLOUT SFT_GEN_TASKS \
      SFT_GEN_CONCURRENT || true

exec sbatch --export=ALL,REPLICATE="${REPLICATE}",START_ITER="${START_ITER}",N_ITERS="${N_ITERS}",ENABLE_RL=0,MODEL_SIZE=9b,INIT_LORA_PATH= \
  "$SBATCH"
