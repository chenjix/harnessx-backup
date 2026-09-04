#!/bin/bash
# RL-only from the latest 9B tournament-evolve outcomes (rep22). No SFT, no evolve.
#
# Task pool is ranked from sibling pass/fail:
#   drop unanimous 0/1 and mastered, prefer split (near 50%) tasks.
# Policy is Qwen3.5-9B base. Trainer is the same 6+2 / 4x8 / 512-episode shape.
#
# Submit:
#   unset INIT_LORA_PATH ADAPTER_DIR RL_INIT_MODEL
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_rl_from_evolve.sbatch
#
#SBATCH --job-name=tmax-rl-evol
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=2-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_rl_evol_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_rl_evol_%j.err

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
    echo "WARN: $_OI_PY missing — using $RL_PYTHON"
  fi
fi
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export RL_GPUS="${RL_GPUS:-$GPU_POOL}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"
export WANDB_MODE="${WANDB_MODE:-disabled}"
export RL_WALL_TIMEOUT="${RL_WALL_TIMEOUT:-0}"

# Latest finished 9B tournament evolve (rep22, iters 1–5).
export RL_EVOLVE_REPLICATE="${RL_EVOLVE_REPLICATE:-22}"
export RL_EVOLVE_MASTERED="${RL_EVOLVE_MASTERED:-$REPO/outputs/tmax_coevolve/rep22/mastered_i5.json}"
export RL_DATASET_NAME="${RL_DATASET_NAME:-tmax_rl_rep22_split50}"
export RL_N_TASKS="${RL_N_TASKS:-50}"
export RL_SEED="${RL_SEED:-42}"
export HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_only200.json}"
export RL_REBUILD_DATASET="${RL_REBUILD_DATASET:-1}"

export RL_EPISODES="${RL_EPISODES:-512}"
export RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-4}"
export RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-8}"
export RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-2}"
export RL_N_LEARNERS="${RL_N_LEARNERS:-6}"
export RL_N_VLLM="${RL_N_VLLM:-2}"
export RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-3}"
export RL_SEQUENCE_PARALLEL="${RL_SEQUENCE_PARALLEL:-1}"
export RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-16384}"
export RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-4096}"
export RL_MAX_STEPS="${RL_MAX_STEPS:-40}"
# Split tasks should have variance; keep 0 so the first steps still train if
# the base policy cannot submit under vanillux yet.
export RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-0}"
export RL_SAVE_FREQ="${RL_SAVE_FREQ:-4}"
export RL_CKPT_FREQ="${RL_CKPT_FREQ:-4}"
export RL_EXP_NAME="${RL_EXP_NAME:-tmax-rl-evol}"
export RL_OUTPUT_DIR="${RL_OUTPUT_DIR:-$REPO/outputs/rl/${RL_DATASET_NAME}_${SLURM_JOB_ID}}"
export RUN_TAG="${RUN_TAG:-rl-evol-${SLURM_JOB_ID}}"
export LOG_ROOT="${LOG_ROOT:-$REPO/logs/$RUN_TAG}"

# Base 9B — do not inherit an SFT merge from the submit shell.
unset INIT_LORA_PATH ADAPTER_DIR || true
export RL_INIT_MODEL="${RL_INIT_MODEL:-Qwen/Qwen3.5-9B}"

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/rl" "$LOG_ROOT"
[[ -x "$RL_PYTHON" ]] || { echo "ERROR: RL_PYTHON not executable: $RL_PYTHON" >&2; exit 2; }

if [[ "$CLEAN_STALE" == "1" ]]; then
  pkill -f 'vllm.entrypoints.openai.api_server' 2>/dev/null || true
  pkill -f 'open_instruct/grpo_fast.py' 2>/dev/null || true
  ray_bin="$(dirname "$RL_PYTHON")/ray"
  [[ -x "$ray_bin" ]] && "$ray_bin" stop --force >/dev/null 2>&1 || true
  sleep 5
fi

echo "===== RL-from-evolve plan ====="
echo "  RL_PYTHON          : $RL_PYTHON"
echo "  RL_INIT_MODEL      : $RL_INIT_MODEL"
echo "  evolve replicate   : $RL_EVOLVE_REPLICATE"
echo "  mastered           : $RL_EVOLVE_MASTERED"
echo "  RL_DATASET_NAME    : $RL_DATASET_NAME  n=$RL_N_TASKS"
echo "  episodes           : $RL_EPISODES  group=${RL_UNIQUE_PROMPTS}x${RL_SAMPLES_PER_PROMPT}"
echo "  layout             : learners=$RL_N_LEARNERS vllm=$RL_N_VLLM  resp=$RL_RESPONSE_LENGTH"
echo "  filter_zero_std    : $RL_FILTER_ZERO_STD"
echo "  RL_OUTPUT_DIR      : $RL_OUTPUT_DIR"
echo "  LOG_ROOT           : $LOG_ROOT"

exec bash "$REPO/scripts/tmax/train_rl_grpo.sh"
