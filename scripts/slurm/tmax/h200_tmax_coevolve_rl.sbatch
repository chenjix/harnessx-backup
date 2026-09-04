#!/bin/bash
# Tmax co-evolve WITH online RL: each iteration is
#   A harness evolve → B holdout (harness ratchet) → C corpus → D SFT → D2 RL → E holdout (model ratchet)
#
# RL trains on ≤RL_N_TASKS taxonomy tasks (prefers this iter's evolve set) and
# never sees holdout-102. The 8×H200 layout matches the recipe that actually
# completed (6 ZeRO-3 learners + 2 vLLM engines). Official 9B DPPO is 8 nodes ×
# 65536 response; this is the single-node contraction, not a copy of Beaker.
#
# Do NOT reuse a running replicate. 2235 is rep12; pick a free one (default 13).
#
# Submit:
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_rl.sbatch
#
# 1-iter smoke (still pays evolve+SFT+RL+holdout, ~12-18h):
#   sbatch --export=ALL,N_ITERS=1,EVOLVE_ROUNDS=2,MAX_SFT_RETRIES=0,RL_EPISODES=128 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_rl.sbatch
#
#SBATCH --job-name=tmax-coev-rl
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=5-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_coev_rl_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_coev_rl_%j.err

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
# Prefer the uv env from scripts/tmax/setup_rl_env.sh. It is not on this
# checkout; ~/.venv already has ray+deepspeed+openenv+vllm, so fall back.
_OI_PY="$REPO/tmax/training/open-instruct/.venv/bin/python"
_SERVE_PY="/fsx/home/jixuan.chen/.venv/bin/python"
if [[ -z "${RL_PYTHON:-}" ]]; then
  if [[ -x "$_OI_PY" ]]; then
    export RL_PYTHON="$_OI_PY"
  elif [[ -x "$_SERVE_PY" ]]; then
    export RL_PYTHON="$_SERVE_PY"
    echo "WARN: $_OI_PY missing — using serving venv $RL_PYTHON"
  else
    export RL_PYTHON="$_OI_PY"
  fi
fi
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

export REPLICATE="${REPLICATE:-13}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-2}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-2}"
export MIN_TRAJS="${MIN_TRAJS:-80}"
export MAX_TRAJS="${MAX_TRAJS:-0}"
export PER_TASK="${PER_TASK:-3}"
export MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-8}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"
export RATCHET_ALLOW_TIE="${RATCHET_ALLOW_TIE:-1}"

# ── RL (the point of this sbatch) ──────────────────────────────────────────
export ENABLE_RL=1
export RL_N_TASKS="${RL_N_TASKS:-100}"
export RL_EPISODES="${RL_EPISODES:-512}"
# Proven 8×H200/A100 9B layout. Official Beaker job is 8 nodes, 8+48, SP=4.
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
# 1 when falling back to the serving venv so missing wheels can be pip'd.
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"
# Pass-through for screened-population evolve (empty = --fanout 1).
export EVOLVE_EXTRA_ARGS="${EVOLVE_EXTRA_ARGS:-}"

if [[ ! -x "$RL_PYTHON" ]]; then
  echo "ERROR: RL_PYTHON not executable: $RL_PYTHON" >&2
  echo "       The dedicated open-instruct venv has not been built on this checkout." >&2
  echo "       Build it once (15-40 min), then resubmit:" >&2
  echo "         bash $REPO/scripts/tmax/setup_rl_env.sh" >&2
  echo "       Expected interpreter: $_OI_PY" >&2
  exit 2
fi

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/tmax_coevolve" "$REPO/outputs/rl"

echo "===== plan ====="
echo "  REPLICATE          : $REPLICATE"
echo "  N_ITERS            : $N_ITERS"
echo "  ENABLE_RL          : $ENABLE_RL"
echo "  RL_PYTHON          : $RL_PYTHON"
echo "  RL_N_TASKS         : $RL_N_TASKS  (holdout-102 excluded)"
echo "  RL_EPISODES        : $RL_EPISODES"
echo "  RL layout          : learners=$RL_N_LEARNERS vllm=$RL_N_VLLM  resp=$RL_RESPONSE_LENGTH"
echo "  RL_WALL_TIMEOUT    : $RL_WALL_TIMEOUT (0 = no cap)"
echo "  META_MODEL         : $META_MODEL"
echo "  EVOLVE_EXTRA_ARGS  : ${EVOLVE_EXTRA_ARGS:-<none: --fanout 1>}"

"$PYTHON_BIN" - <<'PY' || echo "WARN: bedrock preflight failed"
import boto3, os
rt = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION_NAME", "us-west-2"))
print("bedrock-runtime OK", rt.meta.region_name)
PY

exec bash "$REPO/scripts/tmax/run_loop_tmax_coevolve.sh"
