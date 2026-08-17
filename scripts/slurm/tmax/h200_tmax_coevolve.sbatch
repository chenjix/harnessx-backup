#!/bin/bash
# Tmax harness ↔ SFT co-evolution (multi-iter), one chain, 8× H200.
#
#   sbatch /fsx/home/jixuan.chen/scripts/slurm/tmax/h200_tmax_coevolve.sbatch
#   REPLICATE=2 sbatch --export=ALL /fsx/home/jixuan.chen/scripts/slurm/tmax/h200_tmax_coevolve.sbatch
#
# Smoke (1 iter, 2 evolve rounds):
#   sbatch --export=ALL,N_ITERS=1,EVOLVE_ROUNDS=2,MAX_SFT_RETRIES=0 \
#     /fsx/home/jixuan.chen/scripts/slurm/tmax/h200_tmax_coevolve.sbatch
#
# Warm-start from proven SFT-500:
#   sbatch --export=ALL,INIT_LORA_PATH=/fsx/home/jixuan.chen/harnessx-backup/outputs/sft/qwen35_9b_tmax_only500_on102_v2 \
#     /fsx/home/jixuan.chen/scripts/slurm/tmax/h200_tmax_coevolve.sbatch
#
#SBATCH --job-name=tmax-coevolve
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=4-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_coevolve_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_coevolve_%j.err

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
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

export REPLICATE="${REPLICATE:-1}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-2}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-2}"
export MIN_TRAJS="${MIN_TRAJS:-20}"
export MAX_TRAJS="${MAX_TRAJS:-100}"
export PER_TASK="${PER_TASK:-3}"
export MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-2}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
# Online DPPO/GRPO after SFT (trains on up to RL_N_TASKS taxonomy tasks;
# holdout-102 is eval-only). Default 0 until smoke passes:
#   ENABLE_RL=1 RL_N_TASKS=100 RL_EPISODES=512 sbatch --export=ALL ...
export ENABLE_RL="${ENABLE_RL:-0}"
export RL_EPISODES="${RL_EPISODES:-512}"
export RL_N_TASKS="${RL_N_TASKS:-100}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/tmax_coevolve"

echo "===== plan ====="
echo "  REPLICATE          : $REPLICATE"
echo "  N_ITERS            : $N_ITERS"
echo "  EVOLVE_ROUNDS      : $EVOLVE_ROUNDS"
echo "  EXTRA_EVOLVE_ROUNDS: $EXTRA_EVOLVE_ROUNDS"
echo "  MAX_SFT_RETRIES    : $MAX_SFT_RETRIES"
echo "  MIN/MAX_TRAJS      : $MIN_TRAJS / $MAX_TRAJS"
echo "  INIT_LORA_PATH     : ${INIT_LORA_PATH:-<base>}"
echo "  SEED_HARNESS       : ${SEED_HARNESS:-configs/baseline_tmax_harness.yaml}"
echo "  META_MODEL         : $META_MODEL"
echo "  ENABLE_RL          : $ENABLE_RL  (RL train n=$RL_N_TASKS; holdout=eval only)"
echo "  RL_EPISODES        : $RL_EPISODES"

# Soft Bedrock preflight
"$PYTHON_BIN" - <<'PY' || echo "WARN: bedrock preflight failed"
import boto3, os
rt = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION_NAME", "us-west-2"))
print("bedrock-runtime OK", rt.meta.region_name)
PY

exec bash "$REPO/scripts/tmax/run_loop_tmax_coevolve.sh"
