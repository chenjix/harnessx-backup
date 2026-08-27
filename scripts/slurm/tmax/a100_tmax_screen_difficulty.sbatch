#!/bin/bash
# Stage 0 of the fan-out A/B: measure which of the 50 evolve tasks the BASE
# model actually fails under the BASELINE harness.
#
#   sbatch scripts/slurm/tmax/a100_tmax_screen_difficulty.sbatch
#
# Why this stage exists: the previous coevolve smoke picked its evolve set as
# "the first 4 tasks in the list", and the base model solved 4/4. A task set at
# ceiling has no capability gap, so the meta-agent has nothing to close — that
# run's harness came out byte-identical to its input across every round. Any
# evolve experiment needs tasks the model gets WRONG, and the only way to know
# which those are is to measure.
#
# The trajectories this produces are reused as --r0-dir by BOTH arms of the A/B,
# so R0 costs one rollout total instead of one per arm — and both arms start
# from an identical R0, removing it as a source of between-arm variance.
#
#SBATCH --job-name=tmax-screen
#SBATCH --account=interactive-ai-p4d
#SBATCH --partition=ml.p4d.24xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:a100:8
#SBATCH --cpus-per-task=32
#SBATCH --mem=240G
#SBATCH --time=03:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_screen_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_screen_%j.err

set -Eeuo pipefail
REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

echo "===== job ====="
echo "job   : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node  : $(hostname)"
echo "start : $(date -u '+%F %T UTC')"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.88}"
export POOL_BASE_PORT="${POOL_BASE_PORT:-8300}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-16}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export TMAX_MAX_STEPS="${TMAX_MAX_STEPS:-80}"

JOB_NAME="${JOB_NAME:-tmax-screen-evolve50}"
TASKS_JSON="${TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
ENVS_JSONL="${ENVS_JSONL:-$REPO/recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl}"
HARNESS="${HARNESS:-$REPO/configs/baseline_tmax_harness.yaml}"

echo "===== plan ====="
echo "  tasks   : $TASKS_JSON"
echo "  envs    : $ENVS_JSONL"
echo "  harness : $HARNESS"
echo "  job     : $JOB_NAME  (-> .benchmarks/tmax/$JOB_NAME)"
echo "  conc    : $TMAX_CONCURRENT"

docker info >/dev/null 2>&1 || { echo "ERROR: docker unavailable on $(hostname)" >&2; exit 2; }
# Size the check to THIS run's task count rather than a flat number: the repo's
# formula is ~8G for the shared python3/pip/pytest base layer plus ~0.12G per
# task image. A flat 20G rejects nodes that have ample room for a 50-task screen.
if [[ -z "${MIN_IMAGE_GB:-}" ]]; then
  _n_img="$("$PYTHON_BIN" "$REPO/scripts/tmax/count_task_images.py" \
              "$TASKS_JSON" 2>/dev/null || echo 50)"
  [[ "$_n_img" =~ ^[0-9]+$ ]] || _n_img=50
  MIN_IMAGE_GB=$(( 8 + (_n_img * 12 + 99) / 100 ))
  (( MIN_IMAGE_GB < 12 )) && MIN_IMAGE_GB=12
  echo "  image budget: ${_n_img} task images -> need ${MIN_IMAGE_GB}G"
fi
MIN_IMAGE_GB="$MIN_IMAGE_GB" bash "$REPO/scripts/tmax/docker_preflight.sh" \
  || { echo "ERROR: not enough image-store room" >&2; exit 2; }

mkdir -p /fsx/home/jixuan.chen/logs

# --resume lets a requeued job skip tasks that already have a result.json.
bash "$REPO/scripts/with_server_pool.sh" -- \
  "$PYTHON_BIN" -m recipe.tmax_eval.run_eval \
    --tasks-json "$TASKS_JSON" \
    --envs-jsonl "$ENVS_JSONL" \
    --job-name "$JOB_NAME" \
    --harness-config "$HARNESS" \
    --concurrent "$TMAX_CONCURRENT" \
    --max-steps "$TMAX_MAX_STEPS" \
    --resume \
  2>&1 | tee "/fsx/home/jixuan.chen/logs/tmax_screen_${SLURM_JOB_ID}_eval.log"

echo "===== difficulty summary ====="
"$PYTHON_BIN" "$REPO/scripts/tmax/pick_evolve_tasks.py" \
  --results-dir "$REPO/.benchmarks/tmax/$JOB_NAME" \
  --report-only

echo "done : $(date -u '+%F %T UTC')"
