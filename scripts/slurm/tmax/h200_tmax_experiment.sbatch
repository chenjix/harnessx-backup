#!/bin/bash
# Canonical 8xH200 worker for the documented 4B/9B experiment matrix.
# Submit through scripts/tmax/settings/run_*.sh; do not invoke without setting
# MODEL_SIZE, TMAX_PIPELINE, SFT_ROLLOUT_MODE, and REPLICATE.
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=5-00:00:00
#SBATCH --requeue
#SBATCH --output=tmax_%x_%j.out
#SBATCH --error=tmax_%x_%j.err

set -Eeuo pipefail

REPO="${REPO:-${SLURM_SUBMIT_DIR:-$PWD}}"
[[ -f "$REPO/scripts/tmax/run_loop_tmax_coevolve.sh" ]] || {
  echo "ERROR: submit from the repository root or export REPO=/path/to/repo" >&2
  exit 2
}
cd "$REPO"

: "${MODEL_SIZE:?MODEL_SIZE must be 4b or 9b}"
: "${TMAX_PIPELINE:?TMAX_PIPELINE must be sft or rl}"
: "${SFT_ROLLOUT_MODE:?SFT_ROLLOUT_MODE must be single, cross, or none}"
: "${REPLICATE:?REPLICATE is required}"
case "$MODEL_SIZE" in 4b|9b) ;; *) echo "ERROR: MODEL_SIZE=$MODEL_SIZE" >&2; exit 2 ;; esac
case "$TMAX_PIPELINE:$SFT_ROLLOUT_MODE" in
  sft:single|sft:cross|rl:none) ;;
  *) echo "ERROR: invalid pipeline/rollout pair: $TMAX_PIPELINE/$SFT_ROLLOUT_MODE" >&2; exit 2 ;;
esac

export PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
export VLLM_VENV="${VLLM_VENV:-$(dirname "$(dirname "$PYTHON_BIN")")}"
export SFT_PYTHON="${SFT_PYTHON:-$PYTHON_BIN}"
export RL_PYTHON="${RL_PYTHON:-$REPO/tmax/training/open-instruct/.venv/bin/python}"
export MODEL_SIZE META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export WANDB_MODE="${WANDB_MODE:-offline}"
export SHARED_BASE="${SHARED_BASE:-1}"
[[ "$SHARED_BASE" == 1 ]] && export TMAX_SHARED_BASE_TAG="${TMAX_SHARED_BASE_TAG:-hx-tmax-base:1}"

# Shared multi-iteration harness search and promotion policy.
export START_ITER="${START_ITER:-1}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-2}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-0}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-0}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-$([[ "$MODEL_SIZE" == 4b ]] && echo 8 || echo 4)}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-8}"
export TMAX_MAX_STEPS="${TMAX_MAX_STEPS:-80}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export ACCEPT_TIES="${ACCEPT_TIES:-1}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"
export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
export ROTATE_FROM_ITER="${ROTATE_FROM_ITER:-2}"
export EVOLVE_SET_SIZE="${EVOLVE_SET_SIZE:-50}"
export EVOLVE_WALL_CLOCK_S="${EVOLVE_WALL_CLOCK_S:-14400}"
export EVOLVE_EXTRA_ARGS="${EVOLVE_EXTRA_ARGS:---fanout 5 --fanout-keep 5 --fanout-concurrent 5 --fanout-mode tournament --fanout-eval-concurrent 5 --regression-tolerance 0.04 --explore-every 0 --skip-final-score}"
export EVOLVE_TASKS_JSON="${EVOLVE_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
export HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_only200.json}"
export INIT_LORA_PATH="${INIT_LORA_PATH:-}"

if [[ "$TMAX_PIPELINE" == sft ]]; then
  export ENABLE_SFT=1 ENABLE_RL=0
  export MIN_TRAJS="${MIN_TRAJS:-20}" MAX_TRAJS="${MAX_TRAJS:-0}"
  export PER_TASK="${PER_TASK:-1}" MAX_PAIRS_PER_TRAJ="${MAX_PAIRS_PER_TRAJ:-32}"
  export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
  export CORPUS_PREFER_CURRENT="${CORPUS_PREFER_CURRENT:-1}"
  export CORPUS_MAX_PREV_FRAC="${CORPUS_MAX_PREV_FRAC:-0.4}"
  export SFT_EPOCHS="${SFT_EPOCHS:-2}"
  export SFT_EFFECTIVE_BATCH="${SFT_EFFECTIVE_BATCH:-64}"
  export SFT_PER_DEVICE_BS="${SFT_PER_DEVICE_BS:-$([[ "$MODEL_SIZE" == 4b ]] && echo 2 || echo 1)}"
  if [[ "$SFT_ROLLOUT_MODE" == single ]]; then
    # Roll/eat only the harness selected by stage B; discard tournament siblings.
    export SFT_GEN_ROLLOUT=1
    export SFT_GEN_TASKS="${SFT_GEN_TASKS:-100}"
    export SFT_GEN_CONCURRENT="${SFT_GEN_CONCURRENT:-8}"
    export CORPUS_EVAL_HARNESS=1
    export CORPUS_WINNER_ONLY=1
  else
    # Ablation: learn from successful trajectories across tournament candidates.
    # No B2 single-harness top-up, no winner fingerprint filter/prompt rewrite.
    export SFT_GEN_ROLLOUT=0
    export CORPUS_EVAL_HARNESS=0
    export CORPUS_WINNER_ONLY=0
  fi
else
  export ENABLE_SFT=0 ENABLE_RL=1 SFT_GEN_ROLLOUT=0 MASTERY_REQUIRE_CORPUS=0
  export RL_N_TASKS="${RL_N_TASKS:-100}"
  export RL_EPISODES="${RL_EPISODES:-5120}"
  export RL_N_LEARNERS="${RL_N_LEARNERS:-6}" RL_N_VLLM="${RL_N_VLLM:-2}"
  export RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-3}"
  export RL_SEQUENCE_PARALLEL="${RL_SEQUENCE_PARALLEL:-1}"
  export RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-32768}"
  export RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-4096}"
  export RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-16}"
  export RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-2}"
  export RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-2}"
  export RL_LR="${RL_LR:-1e-6}"
  export RL_TOOL_PARSER="${RL_TOOL_PARSER:-vllm_qwen3_xml}"
  export RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-1}"
  export RL_ACTIVE_SAMPLING="${RL_ACTIVE_SAMPLING:-1}"
fi

echo "===== Tmax experiment ====="
echo "setting    : ${MODEL_SIZE} evolve+${TMAX_PIPELINE} rollout=${SFT_ROLLOUT_MODE}"
echo "rep/iters  : ${REPLICATE} / ${START_ITER}..${N_ITERS}"
echo "evolve     : rounds=$EVOLVE_ROUNDS args=$EVOLVE_EXTRA_ARGS"
echo "SFT flags  : enabled=$ENABLE_SFT gen=$SFT_GEN_ROLLOUT eval_harness=${CORPUS_EVAL_HARNESS:-n/a} winner_only=${CORPUS_WINNER_ONLY:-n/a}"
echo "RL flags   : enabled=$ENABLE_RL tasks=${RL_N_TASKS:-n/a} episodes=${RL_EPISODES:-n/a}"
echo "repo/node  : $REPO / $(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

if [[ "${TMAX_CONFIG_ONLY:-0}" == "1" ]]; then
  echo "TMAX_CONFIG_ONLY=1 — configuration validated; stopping before preflight/run."
  exit 0
fi

if [[ "$TMAX_PIPELINE" == rl && ! -x "$RL_PYTHON" ]]; then
  echo "ERROR: RL_PYTHON not executable: $RL_PYTHON" >&2
  echo "Run: bash scripts/tmax/setup_envs.sh rl" >&2
  exit 2
fi

docker info >/dev/null 2>&1 || { echo "ERROR: Docker unavailable" >&2; exit 2; }
source "$REPO/scripts/tmax/size_min_image_gb.sh"
MIN_IMAGE_GB="$MIN_IMAGE_GB" bash "$REPO/scripts/tmax/docker_preflight.sh"

exec bash "$REPO/scripts/tmax/run_loop_tmax_coevolve.sh"
