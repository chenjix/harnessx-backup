#!/usr/bin/env bash
# Live dry-run of the RL stage only: reuses an already-merged SFT checkpoint and
# an already-built RL dataset, so it skips SFT + merge and goes straight to
# grpo_fast. Meant to be launched under srun on an allocated node, e.g.
#
#   srun --account=interactive-ai --partition=ml.p5en.48xlarge --nodes=1 \
#        --gres=gpu:h200:2 --cpus-per-task=24 --mem=0 --time=1:00:00 \
#        --job-name=rl-smoke-dry \
#        bash scripts/tmax/dryrun_rl_grpo.sh 2>&1 | tee logs/rl_dryrun.log
set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export RL_PYTHON="${RL_PYTHON:-$PYTHON_BIN}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"

# Reuse existing artifacts so the dry-run only exercises grpo_fast.
export RL_DATASET_NAME="${RL_DATASET_NAME:-tmax_rl_smoke_dryrun_data}"
export RL_INIT_MODEL="${RL_INIT_MODEL:-$REPO/outputs/rl/merged/tmax_rl_smoke_1948}"
export RL_OUTPUT_DIR="${RL_OUTPUT_DIR:-$REPO/outputs/rl/${DRYRUN_NAME:-tmax_rl_smoke_dryrun3}}"
export RL_EXP_NAME="${RL_EXP_NAME:-tmax-rl-dryrun}"

# Same tiny shape as h200_rl_smoke.sbatch.
export RL_N_TASKS="${RL_N_TASKS:-4}"
export RL_EPISODES="${RL_EPISODES:-8}"
export RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-1}"
export RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-4}"
export RL_MAX_STEPS="${RL_MAX_STEPS:-6}"
export RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-1024}"
export RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-2048}"
export RL_POOL_SIZE="${RL_POOL_SIZE:-4}"
export RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-1}"
export RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-2}"
export RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-0}"
export RL_GPUS="${RL_GPUS:-0,1}"
export GPU_POOL="${GPU_POOL:-$RL_GPUS}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-0}"
export RL_SAVE_FREQ="${RL_SAVE_FREQ:-1000}"
export RL_CKPT_FREQ="${RL_CKPT_FREQ:-1000}"
# wandb/beaker tracking is noise for a dry-run.
export WANDB_MODE="${WANDB_MODE:-disabled}"

[[ -d "$RL_INIT_MODEL" ]] || { echo "ERROR: missing merged model $RL_INIT_MODEL" >&2; exit 2; }

pkill -f 'vllm.entrypoints.openai.api_server' 2>/dev/null || true
ray stop --force >/dev/null 2>&1 || true
sleep 3

echo "===== rl dry-run ====="
echo "  node        : $(hostname)"
echo "  init model  : $RL_INIT_MODEL"
echo "  dataset     : $RL_DATASET_NAME"
echo "  output      : $RL_OUTPUT_DIR"
nvidia-smi -L || true

exec bash "$REPO/scripts/tmax/train_rl_grpo.sh"
