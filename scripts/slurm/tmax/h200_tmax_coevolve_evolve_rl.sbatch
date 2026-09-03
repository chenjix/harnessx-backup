#!/bin/bash
# Tmax harness evolve + online RL. No SFT.
#
# Each iteration:
#   A0 rotate evolve set → A tournament evolve → B holdout (H ratchet)
#   D2 DPPO/GRPO from base (iter 1) or last accepted RL ckpt
#   E holdout (M ratchet)
#
# Skips B2 SFT-gen, C corpus, D LoRA. RL never sees holdout-102.
# vLLM 0.24 weight sync uses start_weight_update → update_weights →
# finish_weight_update (job 3259 idle-cancel was that protocol missing).
#
# Submit:
#   unset INIT_LORA_PATH ADAPTER_DIR RL_INIT_MODEL
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_evolve_rl.sbatch
#
#SBATCH --job-name=tmax-coev-erl
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=5-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_coev_erl_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_coev_erl_%j.err

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
export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export WANDB_MODE="${WANDB_MODE:-disabled}"

export SHARED_BASE="${SHARED_BASE:-1}"
if [[ "$SHARED_BASE" == "1" ]]; then
  export TMAX_SHARED_BASE_TAG="${TMAX_SHARED_BASE_TAG:-hx-tmax-base:1}"
fi

# Fresh replicate — do not reuse an SFT chain.
export REPLICATE="${REPLICATE:-24}"
export START_ITER="${START_ITER:-1}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-2}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-0}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-0}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-4}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-8}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export ACCEPT_TIES="${ACCEPT_TIES:-1}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"
export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
export ROTATE_FROM_ITER="${ROTATE_FROM_ITER:-2}"
export EVOLVE_SET_SIZE="${EVOLVE_SET_SIZE:-50}"
export EVOLVE_WALL_CLOCK_S="${EVOLVE_WALL_CLOCK_S:-14400}"

export EVOLVE_EXTRA_ARGS="${EVOLVE_EXTRA_ARGS:---fanout 5 --fanout-keep 5 --fanout-concurrent 5 --fanout-mode tournament --fanout-eval-concurrent 5 --regression-tolerance 0.04 --explore-every 0 --skip-final-score}"

# The point of this launcher: evolve then RL, no LoRA SFT.
export ENABLE_SFT=0
export ENABLE_RL=1
export SFT_GEN_ROLLOUT=0
export MASTERY_REQUIRE_CORPUS=0
export RL_N_TASKS="${RL_N_TASKS:-50}"
export RL_EPISODES="${RL_EPISODES:-512}"
export RL_N_LEARNERS="${RL_N_LEARNERS:-6}"
export RL_N_VLLM="${RL_N_VLLM:-2}"
export RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-3}"
export RL_SEQUENCE_PARALLEL="${RL_SEQUENCE_PARALLEL:-1}"
export RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-16384}"
export RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-4096}"
export RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-8}"
export RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-4}"
export RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-2}"
export RL_LR="${RL_LR:-1e-6}"
export RL_TOOL_PARSER="${RL_TOOL_PARSER:-vllm_qwen3_xml}"
export RL_WALL_TIMEOUT="${RL_WALL_TIMEOUT:-0}"
# Keep optimizer steps even while the policy is still learning to submit.
export RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-0}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"

# Must start from the hub 9B, not an SFT merge leftover in the submit shell.
unset INIT_LORA_PATH ADAPTER_DIR || true
unset RL_INIT_MODEL || true
export INIT_LORA_PATH=""

export EVOLVE_TASKS_JSON="${EVOLVE_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
export HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_only200.json}"

[[ -x "$RL_PYTHON" ]] || { echo "ERROR: RL_PYTHON not executable: $RL_PYTHON" >&2; exit 2; }

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/tmax_coevolve" "$REPO/outputs/rl"

echo "===== plan ====="
echo "  path               : harness evolve + RL (ENABLE_SFT=0)"
echo "  MODEL_SIZE         : $MODEL_SIZE  (Qwen/Qwen3.5-9B base, no SFT)"
echo "  REPLICATE          : $REPLICATE"
echo "  N_ITERS            : $N_ITERS"
echo "  EVOLVE_ROUNDS      : $EVOLVE_ROUNDS  (tournament: 2)"
echo "  ENABLE_SFT         : $ENABLE_SFT"
echo "  ENABLE_RL          : $ENABLE_RL"
echo "  RL_PYTHON          : $RL_PYTHON"
echo "  RL_N_TASKS         : $RL_N_TASKS  (prefer evolve set, holdout excluded)"
echo "  RL_EPISODES        : $RL_EPISODES  group=${RL_UNIQUE_PROMPTS}x${RL_SAMPLES_PER_PROMPT}"
echo "  RL layout          : learners=$RL_N_LEARNERS vllm=$RL_N_VLLM  resp=$RL_RESPONSE_LENGTH"
echo "  filter_zero_std    : $RL_FILTER_ZERO_STD"
echo "  INIT_LORA_PATH     : ${INIT_LORA_PATH:-<base>}"
echo "  META_MODEL         : $META_MODEL"
echo "  EVOLVE_EXTRA_ARGS  : $EVOLVE_EXTRA_ARGS"

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
