#!/bin/bash
# Fan-out A/B: does generating N harness candidates and screening them down to k
# beat the (1+1) hill-climb, on the same tasks, model, rounds and hardware?
#
#   # 1. screen first (writes the task sets):
#   sbatch scripts/slurm/tmax/a100_tmax_screen_difficulty.sbatch
#   python scripts/tmax/pick_evolve_tasks.py \
#       --results-dir .benchmarks/tmax/tmax-screen-evolve50 \
#       --n-failed 10 --n-passed 6 --holdout-size 30 \
#       --make-r0-dir .benchmarks/tmax/ab-r0
#
#   # 2. then run both arms:
#   sbatch scripts/slurm/tmax/a100_tmax_fanout_ab.sbatch
#
#   # one arm only (e.g. to re-run B after a fix):
#   sbatch --export=ALL,ARMS=B scripts/slurm/tmax/a100_tmax_fanout_ab.sbatch
#
# Both arms run in ONE job, sequentially, on the SAME node. That is deliberate:
# harness pass rates on a 15-task set carry sd ~3 tasks, so any confound worth
# less than that is worth removing for free. Same node = same GPUs, same docker
# image cache, same vLLM build. Both arms also share --r0-dir, so they start
# from a byte-identical R0 rollout rather than two independent draws.
#
# ARM A (control)   : --fanout 1                  -- the legacy path, unchanged
# ARM B (treatment) : --fanout 8 --fanout-keep 2  -- propose 8, screen to 2
#
#SBATCH --job-name=tmax-fanout-ab
#SBATCH --account=interactive-ai-p4d
#SBATCH --partition=ml.p4d.24xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:a100:8
#SBATCH --cpus-per-task=32
#SBATCH --mem=240G
#SBATCH --time=12:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_fanout_ab_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_fanout_ab_%j.err

set -Eeuo pipefail
REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

echo "===== job ====="
echo "job   : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node  : $(hostname)"
echo "start : $(date -u '+%F %T UTC')"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

# ── shared config (identical for both arms) ─────────────────────────────────
export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export META_MODEL="${META_MODEL:-openai/gpt-5.5}"
export PROVIDER_ID="${PROVIDER_ID:-openai}"
export GATEWAY_URL="${GATEWAY_URL:-https://gateway.salesforceresearch.ai}"
export SFG_API_KEY="${SFG_API_KEY:-${OPENAI_API_KEY:-}}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-$SFG_API_KEY}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.88}"
export POOL_BASE_PORT="${POOL_BASE_PORT:-8300}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-16}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export TMAX_MAX_STEPS="${TMAX_MAX_STEPS:-80}"
export REQUIRE_EVIDENCE="${REQUIRE_EVIDENCE:-0}"
export RESUME="${RESUME:-auto}"

EXP_TAG="${EXP_TAG:-fanout-ab}"
ROUNDS="${EVOLVE_ROUNDS:-5}"
TAG="${TASK_TAG:-ab}"
SCREEN_JOB="${SCREEN_JOB:-tmax-screen-evolve50}"

EVOLVE_TASKS="${EVOLVE_TASKS:-}"
if [[ -z "$EVOLVE_TASKS" ]]; then
  # The picker names the file after the set size, which is not known here.
  EVOLVE_TASKS="$(ls -1 "$REPO"/recipe/tb2_evolver/tasks_${TAG}_evolve*_list.json 2>/dev/null | head -1 || true)"
fi
export TMAX_ENVS_JSONL="${TMAX_ENVS_JSONL:-$REPO/recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl}"

# Reuse the screening rollout as R0 so both arms skip it AND start identical.
# This must be the SUBSET dir pick_evolve_tasks.py --make-r0-dir writes, not the
# raw screening dir: read_round_score reads pass_rate straight out of
# summary.json, so the raw dir would score R0 over all 50 screened tasks while
# R1+ score over the evolve set — the gate would silently compare two different
# denominators and its accept/revert decisions would be meaningless.
export R0_DIR="${R0_DIR:-$REPO/.benchmarks/tmax/${TAG}-r0}"

ARMS="${ARMS:-AB}"

# ── preflight: fail before burning an allocation ────────────────────────────
if [[ -z "$EVOLVE_TASKS" || ! -f "$EVOLVE_TASKS" ]]; then
  echo "ERROR: no evolve task set found (looked for tasks_${TAG}_evolve*_list.json)." >&2
  echo "       Run the screening job, then scripts/tmax/pick_evolve_tasks.py." >&2
  exit 2
fi
[[ -f "$TMAX_ENVS_JSONL" ]] || { echo "ERROR: envs jsonl missing: $TMAX_ENVS_JSONL" >&2; exit 2; }
if [[ ! -d "$R0_DIR" ]]; then
  echo "ERROR: R0 dir missing: $R0_DIR" >&2
  echo "       Run the screening job, then pick_evolve_tasks.py --make-r0-dir $R0_DIR" >&2
  exit 2
fi
# The R0 summary must cover exactly the evolve set, or the gate compares
# mismatched denominators from R1 onward.
"$PYTHON_BIN" - <<PY || exit 2
import json, sys, pathlib
s = json.loads(pathlib.Path("$R0_DIR/summary.json").read_text())
tasks = json.loads(pathlib.Path("$EVOLVE_TASKS").read_text())
if int(s.get("n_tasks", -1)) != len(tasks):
    print(f"ERROR: R0 summary covers {s.get('n_tasks')} tasks but the evolve set "
          f"has {len(tasks)}. Rebuild it with pick_evolve_tasks.py --make-r0-dir.",
          file=sys.stderr)
    sys.exit(2)
print(f"  R0 check    : {s['n_passed']}/{s['n_tasks']} pass_rate={s['pass_rate']:.4f}")
PY
[[ -n "$SFG_API_KEY" ]] || { echo "ERROR: META_MODEL=$META_MODEL needs SFG_API_KEY" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "ERROR: docker unavailable on $(hostname)" >&2; exit 2; }
# Size the check to THIS run's task count rather than a flat number: the repo's
# formula is ~8G for the shared python3/pip/pytest base layer plus ~0.12G per
# task image. A flat 20G rejects nodes that have ample room for a 50-task screen.
if [[ -z "${MIN_IMAGE_GB:-}" ]]; then
  _n_img="$("$PYTHON_BIN" "$REPO/scripts/tmax/count_task_images.py" \
              "$EVOLVE_TASKS" 2>/dev/null || echo 50)"
  [[ "$_n_img" =~ ^[0-9]+$ ]] || _n_img=50
  MIN_IMAGE_GB=$(( 8 + (_n_img * 12 + 99) / 100 ))
  (( MIN_IMAGE_GB < 12 )) && MIN_IMAGE_GB=12
  echo "  image budget: ${_n_img} task images -> need ${MIN_IMAGE_GB}G"
fi
MIN_IMAGE_GB="$MIN_IMAGE_GB" bash "$REPO/scripts/tmax/docker_preflight.sh" \
  || { echo "ERROR: not enough image-store room" >&2; exit 2; }

N_TASKS="$("$PYTHON_BIN" -c "import json;print(len(json.load(open('$EVOLVE_TASKS'))))")"

echo "===== plan ====="
echo "  arms        : $ARMS"
echo "  evolve set  : $EVOLVE_TASKS  ($N_TASKS tasks)"
echo "  R0 (shared) : $R0_DIR"
echo "  rounds      : $ROUNDS"
echo "  meta        : $META_MODEL"
echo "  concurrency : $TMAX_CONCURRENT"
"$PYTHON_BIN" - <<PY
import json, pathlib
p = pathlib.Path("$REPO/recipe/tb2_evolver/tasks_${TAG}_evolve_baseline.json")
if p.is_file():
    d = json.loads(p.read_text())
    nf = sum(1 for v in d.values() if not v)
    print(f"  baseline    : {len(d)-nf}/{len(d)} solved by the base model "
          f"-> {nf} tasks are the gap this experiment tries to close")
else:
    print("  baseline    : (no sidecar; run pick_evolve_tasks.py to record it)")
PY

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/$EXP_TAG"

run_arm() {
  local arm="$1" extra="$2"
  local tag="${EXP_TAG}-${arm}"
  echo
  echo "════════════════════════════════════════════════════════════"
  echo "  ARM $arm   $(date -u '+%F %T UTC')"
  echo "  run tag : $tag"
  echo "  extra   : $extra"
  echo "════════════════════════════════════════════════════════════"
  EVOLVE_EXTRA_ARGS="$extra" \
  RUN_TAG="$tag" \
  TASKS_JSON="$EVOLVE_TASKS" \
  NUM_ROUNDS="$ROUNDS" \
    bash "$REPO/scripts/tmax/evolve_tmax.sh" \
    2>&1 | tee "/fsx/home/jixuan.chen/logs/${EXP_TAG}_${arm}_${SLURM_JOB_ID}.log"
  echo "  ARM $arm done $(date -u '+%F %T UTC')"
}

if [[ "$ARMS" == *A* ]]; then
  run_arm A "--fanout 1"
fi
if [[ "$ARMS" == *B* ]]; then
  run_arm B "--fanout ${FANOUT:-8} --fanout-keep ${FANOUT_KEEP:-2} --fanout-concurrent ${FANOUT_CONCURRENT:-4} --explore-every ${EXPLORE_EVERY:-3}"
fi

echo
echo "===== comparison ====="
"$PYTHON_BIN" "$REPO/scripts/tmax/compare_fanout_ab.py" \
  --exp-tag "$EXP_TAG" --arms "$ARMS" \
  --baseline "$REPO/recipe/tb2_evolver/tasks_${TAG}_evolve_baseline.json" \
  || echo "(comparison script failed; per-arm state is under recipe/tb2_evolver/runs/${EXP_TAG}-*)"

echo "done : $(date -u '+%F %T UTC')"
