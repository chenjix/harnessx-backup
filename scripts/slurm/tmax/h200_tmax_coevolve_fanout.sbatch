#!/bin/bash
# Tmax harness ↔ SFT co-evolution using the screened-population evolve method
# (fan-out N proposals, structural + LLM + mini-eval screens, keep k for full
# eval). Same outer loop as h200_tmax_coevolve.sbatch; only stage A changes.
#
# Default evolve flags match the A100 A/B treatment arm:
#   --fanout 8 --fanout-keep 2 --fanout-concurrent 4 --explore-every 3
#
# ENABLE_RL stays 0. Isolated RL is not proven on this checkout yet; do not
# combine fan-out + RL on the first run. Use REPLICATE=14 (1, 2, 9, 10, 12,
# 13, 27 are already occupied). Job 2691 is tmax-coev-rl on rep13 — do not
# overlap that node or that replicate.
#
# Full 3-iter chain (submit ONE job — 2717/2718/2719 were three variants of
# the same REPLICATE=14 that all died on docker space preflight):
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_fanout.sbatch
#
# Smoke (1 iter, 2 evolve rounds, no SFT retries):
#   sbatch --export=ALL,N_ITERS=1,EVOLVE_ROUNDS=2,MAX_SFT_RETRIES=0 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_fanout.sbatch
#
# Warm-start from proven SFT-500:
#   sbatch --export=ALL,INIT_LORA_PATH=/fsx/home/jixuan.chen/harnessx-backup/outputs/sft/qwen35_9b_tmax_only500_on102_v2 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_fanout.sbatch
#
# Override fan-out without editing this file:
#   sbatch --export=ALL,EVOLVE_EXTRA_ARGS='--fanout 4 --fanout-keep 2 --explore-every 3' \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_fanout.sbatch
#
#SBATCH --job-name=tmax-coev-fo
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=5-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_coev_fo_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_coev_fo_%j.err

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

# Shared python3/pip/pytest base layer for task images. docker_env falls back
# if hx-tmax-base:1 is not on the node; exporting the tag is still the right
# default after the image-store space incidents.
export SHARED_BASE="${SHARED_BASE:-1}"
if [[ "$SHARED_BASE" == "1" ]]; then
  export TMAX_SHARED_BASE_TAG="${TMAX_SHARED_BASE_TAG:-hx-tmax-base:1}"
fi

export REPLICATE="${REPLICATE:-14}"
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
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-8}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
export CORPUS_PREFER_CURRENT="${CORPUS_PREFER_CURRENT:-1}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export ACCEPT_TIES="${ACCEPT_TIES:-1}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"
export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
export ROTATE_FROM_ITER="${ROTATE_FROM_ITER:-2}"
export EVOLVE_SET_SIZE="${EVOLVE_SET_SIZE:-50}"

# Fan-out meta wall-clock: 8 proposals in waves of 4, plus screens. 3h was
# sized for a single meta call; 4h leaves headroom without changing eval time.
export EVOLVE_WALL_CLOCK_S="${EVOLVE_WALL_CLOCK_S:-14400}"

# ── the new evolve method ──────────────────────────────────────────────────
export EVOLVE_EXTRA_ARGS="${EVOLVE_EXTRA_ARGS:---fanout 8 --fanout-keep 2 --fanout-concurrent 4 --explore-every 3}"

export ENABLE_RL="${ENABLE_RL:-0}"
export RL_EPISODES="${RL_EPISODES:-512}"
export RL_N_TASKS="${RL_N_TASKS:-100}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"

export EVOLVE_TASKS_JSON="${EVOLVE_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
export HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_only200.json}"

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
echo "  EVOLVE_EXTRA_ARGS  : $EVOLVE_EXTRA_ARGS"
echo "  SHARED_BASE        : $SHARED_BASE  tag=${TMAX_SHARED_BASE_TAG:-<unset>}"
echo "  ENABLE_RL          : $ENABLE_RL  (leave 0 until isolated RL is proven)"
echo "  rotation           : ROTATE_EVOLVE_TASKS=$ROTATE_EVOLVE_TASKS from iter $ROTATE_FROM_ITER"

docker info >/dev/null 2>&1 || { echo "ERROR: docker unavailable on $(hostname)" >&2; exit 2; }
# Space check is missing-image based (see scripts/tmax/size_min_image_gb.sh).
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
