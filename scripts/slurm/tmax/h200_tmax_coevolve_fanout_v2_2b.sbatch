#!/bin/bash
# Tmax harness ↔ SFT co-evolution, fan-out v2, Qwen3.5-2B (no INIT_LORA).
#
# Same evolve method as h200_tmax_coevolve_fanout_v2.sbatch (9b). 2B weights
# are ~4× smaller than 9B, so the 9b concurrency (TMAX=8, HOLDOUT=2,
# fanout-concurrent 4) leaves H200s idle between docker steps. This launcher
# oversubscribes docker+agent workers and lets all 8 fan-out proposals run
# in one wave.
#
# Replicate 15 is the 9b v2 chain (job 2895). Use 16 for 2B.
#
#   unset INIT_LORA_PATH
#   sbatch --export=ALL,REPLICATE=16,N_ITERS=3,ENABLE_RL=0,INIT_LORA_PATH= \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_fanout_v2_2b.sbatch
#
#SBATCH --job-name=tmax-coev-2b
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=5-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_coev_2b_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_coev_2b_%j.err

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
export MODEL_SIZE="${MODEL_SIZE:-2b}"
export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

export SHARED_BASE="${SHARED_BASE:-1}"
if [[ "$SHARED_BASE" == "1" ]]; then
  export TMAX_SHARED_BASE_TAG="${TMAX_SHARED_BASE_TAG:-hx-tmax-base:1}"
fi

export REPLICATE="${REPLICATE:-16}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-2}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-2}"
export MIN_TRAJS="${MIN_TRAJS:-20}"
export MAX_TRAJS="${MAX_TRAJS:-100}"
export PER_TASK="${PER_TASK:-3}"
export MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
# 9b used 8/2. 2B decode is cheap; 4× docker oversubscribe keeps 8 vLLM
# replicas batching. Holdout used to leave 6/8 GPUs idle at concurrent=2.
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-32}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-32}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
export CORPUS_PREFER_CURRENT="${CORPUS_PREFER_CURRENT:-1}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export ACCEPT_TIES="${ACCEPT_TIES:-1}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"
export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
export ROTATE_FROM_ITER="${ROTATE_FROM_ITER:-2}"
export EVOLVE_SET_SIZE="${EVOLVE_SET_SIZE:-50}"

export EVOLVE_WALL_CLOCK_S="${EVOLVE_WALL_CLOCK_S:-14400}"

# Same v2 method as 9b; fanout-concurrent 8 = one wave of 8 proposals.
export EVOLVE_EXTRA_ARGS="${EVOLVE_EXTRA_ARGS:---fanout 8 --fanout-keep 3 --fanout-concurrent 8 --fanout-mode v2 --explore-every 3 --probe-solved 4 --probe-unsolved 2}"

# KV room on H200 for ~4 in-flight seqs/GPU at TMAX_CONCURRENT=32.
export VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-64}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"

# 2B LoRA on 141GB: raise per-device batch vs 9b's 1.
export SFT_PER_DEVICE_BS="${SFT_PER_DEVICE_BS:-4}"
export SFT_EFFECTIVE_BATCH="${SFT_EFFECTIVE_BATCH:-64}"

export ENABLE_RL="${ENABLE_RL:-0}"
export RL_EPISODES="${RL_EPISODES:-512}"
export RL_N_TASKS="${RL_N_TASKS:-100}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"

export EVOLVE_TASKS_JSON="${EVOLVE_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
export HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_only200.json}"

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/tmax_coevolve"

echo "===== plan ====="
echo "  MODEL_SIZE         : $MODEL_SIZE  (Qwen/Qwen3.5-2B, no INIT_LORA)"
echo "  REPLICATE          : $REPLICATE"
echo "  N_ITERS            : $N_ITERS"
echo "  EVOLVE_ROUNDS      : $EVOLVE_ROUNDS"
echo "  EXTRA_EVOLVE_ROUNDS: $EXTRA_EVOLVE_ROUNDS"
echo "  MAX_SFT_RETRIES    : $MAX_SFT_RETRIES"
echo "  MIN/MAX_TRAJS      : $MIN_TRAJS / $MAX_TRAJS"
echo "  TMAX_CONCURRENT    : $TMAX_CONCURRENT"
echo "  HOLDOUT_CONCURRENT : $HOLDOUT_CONCURRENT"
echo "  VLLM_MAX_NUM_SEQS  : $VLLM_MAX_NUM_SEQS"
echo "  SFT_PER_DEVICE_BS  : $SFT_PER_DEVICE_BS  eff=$SFT_EFFECTIVE_BATCH"
echo "  INIT_LORA_PATH     : ${INIT_LORA_PATH:-<base>}"
echo "  SEED_HARNESS       : ${SEED_HARNESS:-configs/baseline_tmax_harness.yaml}"
echo "  META_MODEL         : $META_MODEL"
echo "  EVOLVE_EXTRA_ARGS  : $EVOLVE_EXTRA_ARGS"
echo "  SHARED_BASE        : $SHARED_BASE  tag=${TMAX_SHARED_BASE_TAG:-<unset>}"
echo "  ENABLE_RL          : $ENABLE_RL"
echo "  rotation           : ROTATE_EVOLVE_TASKS=$ROTATE_EVOLVE_TASKS from iter $ROTATE_FROM_ITER"

docker info >/dev/null 2>&1 || { echo "ERROR: docker unavailable on $(hostname)" >&2; exit 2; }
# Space check is missing-image based (see scripts/tmax/size_min_image_gb.sh).
# 2895/2898 died on the old 20G-empty floor while the images were already cached.
# shellcheck disable=SC1091
source "$REPO/scripts/tmax/size_min_image_gb.sh"
MIN_IMAGE_GB="$MIN_IMAGE_GB" bash "$REPO/scripts/tmax/docker_preflight.sh" \
  || { echo "ERROR: not enough room in this node's image store for task images" >&2; exit 2; }

"$PYTHON_BIN" - <<'PY' || echo "WARN: bedrock preflight failed"
import boto3, os
rt = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION_NAME", "us-west-2"))
print("bedrock-runtime OK", rt.meta.region_name)
PY

exec bash "$REPO/scripts/tmax/run_loop_tmax_coevolve.sh"
