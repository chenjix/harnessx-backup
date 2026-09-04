#!/bin/bash
# Isolated RL check: 50 taxonomy tasks × Qwen3.5-9B base.
#   1. reuse/build 50-task RL dataset (holdout-102 excluded)
#   2. one DPPO/GRPO-fast run from hub 9B  (no before-eval)
#   3. eval AFTER on the same 50
#
# Submit:
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_rl_base50.sbatch
#
# Also score hub 9B before training:
#   sbatch --export=ALL,SKIP_BEFORE=0 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_rl_base50.sbatch
#
# Smoke (~3-5h, 8 episodes, 8 tasks):
#   sbatch --export=ALL,RL_N_TASKS=8,RL_EPISODES=8,RL_UNIQUE_PROMPTS=1,RL_SAMPLES_PER_PROMPT=4,LIMIT=8 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_rl_base50.sbatch
#
# Resume after a train that already wrote a ckpt:
#   sbatch --export=ALL,SKIP_TRAIN=1,RL_OUTPUT_DIR=/path/to/rl/dir \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_rl_base50.sbatch
#
#SBATCH --job-name=tmax-rl-base50
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=2-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_rl_base50_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_rl_base50_%j.err

set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

echo "===== job ====="
echo "job     : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node    : $(hostname)"
echo "gpus    : ${CUDA_VISIBLE_DEVICES:-unset}"
echo "start   : $(date -u '+%F %T UTC')"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export SFT_PYTHON="${SFT_PYTHON:-$PYTHON_BIN}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
_OI_PY="$REPO/tmax/training/open-instruct/.venv/bin/python"
if [[ -z "${RL_PYTHON:-}" ]]; then
  if [[ -x "$_OI_PY" ]]; then
    export RL_PYTHON="$_OI_PY"
  else
    export RL_PYTHON="$PYTHON_BIN"
    echo "WARN: $_OI_PY missing — using serving venv $RL_PYTHON"
  fi
fi
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export RL_GPUS="${RL_GPUS:-$GPU_POOL}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"
export RL_WALL_TIMEOUT="${RL_WALL_TIMEOUT:-0}"
export RL_INIT_MODEL="${RL_INIT_MODEL:-Qwen/Qwen3.5-9B}"
export SKIP_BEFORE="${SKIP_BEFORE:-1}"

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/rl"

[[ -x "$RL_PYTHON" ]] || { echo "ERROR: RL_PYTHON not executable: $RL_PYTHON" >&2; exit 2; }

exec bash "$REPO/scripts/tmax/run_rl_base50.sh"
