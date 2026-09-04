#!/bin/bash
# Tmax harness ↔ SFT co-evolution, tournament evolve, 9B *base*, new SFT recipe.
#
# Same two-round / 5+5 tournament as h200_tmax_coevolve_tournament.sbatch
# (rep19). The difference is corpus construction:
#   - only the holdout-eval (winner) harness's traj dirs
#   - inject that harness's system_prompt.txt
#   - quality prefers compact, non-loop successes (not long/slow traces)
#   - PER_TASK=1, MAX_PAIRS_PER_TRAJ=32
#   - SFT-gen tops the harvest plane up to 100 unique non-holdout tasks
#   - older-iteration trajs capped at 40% of the selected set
#   - inner gate 0.04; holdout/SFT-gen concurrent=8
#
# Start from Qwen3.5-9B base (no INIT_LORA_PATH). Replicate 22 is free
# (21 is the running 4B tournament job 3115).
#
#   unset INIT_LORA_PATH EVOLVE_EXTRA_ARGS MIN_IMAGE_GB
#   sbatch --export=ALL,REPLICATE=22,N_ITERS=3,ENABLE_RL=0,MODEL_SIZE=9b,INIT_LORA_PATH= \
#     scripts/slurm/tmax/h200_tmax_coevolve_tournament_sft.sbatch
#
#SBATCH --job-name=tmax-coev-tns
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=5-00:00:00
#SBATCH --requeue
#SBATCH --output=tmax_coev_tns_%j.out
#SBATCH --error=tmax_coev_tns_%j.err

set -Eeuo pipefail

# Submit from the repository root. SLURM_SUBMIT_DIR is captured by sbatch and
# makes this launcher usable by another checkout/user without editing paths.
REPO="${REPO:-${SLURM_SUBMIT_DIR:-$PWD}}"
[[ -f "$REPO/scripts/tmax/run_loop_tmax_coevolve.sh" ]] || {
  echo "ERROR: REPO is not a HarnessX checkout: $REPO" >&2
  echo "       cd to the repository root before sbatch, or export REPO=/path/to/repo" >&2
  exit 2
}
cd "$REPO"

echo "===== job ====="
echo "job     : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node    : $(hostname)"
echo "gpus    : ${CUDA_VISIBLE_DEVICES:-unset}"
echo "start   : $(date -u '+%F %T UTC')"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

export PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
export SFT_PYTHON="${SFT_PYTHON:-$PYTHON_BIN}"
export VLLM_VENV="${VLLM_VENV:-$(dirname "$(dirname "$PYTHON_BIN")")}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

export SHARED_BASE="${SHARED_BASE:-1}"
if [[ "$SHARED_BASE" == "1" ]]; then
  export TMAX_SHARED_BASE_TAG="${TMAX_SHARED_BASE_TAG:-hx-tmax-base:1}"
fi

export REPLICATE="${REPLICATE:-22}"
export START_ITER="${START_ITER:-1}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-2}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-0}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-0}"
export MIN_TRAJS="${MIN_TRAJS:-20}"
export MAX_TRAJS="${MAX_TRAJS:-0}"
export PER_TASK="${PER_TASK:-1}"
export MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-4}"
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

export ENABLE_RL="${ENABLE_RL:-0}"
export RL_EPISODES="${RL_EPISODES:-512}"
export RL_N_TASKS="${RL_N_TASKS:-100}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"

export EVOLVE_TASKS_JSON="${EVOLVE_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
export HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_only200.json}"

mkdir -p "$REPO/outputs/tmax_coevolve"

echo "===== plan ====="
echo "  MODEL_SIZE         : $MODEL_SIZE  (Qwen/Qwen3.5-9B, no INIT_LORA)"
echo "  REPLICATE          : $REPLICATE"
echo "  START_ITER         : $START_ITER"
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
