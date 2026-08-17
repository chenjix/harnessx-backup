#!/bin/bash
#SBATCH --job-name=tmax-sft500
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=2-00:00:00
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_sft500_%j.out

# Sample-size ablation (N=500), train-on-test eval still on the fixed 102 tasks.
#
# Pipeline on one 8×H200 node:
#   1) Build SFT data: 500 Tmax only-success traj restricted to the 102-task
#      allowlist (tasks_tmax_only200.json). Cap on that allowlist ≈ 618.
#   2) LoRA SFT on Qwen3.5-9B (aligned recipe).
#   3) Eval base vs this adapter on the same 102 tasks
#      (reuses prior base results when RESUME=1).
#
#   sbatch h200_sft_tmax500_eval.sbatch
#
# Optional overrides:
#   TMAX_N=500
#   TMAX_TASK_ALLOWLIST=none   # full Tmax pool (needed for true N=1000); eval still 102
#   TMAX_PER_TASK=8
#   SFT_DATASET_NAME=...
#   SFT_OUTPUT_DIR=...
#   ALLOW_OVERWRITE=1          # retrain even if adapter exists
#   SKIP_BUILD=1 / SKIP_TRAIN=1 / SKIP_EVAL=1
#   LIMIT=5                    # smoke eval
#
# True N=1000 (mix tasks outside the 102, eval still on 102):
#   sbatch --export=ALL,TMAX_N=1000,TMAX_TASK_ALLOWLIST=none \
#     /fsx/home/jixuan.chen/h200_sft_tmax500_eval.sbatch

set -euo pipefail

ROOT=/fsx/home/jixuan.chen/harnessx-backup
VENV="${VENV:-/fsx/home/jixuan.chen/.venv}"
PY="${SFT_PYTHON:-$VENV/bin/python}"

TMAX_N="${TMAX_N:-500}"
TMAX_SEED="${TMAX_SEED:-42}"
TMAX_PER_TASK="${TMAX_PER_TASK:-8}"
_ALLOW_RAW="${TMAX_TASK_ALLOWLIST:-$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json}"
case "${_ALLOW_RAW,,}" in
  none|off|0|false|no|"") USE_ALLOWLIST=0; TMAX_TASK_ALLOWLIST="" ;;
  *) USE_ALLOWLIST=1; TMAX_TASK_ALLOWLIST="$_ALLOW_RAW" ;;
esac
if [[ -z "${SFT_DATASET_NAME:-}" ]]; then
  if [[ "$USE_ALLOWLIST" == "1" ]]; then
    SFT_DATASET_NAME="qwen35_9b_tmax_only${TMAX_N}_on102"
  else
    SFT_DATASET_NAME="qwen35_9b_tmax_only${TMAX_N}"
  fi
fi
DATA_DIR="$ROOT/recipe/tb2_sft/data/$SFT_DATASET_NAME"
OUT_DIR="${SFT_OUTPUT_DIR:-$ROOT/outputs/sft/${SFT_DATASET_NAME}_v2}"

export MODEL_SIZE="${MODEL_SIZE:-9b}"
export SFT_DATASET_NAME
export SFT_OUTPUT_DIR="$OUT_DIR"
export SFT_GPUS="${SFT_GPUS:-0,1,2,3,4,5,6,7}"
export SFT_PYTHON="$PY"
export SFT_PYTHON

# Same recipe as tmax_only200_v2 (chat-template + completion_only_loss).
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export SFT_LEARNING_RATE="${SFT_LEARNING_RATE:-2.0e-5}"
export SFT_LR_SCHEDULER="${SFT_LR_SCHEDULER:-linear}"
export SFT_MAX_SEQ_LENGTH="${SFT_MAX_SEQ_LENGTH:-16384}"
export SFT_EFFECTIVE_BATCH="${SFT_EFFECTIVE_BATCH:-32}"
export SFT_ATTN_IMPLEMENTATION="${SFT_ATTN_IMPLEMENTATION:-flash_attention_2}"

# Eval on the fixed 102-task set (NOT regenerated from the 500-traj corpus).
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export TB2_TEMPERATURE="${TB2_TEMPERATURE:-0}"
export TMAX_MAX_STEPS="${TMAX_MAX_STEPS:-80}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-2}"
export RESUME="${RESUME:-1}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export PYTHON_BIN="${PYTHON_BIN:-$PY}"
export TASKS_JSON="${TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json}"
export ENVS_JSONL="${ENVS_JSONL:-$ROOT/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl}"
export BASE_JOB="${BASE_JOB:-tmax-base9b-full}"
if [[ -z "${SFT_JOB:-}" ]]; then
  if [[ "$USE_ALLOWLIST" == "1" ]]; then
    SFT_JOB="tmax-sft${TMAX_N}-on102-full"
  else
    SFT_JOB="tmax-sft${TMAX_N}-full"
  fi
fi
export SFT_JOB
export LORA_PATH="$OUT_DIR"
if [[ -z "${LORA_NAME:-}" ]]; then
  if [[ "$USE_ALLOWLIST" == "1" ]]; then
    LORA_NAME="qwen35-9b-tmax${TMAX_N}-on102"
  else
    LORA_NAME="qwen35-9b-tmax${TMAX_N}"
  fi
fi
export LORA_NAME

SKIP_BUILD="${SKIP_BUILD:-0}"
SKIP_TRAIN="${SKIP_TRAIN:-0}"
SKIP_EVAL="${SKIP_EVAL:-0}"

echo "[$(date)] job $SLURM_JOB_ID started on $(hostname)"
echo "[$(date)] attach: srun --jobid=$SLURM_JOB_ID --overlap --pty bash"
nvidia-smi -L || true

[[ -x "$PY" ]] || { echo "ERROR: missing $PY" >&2; exit 2; }
if [[ "$USE_ALLOWLIST" == "1" ]]; then
  [[ -f "$TMAX_TASK_ALLOWLIST" ]] || { echo "ERROR: allowlist missing: $TMAX_TASK_ALLOWLIST" >&2; exit 2; }
fi
[[ -f "$TASKS_JSON" && -f "$ENVS_JSONL" ]] || {
  echo "ERROR: eval task set missing (TASKS_JSON / ENVS_JSONL)" >&2
  exit 2
}
command -v docker >/dev/null || { echo "ERROR: docker not in PATH" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "ERROR: docker daemon not usable" >&2; exit 2; }

cd "$ROOT"
mkdir -p /fsx/home/jixuan.chen/logs "$OUT_DIR" "$ROOT/.benchmarks/tmax"

echo "[$(date)] plan"
echo "  TMAX_N       : $TMAX_N  per_task=$TMAX_PER_TASK  seed=$TMAX_SEED"
if [[ "$USE_ALLOWLIST" == "1" ]]; then
  echo "  allowlist    : $TMAX_TASK_ALLOWLIST"
else
  echo "  allowlist    : OFF (full Tmax pool; eval still 102)"
fi
echo "  dataset      : $SFT_DATASET_NAME  -> $DATA_DIR"
echo "  adapter out  : $OUT_DIR"
echo "  eval tasks   : $TASKS_JSON"
echo "  eval envs    : $ENVS_JSONL"
echo "  base_job     : $BASE_JOB"
echo "  sft_job      : $SFT_JOB"
echo "  skip         : build=$SKIP_BUILD train=$SKIP_TRAIN eval=$SKIP_EVAL"

# ── 1) Build conversational SFT data on the 102-task allowlist ───────────────
if [[ "$SKIP_BUILD" != "1" ]]; then
  echo
  if [[ "$USE_ALLOWLIST" == "1" ]]; then
    echo "[$(date)] ===== 1/3 build SFT data (N=$TMAX_N, 102-task allowlist) ====="
  else
    echo "[$(date)] ===== 1/3 build SFT data (N=$TMAX_N, full Tmax pool) ====="
  fi
  if [[ "$USE_ALLOWLIST" == "1" ]]; then
    _BUILD_ALLOW="$TMAX_TASK_ALLOWLIST"
  else
    _BUILD_ALLOW=none
  fi
  TMAX_N="$TMAX_N" TMAX_SEED="$TMAX_SEED" TMAX_PER_TASK="$TMAX_PER_TASK" \
    TMAX_TASK_ALLOWLIST="$_BUILD_ALLOW" \
    MODEL_SIZE="$MODEL_SIZE" SFT_DATASET_NAME="$SFT_DATASET_NAME" \
    SFT_PYTHON="$PY" \
    bash "$ROOT/scripts/build_tmax_allowlist_sft_data.sh"
else
  echo "[$(date)] SKIP_BUILD=1"
fi

[[ -f "$DATA_DIR/train.jsonl" && -f "$DATA_DIR/eval.jsonl" ]] || {
  echo "ERROR: dataset missing under $DATA_DIR" >&2
  exit 2
}
"$PY" - "$DATA_DIR/train.jsonl" <<'PY'
import json, sys
from pathlib import Path
row = json.loads(Path(sys.argv[1]).open().readline())
assert isinstance(row.get("prompt"), list) and isinstance(row.get("completion"), list)
print("data schema OK")
PY

# ── 2) Train LoRA ────────────────────────────────────────────────────────────
if [[ "$SKIP_TRAIN" != "1" ]]; then
  echo
  echo "[$(date)] ===== 2/3 train LoRA SFT ====="
  if [[ -f "$OUT_DIR/adapter_model.safetensors" && -z "${SFT_RESUME:-}" && "${ALLOW_OVERWRITE:-0}" != "1" ]]; then
    echo "ERROR: adapter already exists at $OUT_DIR" >&2
    echo "       set ALLOW_OVERWRITE=1 or SKIP_TRAIN=1" >&2
    exit 2
  fi
  bash "$ROOT/scripts/train_sft.sh"
else
  echo "[$(date)] SKIP_TRAIN=1"
fi

[[ -f "$OUT_DIR/adapter_model.safetensors" ]] || {
  echo "ERROR: training finished without adapter: $OUT_DIR/adapter_model.safetensors" >&2
  exit 2
}
echo "[$(date)] adapter ready: $OUT_DIR/adapter_model.safetensors"
ls -lh "$OUT_DIR/adapter_model.safetensors"

# ── 3) Eval base vs SFT on the fixed 102 tasks ───────────────────────────────
if [[ "$SKIP_EVAL" != "1" ]]; then
  echo
  echo "[$(date)] ===== 3/3 eval base vs SFT on 102 tasks ====="
  if [[ "$CLEAN_STALE" == "1" ]]; then
    if pgrep -f 'vllm.entrypoints.openai.api_server' >/dev/null 2>&1; then
      echo "[$(date)] CLEAN_STALE: killing leftover vLLM api_server processes"
      pkill -f 'vllm.entrypoints.openai.api_server' || true
      sleep 8
    fi
  fi
  export LORA_PATH LORA_NAME BASE_JOB SFT_JOB TASKS_JSON ENVS_JSONL
  bash "$ROOT/scripts/compare_tmax_base_sft.sh"
else
  echo "[$(date)] SKIP_EVAL=1"
fi

echo
echo "[$(date)] pipeline done"
echo "  dataset   : $DATA_DIR"
echo "  adapter   : $OUT_DIR/adapter_model.safetensors"
echo "  base sum  : $ROOT/.benchmarks/tmax/$BASE_JOB/summary.json"
echo "  sft  sum  : $ROOT/.benchmarks/tmax/$SFT_JOB/summary.json"
echo "  slurm log : /fsx/home/jixuan.chen/logs/tmax_sft500_${SLURM_JOB_ID}.out"
