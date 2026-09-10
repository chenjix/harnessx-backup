#!/bin/bash
#SBATCH --job-name=rep48-r1r4-h102
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=1-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/rep48_r1r4_h102_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/rep48_r1r4_h102_%j.err

set -Eeuo pipefail

ROOT=/fsx/home/jixuan.chen/harnessx-backup
VENV=/fsx/home/jixuan.chen/.venv
cd "$ROOT"
mkdir -p /fsx/home/jixuan.chen/logs

export PYTHON_BIN="$VENV/bin/python"
export VLLM_VENV="$VENV"
export MODEL_SIZE=9b
export GPU_POOL=0,1,2,3,4,5,6,7
export TMAX_CONCURRENT=8
export CLEAN_STALE=1
export RESUME="${RESUME:-0}"
export WANDB_MODE=offline

if pgrep -f 'vllm.entrypoints.openai.api_server' >/dev/null 2>&1; then
  pkill -f 'vllm.entrypoints.openai.api_server' || true
  sleep 8
fi

exec bash "$ROOT/scripts/with_server_pool.sh" -- \
  bash "$ROOT/scripts/tmax/eval_rep48_r1_r4_holdout102.sh"
