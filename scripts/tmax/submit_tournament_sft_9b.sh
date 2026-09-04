#!/usr/bin/env bash
# Submit the 9B-base tournament coevolve with the new SFT corpus recipe.
# Always clear leftover adapter / extra-args from the submit shell.
set -Eeuo pipefail

SBATCH=/fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_tournament_sft.sbatch
REPLICATE="${REPLICATE:-22}"

unset INIT_LORA_PATH EVOLVE_EXTRA_ARGS MIN_IMAGE_GB PER_TASK MAX_PAIRS_PER_TRAJ \
      CORPUS_WINNER_ONLY CORPUS_MAX_PREV_FRAC CORPUS_EVAL_HARNESS MODEL_SIZE \
      TMAX_CONCURRENT HOLDOUT_CONCURRENT START_ITER || true

exec sbatch --export=ALL,REPLICATE="${REPLICATE}",N_ITERS="${N_ITERS:-3}",ENABLE_RL=0,MODEL_SIZE=9b,INIT_LORA_PATH= \
  "$SBATCH"
