#!/bin/bash
# Tmax harness ↔ SFT co-evolution on Qwen3.5-4B *base*, new data recipe.
#
# Same loop as h200_tmax_coevolve_tournament_sft.sbatch (9B / job 3165):
#   A0 rotate evolve-50 (from iter 2)
#   A  2-round tournament 5+5, inner gate 0.04
#   B  holdout-102 H ratchet (ties on pass count only)
#   B2 SFT-gen: reuse evolve evals, top up to 100 unique non-holdout, conc=8
#   C  winner_only corpus + eval-harness prompt; compact (non-loop) quality
#   D  LoRA SFT  PER_TASK=1
#   E  holdout-102 M ratchet
#
# Do NOT reuse REPLICATE=21 (old mixed-fanout 4B chain, stuck at 64).
# Default REPLICATE=23 is a fresh chain from Qwen3.5-4B base.
#
#   bash scripts/tmax/submit_tournament_sft_4b.sh
#   # or:
#   unset INIT_LORA_PATH EVOLVE_EXTRA_ARGS MIN_IMAGE_GB
#   sbatch --export=ALL,REPLICATE=23,N_ITERS=3,ENABLE_RL=0,MODEL_SIZE=4b,INIT_LORA_PATH= \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_tournament_4b_sft.sbatch
#
#SBATCH --job-name=tmax-coev-tn4s
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=5-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_coev_tn4s_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_coev_tn4s_%j.err

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
export MODEL_SIZE="${MODEL_SIZE:-4b}"
export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

export SHARED_BASE="${SHARED_BASE:-1}"
if [[ "$SHARED_BASE" == "1" ]]; then
  export TMAX_SHARED_BASE_TAG="${TMAX_SHARED_BASE_TAG:-hx-tmax-base:1}"
fi

export REPLICATE="${REPLICATE:-23}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-2}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-0}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-0}"
export MIN_TRAJS="${MIN_TRAJS:-20}"
export MAX_TRAJS="${MAX_TRAJS:-0}"
export PER_TASK="${PER_TASK:-1}"
export MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
# 4B decode is cheap: 5-way tournament × 8 in-flight is OK on 8×H200.
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-8}"
export SFT_GEN_ROLLOUT="${SFT_GEN_ROLLOUT:-1}"
export SFT_GEN_TASKS="${SFT_GEN_TASKS:-100}"
export SFT_GEN_CONCURRENT="${SFT_GEN_CONCURRENT:-8}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
export CORPUS_PREFER_CURRENT="${CORPUS_PREFER_CURRENT:-1}"
export CORPUS_EVAL_HARNESS="${CORPUS_EVAL_HARNESS:-1}"
export CORPUS_WINNER_ONLY="${CORPUS_WINNER_ONLY:-1}"
export CORPUS_MAX_PREV_FRAC="${CORPUS_MAX_PREV_FRAC:-0.4}"
export MAX_PAIRS_PER_TRAJ="${MAX_PAIRS_PER_TRAJ:-32}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export ACCEPT_TIES="${ACCEPT_TIES:-1}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"
export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
export ROTATE_FROM_ITER="${ROTATE_FROM_ITER:-2}"
export EVOLVE_SET_SIZE="${EVOLVE_SET_SIZE:-50}"

export EVOLVE_WALL_CLOCK_S="${EVOLVE_WALL_CLOCK_S:-14400}"

export EVOLVE_EXTRA_ARGS="${EVOLVE_EXTRA_ARGS:---fanout 5 --fanout-keep 5 --fanout-concurrent 5 --fanout-mode tournament --fanout-eval-concurrent 5 --regression-tolerance 0.04 --explore-every 0 --skip-final-score}"

export VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-64}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
export SFT_PER_DEVICE_BS="${SFT_PER_DEVICE_BS:-2}"
export SFT_EFFECTIVE_BATCH="${SFT_EFFECTIVE_BATCH:-64}"

export ENABLE_RL="${ENABLE_RL:-0}"
export RL_EPISODES="${RL_EPISODES:-512}"
export RL_N_TASKS="${RL_N_TASKS:-100}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"

export EVOLVE_TASKS_JSON="${EVOLVE_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
export HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_only200.json}"

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/tmax_coevolve"

echo "===== plan ====="
echo "  MODEL_SIZE         : $MODEL_SIZE  (Qwen/Qwen3.5-4B, no INIT_LORA)"
echo "  REPLICATE          : $REPLICATE"
echo "  N_ITERS            : $N_ITERS"
echo "  EVOLVE_ROUNDS      : $EVOLVE_ROUNDS  (tournament: 2)"
echo "  EXTRA_EVOLVE_ROUNDS: $EXTRA_EVOLVE_ROUNDS"
echo "  MAX_SFT_RETRIES    : $MAX_SFT_RETRIES"
echo "  MIN/MAX_TRAJS      : $MIN_TRAJS / $MAX_TRAJS"
echo "  PER_TASK           : $PER_TASK"
echo "  CORPUS             : winner_only=$CORPUS_WINNER_ONLY prev_frac=$CORPUS_MAX_PREV_FRAC pairs=$MAX_PAIRS_PER_TRAJ eval_harness=$CORPUS_EVAL_HARNESS"
echo "  INIT_LORA_PATH     : ${INIT_LORA_PATH:-<base>}"
echo "  SEED_HARNESS       : ${SEED_HARNESS:-configs/baseline_tmax_harness.yaml}"
echo "  META_MODEL         : $META_MODEL"
echo "  EVOLVE_EXTRA_ARGS  : $EVOLVE_EXTRA_ARGS"
echo "  TMAX_CONCURRENT    : $TMAX_CONCURRENT"
echo "  HOLDOUT_CONCURRENT : $HOLDOUT_CONCURRENT"
echo "  SFT_GEN            : rollout=$SFT_GEN_ROLLOUT tasks=$SFT_GEN_TASKS conc=$SFT_GEN_CONCURRENT"
echo "  VLLM_MAX_NUM_SEQS  : $VLLM_MAX_NUM_SEQS"
echo "  SFT_PER_DEVICE_BS  : $SFT_PER_DEVICE_BS  eff=$SFT_EFFECTIVE_BATCH"
echo "  SHARED_BASE        : $SHARED_BASE  tag=${TMAX_SHARED_BASE_TAG:-<unset>}"
echo "  ENABLE_RL          : $ENABLE_RL"
echo "  rotation           : ROTATE_EVOLVE_TASKS=$ROTATE_EVOLVE_TASKS from iter $ROTATE_FROM_ITER"

docker info >/dev/null 2>&1 || { echo "ERROR: docker unavailable on $(hostname)" >&2; exit 2; }
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
