#!/bin/bash
# Smoke-test Tmax online RL (DPPO / grpo_fast) on 1×H200 node.
#
# What it checks:
#   1) build RL train set from taxonomy (default 8 tasks, exclude holdout-102)
#   2) merge coevolve SFT LoRA → full weights (if adapter exists)
#   3) run a tiny grpo_fast job (few episodes, short context)
#
# Submit:
#   sbatch scripts/slurm/tmax/h200_rl_smoke.sbatch
#
# Slightly larger smoke (still short):
#   sbatch --export=ALL,RL_N_TASKS=16,RL_EPISODES=32 \
#     scripts/slurm/tmax/h200_rl_smoke.sbatch
#
# Full 100-task dataset build only (no train) — not this script; use:
#   RL_N_TASKS=100 python -m recipe.tb2_sft.src.build_tmax_rl_dataset --from-taxonomy --name tmax_rl_train100
#
#SBATCH --job-name=tmax-rl-smoke
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:2
#SBATCH --cpus-per-task=24
#SBATCH --mem=0
#SBATCH --time=0-04:00:00
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_rl_smoke_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_rl_smoke_%j.err

set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"
mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/rl"

echo "===== job ====="
echo "job     : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node    : $(hostname)"
echo "gpus    : ${CUDA_VISIBLE_DEVICES:-unset}"
echo "start   : $(date -u '+%F %T UTC')"
nvidia-smi -L || true

export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export SFT_PYTHON="${SFT_PYTHON:-$PYTHON_BIN}"
export RL_PYTHON="${RL_PYTHON:-$PYTHON_BIN}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"

# Smoke knobs (override via --export=ALL,...)
export SMOKE_NAME="${SMOKE_NAME:-tmax_rl_smoke}"
export RL_N_TASKS="${RL_N_TASKS:-8}"
export RL_EPISODES="${RL_EPISODES:-16}"
export RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-2}"
export RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-4}"
export RL_MAX_STEPS="${RL_MAX_STEPS:-8}"
export RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-1024}"
export RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-4096}"
export RL_POOL_SIZE="${RL_POOL_SIZE:-8}"
export RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-1}"
export RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-2}"
export INSTALL_RAY="${INSTALL_RAY:-1}"
# Use first two visible GPUs by default (matches --gres=gpu:h200:2)
export RL_GPUS="${RL_GPUS:-0,1}"
export GPU_POOL="${GPU_POOL:-$RL_GPUS}"
export ADAPTER_DIR="${ADAPTER_DIR:-$REPO/outputs/sft/tmax_coev_rep1_i1}"
export RL_OUTPUT_DIR="${RL_OUTPUT_DIR:-$REPO/outputs/rl/${SMOKE_NAME}_${SLURM_JOB_ID}}"
export RL_DATASET_NAME="${RL_DATASET_NAME:-${SMOKE_NAME}_data}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

if [[ "$CLEAN_STALE" == "1" ]]; then
  pkill -f 'vllm.entrypoints.openai.api_server' 2>/dev/null || true
  sleep 3
fi

echo "===== smoke plan ====="
echo "  RL_N_TASKS           : $RL_N_TASKS   (holdout-102 excluded)"
echo "  RL_EPISODES          : $RL_EPISODES"
echo "  RL_GPUS              : $RL_GPUS"
echo "  ADAPTER_DIR          : $ADAPTER_DIR"
echo "  RL_OUTPUT_DIR        : $RL_OUTPUT_DIR"

exec bash "$REPO/scripts/tmax/smoke_rl_grpo.sh"
