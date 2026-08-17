#!/bin/bash
#SBATCH --job-name=tmax-ev9b50
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=2-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_evolve9b50_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_evolve9b50_%j.err

# Harness evolve on 50 Tmax tasks (held-out: the 102 tmax_only200 eval tasks).
#   base agent : Qwen3.5-9B (8× vLLM)
#   meta agent : Bedrock Claude Opus 4.8
#   rounds     : 5 (R0 rollout + 5 evolve steps → R1..R5)
#
#   # cancel old low-pri pending job first if still PD:
#   scancel 1530
#   sbatch /fsx/home/jixuan.chen/h200_evolve_9b_tmax50.sbatch
#
# Optional:
#   EVOLVE_ROUNDS=5 TMAX_CONCURRENT=8 RESUME=0
#
# Rough ETA: ~10–16h (6× ~50-task rollouts @ ~1.5–2.5h each + meta Bedrock).

set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

echo "===== job ====="
echo "job     : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node    : $(hostname)"
echo "gpus    : ${CUDA_VISIBLE_DEVICES:-unset}"
echo "cpus    : $(nproc)"
echo "start   : $(date -u '+%F %T UTC')"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
free_gb() { df -BG --output=avail "$1" | tail -1 | tr -dc '0-9'; }
echo "docker root : $DOCKER_ROOT"
df -h "$DOCKER_ROOT" | tail -1
if (( $(free_gb "$DOCKER_ROOT") < 40 )); then
  echo "below floor — reclaiming docker disk"
  docker container prune -f >/dev/null 2>&1 || true
  docker image prune -af >/dev/null 2>&1 || true
  docker builder prune -af >/dev/null 2>&1 || true
fi

export PYTHON_BIN=/fsx/home/jixuan.chen/.venv/bin/python
export SFT_PYTHON="${SFT_PYTHON:-$PYTHON_BIN}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"

# Models
export MODEL_SIZE=9b
export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"

# Evolve scope: 50 Tmax tasks, disjoint from 102 holdout
export TASKS_JSON="$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json"
export TMAX_ENVS_JSONL="$REPO/recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}"
export NUM_ROUNDS="$EVOLVE_ROUNDS"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
# Total concurrent docker+agent workers (fan across pool endpoints inside run_eval).
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
export TB2_CONCURRENT="$TMAX_CONCURRENT"
export TMAX_MAX_STEPS="${TMAX_MAX_STEPS:-80}"
export TB2_TEMPERATURE="${TB2_TEMPERATURE:-0}"
export REGRESSION_TOLERANCE="${REGRESSION_TOLERANCE:-0.04}"
export REQUIRE_EVIDENCE="${REQUIRE_EVIDENCE:-0}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export RESUME="${RESUME:-auto}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"

# Unique run tag unless overridden
export RUN_TAG="${RUN_TAG:-tmax-ev9b50-$(date +%Y%m%d-%H%M%S)}"

[[ -f "$TASKS_JSON" ]] || { echo "ERROR: missing $TASKS_JSON" >&2; exit 2; }
[[ -f "$TMAX_ENVS_JSONL" ]] || { echo "ERROR: missing $TMAX_ENVS_JSONL" >&2; exit 2; }
[[ -x "$PYTHON_BIN" ]] || { echo "ERROR: missing $PYTHON_BIN" >&2; exit 2; }
command -v docker >/dev/null || { echo "ERROR: docker not in PATH" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "ERROR: docker daemon not usable" >&2; exit 2; }

if [[ "$CLEAN_STALE" == "1" ]]; then
  if pgrep -f 'vllm.entrypoints.openai.api_server' >/dev/null 2>&1; then
    echo "[$(date)] CLEAN_STALE: killing leftover vLLM"
    pkill -f 'vllm.entrypoints.openai.api_server' || true
    sleep 8
  fi
fi

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/recipe/tb2_evolver/runs"

echo "===== plan ====="
echo "  MODEL_SIZE     : $MODEL_SIZE"
echo "  META_MODEL     : $META_MODEL"
echo "  EVOLVE_ROUNDS  : $EVOLVE_ROUNDS"
echo "  TASKS_JSON     : $TASKS_JSON  (50, held-out=102)"
echo "  TMAX_ENVS      : $TMAX_ENVS_JSONL"
echo "  GPU_POOL       : $GPU_POOL"
echo "  TMAX_CONCURRENT: $TMAX_CONCURRENT"
echo "  RUN_TAG        : $RUN_TAG"

# Soft AWS check (Bedrock SigV4)
"$PYTHON_BIN" - <<'PY' || echo "WARN: boto3/bedrock preflight failed — evolve may fail at meta step"
import boto3, os
rt = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION_NAME", "us-west-2"))
print("bedrock-runtime client OK", rt.meta.region_name)
PY

exec bash "$REPO/scripts/evolve_tmax.sh"
