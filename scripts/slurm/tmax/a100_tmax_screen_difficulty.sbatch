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
# SHARED_BASE=1 is REQUIRED here, not an optimisation. The ~0.12G-per-image
# figure the budget below uses is the marginal cost of a task image ON TOP of a
# shared python3/pip/pytest base layer. With SHARED_BASE=0 each image carries
# its own full toolchain (cargo, python3-dev, ...) and 50 of them need ~40G+,
# not 14G. Job 33602 ran with the formula but without the sharing it assumes,
# and filled a 97G root volume to 0 bytes — every remaining task then failed in
# ~0.5s with "docker build failed", which read_per_task_results reports as an
# ordinary task failure.
export SHARED_BASE="${SHARED_BASE:-1}"
if [[ "$SHARED_BASE" == "1" ]]; then
  export TMAX_SHARED_BASE_TAG="${TMAX_SHARED_BASE_TAG:-hx-tmax-base:1}"
fi

# Size the check to THIS run's task count. The per-image term only holds with
# SHARED_BASE=1; without it, budget ~0.8G per image instead.
if [[ -z "${MIN_IMAGE_GB:-}" ]]; then
  _n_img="$("$PYTHON_BIN" "$REPO/scripts/tmax/count_task_images.py" \
              "$TASKS_JSON" 2>/dev/null || echo 50)"
  [[ "$_n_img" =~ ^[0-9]+$ ]] || _n_img=50
  if [[ "$SHARED_BASE" == "1" ]]; then
    MIN_IMAGE_GB=$(( 8 + (_n_img * 12 + 99) / 100 ))
  else
    MIN_IMAGE_GB=$(( 8 + (_n_img * 80 + 99) / 100 ))
  fi
  (( MIN_IMAGE_GB < 12 )) && MIN_IMAGE_GB=12
  echo "  image budget: ${_n_img} task images, SHARED_BASE=$SHARED_BASE -> need ${MIN_IMAGE_GB}G"
fi
MIN_IMAGE_GB="$MIN_IMAGE_GB" bash "$REPO/scripts/tmax/docker_preflight.sh" \
  || { echo "ERROR: not enough image-store room" >&2; exit 2; }

mkdir -p /fsx/home/jixuan.chen/logs

# ── image janitor ───────────────────────────────────────────────────────────
# run_eval builds one image per task and never removes any, so peak disk grows
# with the task count. On this cluster that is fatal rather than merely wasteful:
# the effective image store is /var/lib/containerd on a 97G root volume that
# already sits at ~90% for unrelated reasons, leaving ~10G — about 20 task
# images at the ~0.5G each these tasks actually cost.
#
# Job 33602 died exactly here: it exhausted the volume ~8 minutes in, and the
# remaining 34 tasks failed with "docker build failed" while reporting as
# ordinary task results.
#
# `docker image prune -a` only removes images no container references, and
# run_eval removes each task's container when the task ends (unless
# --keep-container). So a periodic prune reclaims finished tasks' images and
# cannot touch one that is still in use. This bounds the footprint instead of
# letting it accumulate.
IMAGE_JANITOR="${IMAGE_JANITOR:-1}"
JANITOR_INTERVAL_S="${JANITOR_INTERVAL_S:-120}"
_janitor_pid=""
if [[ "$IMAGE_JANITOR" == "1" ]]; then
  (
    while true; do
      sleep "$JANITOR_INTERVAL_S"
      free_gb="$(df -BG --output=avail /var/lib/containerd 2>/dev/null | tail -1 | tr -dc '0-9')"
      [[ -z "$free_gb" ]] && continue
      if (( free_gb < ${JANITOR_FLOOR_GB:-25} )); then
        echo "[janitor] ${free_gb}G free — pruning unreferenced images/build cache"
        docker image prune -af >/dev/null 2>&1 || true
        docker builder prune -af >/dev/null 2>&1 || true
        echo "[janitor] now $(df -BG --output=avail /var/lib/containerd 2>/dev/null | tail -1 | tr -dc '0-9')G free"
      fi
    done
  ) &
  _janitor_pid=$!
  echo "image janitor: on (pid $_janitor_pid, every ${JANITOR_INTERVAL_S}s, floor ${JANITOR_FLOOR_GB:-25}G)"
  trap '[[ -n "$_janitor_pid" ]] && kill "$_janitor_pid" 2>/dev/null || true' EXIT
fi

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
