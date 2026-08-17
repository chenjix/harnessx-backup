#!/bin/bash
#SBATCH --job-name=tmax-harness102
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=1-00:00:00
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_harness102_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_harness102_%j.err

# Holdout eval: base Qwen3.5-9B + harness BEFORE vs AFTER evolve, on the 102
# Tmax tasks (tasks_tmax_only200) that were held out of evolve-50.
#
#   before : R0  (baseline_tmax_harness.yaml + short default prompt)
#   after  : R4  (best evolve round on the 50-task set; same as R5 prompt)
#
# Evolve run: tmax-ev9b50-20260814-042904
# Agent path: full HarnessX (processors + tool_registry + system_prompt.txt)
# via recipe.tmax_eval.harness_runner when HARNESS_CONFIG is set.
#
#   sbatch /fsx/home/jixuan.chen/h200_eval_harness_before_after_102.sbatch
#
# Optional:
#   AFTER_ONLY=1            # skip before; only re-eval after (default 0)
#   AFTER_ROUND=R5          # which evolved round to treat as "after" (default R4)
#   SKIP_BEFORE=1           # alias of AFTER_ONLY=1
#   TMAX_CONCURRENT=2
#   RESUME=0                # wipe and redo (needed if prior after results are bad)
#   LIMIT=5                 # smoke
#   EVAL_SFT=1 LORA_PATH=...  # optional: score harness on an SFT adapter instead
#
# Only re-test after (keep R0 73/102):
#   sbatch --export=ALL,AFTER_ONLY=1,AFTER_ROUND=R5,RESUME=0,AFTER_JOB=tmax-harness-r5-holdout102-v2 \
#     /fsx/home/jixuan.chen/h200_eval_harness_before_after_102.sbatch

set -Eeuo pipefail

ROOT=/fsx/home/jixuan.chen/harnessx-backup
VENV="${VENV:-/fsx/home/jixuan.chen/.venv}"
EVOLVE_RUN="${EVOLVE_RUN:-$ROOT/recipe/tb2_evolver/runs/tmax-ev9b50-20260814-042904}"
AFTER_ROUND="${AFTER_ROUND:-R4}"
AFTER_ONLY="${AFTER_ONLY:-${SKIP_BEFORE:-0}}"

BEFORE_CFG="$EVOLVE_RUN/R0/config.yaml"
AFTER_CFG="$EVOLVE_RUN/${AFTER_ROUND}/config.yaml"

export MODEL_SIZE="${MODEL_SIZE:-9b}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export TB2_TEMPERATURE="${TB2_TEMPERATURE:-0}"
export TMAX_MAX_STEPS="${TMAX_MAX_STEPS:-80}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-2}"
export RESUME="${RESUME:-1}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export PYTHON_BIN="${PYTHON_BIN:-$VENV/bin/python}"
export SFT_PYTHON="${SFT_PYTHON:-$VENV/bin/python}"
export EVAL_SFT="${EVAL_SFT:-0}"

# Fixed 102-task holdout
export TASKS_JSON="$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json"
export ENVS_JSONL="$ROOT/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl"

BEFORE_JOB="${BEFORE_JOB:-tmax-harness-r0-holdout102}"
AFTER_JOB="${AFTER_JOB:-tmax-harness-${AFTER_ROUND,,}-holdout102}"

echo "===== job ====="
echo "job     : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node    : $(hostname)"
echo "gpus    : ${CUDA_VISIBLE_DEVICES:-unset}"
echo "start   : $(date -u '+%F %T UTC')"
nvidia-smi -L || true

[[ -x "$PYTHON_BIN" ]] || { echo "ERROR: missing $PYTHON_BIN" >&2; exit 2; }
if [[ "$AFTER_ONLY" != "1" ]]; then
  [[ -f "$BEFORE_CFG" ]] || { echo "ERROR: missing before harness $BEFORE_CFG" >&2; exit 2; }
  [[ -f "$EVOLVE_RUN/R0/system_prompt.txt" ]] || { echo "ERROR: missing R0 system_prompt.txt" >&2; exit 2; }
fi
[[ -f "$AFTER_CFG" ]] || { echo "ERROR: missing after harness $AFTER_CFG" >&2; exit 2; }
[[ -f "$EVOLVE_RUN/${AFTER_ROUND}/system_prompt.txt" ]] || {
  echo "ERROR: missing ${AFTER_ROUND}/system_prompt.txt" >&2; exit 2
}
[[ -f "$TASKS_JSON" && -f "$ENVS_JSONL" ]] || { echo "ERROR: missing tasks/envs" >&2; exit 2; }
command -v docker >/dev/null || { echo "ERROR: docker not in PATH" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "ERROR: docker daemon not usable" >&2; exit 2; }

if [[ "$EVAL_SFT" == "1" ]]; then
  export LORA_PATH="${LORA_PATH:?EVAL_SFT=1 requires LORA_PATH}"
  export LORA_NAME="${LORA_NAME:-qwen35-9b-tmax-sft}"
  [[ -f "$LORA_PATH/adapter_model.safetensors" ]] || {
    echo "ERROR: missing adapter $LORA_PATH/adapter_model.safetensors" >&2; exit 2
  }
fi

cd "$ROOT"
mkdir -p /fsx/home/jixuan.chen/logs "$ROOT/.benchmarks/tmax"

if [[ "$CLEAN_STALE" == "1" ]]; then
  if pgrep -f 'vllm.entrypoints.openai.api_server' >/dev/null 2>&1; then
    echo "[$(date)] CLEAN_STALE: killing leftover vLLM"
    pkill -f 'vllm.entrypoints.openai.api_server' || true
    sleep 8
  fi
fi

echo "===== plan ====="
echo "  tasks       : $TASKS_JSON (102 holdout)"
echo "  after_only  : $AFTER_ONLY"
echo "  before      : $BEFORE_CFG"
echo "  after       : $AFTER_CFG  (AFTER_ROUND=$AFTER_ROUND)"
echo "  before_job  : $BEFORE_JOB"
echo "  after_job   : $AFTER_JOB"
echo "  gpu_pool    : $GPU_POOL  concurrent=$TMAX_CONCURRENT  resume=$RESUME"
echo "  eval_sft    : $EVAL_SFT  lora=${LORA_PATH:-none}"
[[ -n "${LIMIT:-}" ]] && echo "  LIMIT       : $LIMIT"
if [[ "$AFTER_ONLY" != "1" ]]; then
  echo "  prompt R0   : $(wc -c < "$EVOLVE_RUN/R0/system_prompt.txt") bytes"
fi
echo "  prompt $AFTER_ROUND   : $(wc -c < "$EVOLVE_RUN/${AFTER_ROUND}/system_prompt.txt") bytes"

run_arm() {
  local label="$1" cfg="$2" job="$3"
  echo
  echo "===== $label ====="
  echo "  HARNESS_CONFIG=$cfg"
  echo "  JOB_NAME=$job"
  env HARNESS_CONFIG="$cfg" JOB_NAME="$job" \
    bash "$ROOT/scripts/evaluate_tmax.sh"
}

# Same vLLM pool can be reused across arms by keeping servers up: evaluate_tmax
# starts a fresh pool each call via with_server_pool.sh. That is fine but slow;
# CLEAN_STALE only at start. Second arm restarts pool — acceptable for clarity.
if [[ "$AFTER_ONLY" != "1" ]]; then
  run_arm "1/2 BEFORE (R0 baseline harness)" "$BEFORE_CFG" "$BEFORE_JOB"
  run_arm "2/2 AFTER (${AFTER_ROUND} evolved harness)" "$AFTER_CFG" "$AFTER_JOB"
else
  run_arm "AFTER ONLY (${AFTER_ROUND} evolved harness)" "$AFTER_CFG" "$AFTER_JOB"
fi

echo
echo "===== holdout-102 harness compare ====="
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path
root = Path("$ROOT") / ".benchmarks" / "tmax"
after_only = "$AFTER_ONLY" == "1"
pairs = []
if not after_only:
    pairs.append(("before/R0", "$BEFORE_JOB"))
pairs.append(("after/$AFTER_ROUND", "$AFTER_JOB"))
rows = []
for label, job in pairs:
    p = root / job / "summary.json"
    s = json.loads(p.read_text())
    rows.append((label, s["n_passed"], s["n_tasks"], s["pass_rate"], p))
    print(f"{label:16s}  {s['n_passed']}/{s['n_tasks']}  ({s['pass_rate']:.3f})")
    print(f"  summary: {p}")
if after_only:
    before = root / "$BEFORE_JOB" / "summary.json"
    if before.is_file():
        b = json.loads(before.read_text())
        a = rows[0]
        print(f"before/R0 (cached) {b['n_passed']}/{b['n_tasks']}  ({b['pass_rate']:.3f})")
        print(f"delta vs cached before: {a[1] - b['n_passed']:+d} tasks  ({a[3] - b['pass_rate']:+.3f})")
elif len(rows) == 2:
    b, a = rows[0], rows[1]
    print(f"delta: {a[1] - b[1]:+d} tasks  ({a[3] - b[3]:+.3f})")
PY

echo "[$(date -u '+%F %T UTC')] done"
