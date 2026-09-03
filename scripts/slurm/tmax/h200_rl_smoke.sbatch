#!/bin/bash
# Smoke-test: SFT LoRA → merge → tiny DPPO/GRPO on taxonomy tasks (holdout excluded).
#
# Requires a finished SFT adapter (default: coevolve rep1 i1).
#
# Submit:
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_rl_smoke.sbatch
#
# Or from home copy (kept in sync):
#   sbatch /fsx/home/jixuan.chen/h200_rl_smoke.sbatch
#
#SBATCH --job-name=tmax-rl-smoke
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:2
#SBATCH --cpus-per-task=24
#SBATCH --mem=0
#SBATCH --time=0-06:00:00
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
# Dedicated open-instruct uv env is not on this checkout; the serving venv
# already has ray+deepspeed+openenv+vllm (verified). Fall back to it.
_OI_PY="$REPO/tmax/training/open-instruct/.venv/bin/python"
if [[ -z "${RL_PYTHON:-}" ]]; then
  if [[ -x "$_OI_PY" ]]; then
    export RL_PYTHON="$_OI_PY"
  else
    export RL_PYTHON="$PYTHON_BIN"
  fi
fi
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"

export SMOKE_NAME="${SMOKE_NAME:-tmax_rl_smoke}"
# This job asks for 2 GPUs. smoke_rl_grpo.sh defaults to 4 learners + 2 vLLM
# (6 GPUs) unless we pin the split — that mismatch is what would die after merge.
export RL_N_TASKS="${RL_N_TASKS:-4}"
export RL_EPISODES="${RL_EPISODES:-8}"
export RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-1}"
export RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-4}"
export RL_N_LEARNERS="${RL_N_LEARNERS:-1}"
export RL_N_VLLM="${RL_N_VLLM:-1}"
export RL_MAX_STEPS="${RL_MAX_STEPS:-6}"
export RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-1024}"
export RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-2048}"
export RL_POOL_SIZE="${RL_POOL_SIZE:-4}"
export RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-1}"
export RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-2}"
export RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-0}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"
export RL_GPUS="${RL_GPUS:-0,1}"
export GPU_POOL="${GPU_POOL:-$RL_GPUS}"
[[ -x "$RL_PYTHON" ]] || { echo "ERROR: RL_PYTHON not executable: $RL_PYTHON" >&2; exit 2; }
[[ -f "$REPO/outputs/sft/tmax_coev_rep1_i1/adapter_config.json" ]] || {
  echo "ERROR: default adapter missing: $REPO/outputs/sft/tmax_coev_rep1_i1" >&2
  exit 2
}
# Proven SFT checkpoint from coevolve rep1
export ADAPTER_DIR="${ADAPTER_DIR:-$REPO/outputs/sft/tmax_coev_rep1_i1}"
export RL_OUTPUT_DIR="${RL_OUTPUT_DIR:-$REPO/outputs/rl/${SMOKE_NAME}_${SLURM_JOB_ID}}"
export RL_DATASET_NAME="${RL_DATASET_NAME:-${SMOKE_NAME}_${SLURM_JOB_ID}_data}"
export RL_MERGED_DIR="${RL_MERGED_DIR:-$REPO/outputs/rl/merged/${SMOKE_NAME}_${SLURM_JOB_ID}}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

if [[ "$CLEAN_STALE" == "1" ]]; then
  pkill -f 'vllm.entrypoints.openai.api_server' 2>/dev/null || true
  sleep 3
fi

echo "===== smoke plan ====="
echo "  ADAPTER_DIR          : $ADAPTER_DIR"
echo "  RL_PYTHON            : $RL_PYTHON"
echo "  RL_N_TASKS           : $RL_N_TASKS   (holdout-102 excluded)"
echo "  RL_EPISODES          : $RL_EPISODES"
echo "  RL layout            : learners=$RL_N_LEARNERS vllm=$RL_N_VLLM gpus=$RL_GPUS"
echo "  RL_OUTPUT_DIR        : $RL_OUTPUT_DIR"

exec bash "$REPO/scripts/tmax/smoke_rl_grpo.sh"
