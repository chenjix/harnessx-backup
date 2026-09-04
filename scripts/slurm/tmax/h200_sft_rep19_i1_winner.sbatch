#!/bin/bash
# LoRA SFT on 9B *base* using rep19 iter1 winner-harness trajectories,
# filtered with the new corpus recipe. Then holdout-102 under that same harness.
#
# Does not touch the running coevolve job 3139 (rep22). This job will queue if
# no p5en node is free.
#
#   unset INIT_LORA_PATH
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_sft_rep19_i1_winner.sbatch
#
# Optional:
#   SKIP_BUILD=1 SKIP_TRAIN=0 SKIP_EVAL=0
#   ALLOW_OVERWRITE=1
#   SFT_EPOCHS=2
#
#SBATCH --job-name=tmax-sft-i1w
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=1-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_sft_i1w_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_sft_i1w_%j.err

set -Eeuo pipefail

ROOT=/fsx/home/jixuan.chen/harnessx-backup
VENV="${VENV:-/fsx/home/jixuan.chen/.venv}"
PY="${SFT_PYTHON:-$VENV/bin/python}"
cd "$ROOT"

export MODEL_SIZE="${MODEL_SIZE:-9b}"
export SFT_PYTHON="$PY"
export PYTHON_BIN="$PY"
export SFT_DATASET_NAME="${SFT_DATASET_NAME:-tmax_rep19_i1_winner}"
export SFT_OUTPUT_DIR="${SFT_OUTPUT_DIR:-$ROOT/outputs/sft/qwen35_9b_tmax_rep19_i1_winner}"
export SFT_GPUS="${SFT_GPUS:-0,1,2,3,4,5,6,7}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export SFT_LEARNING_RATE="${SFT_LEARNING_RATE:-2.0e-5}"
export SFT_LR_SCHEDULER="${SFT_LR_SCHEDULER:-linear}"
export SFT_MAX_SEQ_LENGTH="${SFT_MAX_SEQ_LENGTH:-16384}"
export SFT_EFFECTIVE_BATCH="${SFT_EFFECTIVE_BATCH:-32}"
export SFT_ATTN_IMPLEMENTATION="${SFT_ATTN_IMPLEMENTATION:-flash_attention_2}"
export PER_TASK="${PER_TASK:-2}"
export MAX_PAIRS_PER_TRAJ="${MAX_PAIRS_PER_TRAJ:-32}"

HARNESS="${EVAL_HARNESS:-$ROOT/recipe/tb2_evolver/runs/tmax-coev-rep19-i1/R2/config.yaml}"
export HARNESS_CONFIG="$HARNESS"
export EVAL_HARNESS="$HARNESS"

DATA_DIR="$ROOT/recipe/tb2_sft/data/$SFT_DATASET_NAME"
OUT_DIR="$SFT_OUTPUT_DIR"

# Holdout-102 under the iter1 winner harness. Reuse the coevolve B-eval as the
# base arm when its summary exists (same YAML, 9B base, 83/102).
export TASKS_JSON="${TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json}"
export ENVS_JSONL="${ENVS_JSONL:-$ROOT/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl}"
export BASE_JOB="${BASE_JOB:-tmax-coev-rep19-i1-B-harness}"
export SFT_JOB="${SFT_JOB:-tmax-sft-rep19-i1w}"
export LORA_PATH="$OUT_DIR"
export LORA_NAME="${LORA_NAME:-qwen35-9b-rep19-i1w}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-2}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-2}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export RESUME="${RESUME:-1}"

SKIP_BUILD="${SKIP_BUILD:-0}"
SKIP_TRAIN="${SKIP_TRAIN:-0}"
SKIP_EVAL="${SKIP_EVAL:-0}"

echo "[$(date -u '+%F %T UTC')] job ${SLURM_JOB_ID:-local} on $(hostname)"
nvidia-smi -L || true
echo "  harness   : $HARNESS"
echo "  dataset   : $SFT_DATASET_NAME -> $DATA_DIR"
echo "  adapter   : $OUT_DIR"
echo "  skip      : build=$SKIP_BUILD train=$SKIP_TRAIN eval=$SKIP_EVAL"

[[ -x "$PY" ]] || { echo "ERROR: missing $PY" >&2; exit 2; }
[[ -f "$HARNESS" ]] || { echo "ERROR: missing harness $HARNESS" >&2; exit 2; }

mkdir -p /fsx/home/jixuan.chen/logs "$OUT_DIR"

# ── 1) corpus ────────────────────────────────────────────────────────────────
if [[ "$SKIP_BUILD" != "1" ]]; then
  echo
  echo "[$(date -u '+%F %T UTC')] ===== 1/3 build winner-only corpus ====="
  bash "$ROOT/scripts/tmax/build_sft_rep19_i1_winner.sh"
else
  echo "[$(date -u '+%F %T UTC')] SKIP_BUILD=1"
fi

[[ -f "$DATA_DIR/train.jsonl" && -f "$DATA_DIR/eval.jsonl" ]] || {
  echo "ERROR: corpus missing under $DATA_DIR" >&2
  exit 2
}
"$PY" - "$DATA_DIR/train.jsonl" <<'PY'
import json, sys
from pathlib import Path
row = json.loads(Path(sys.argv[1]).open().readline())
assert isinstance(row.get("prompt"), list) and isinstance(row.get("completion"), list)
roles = [m.get("role") for m in row["prompt"] if isinstance(m, dict)]
print(f"data OK: prompt_turns={len(row['prompt'])} first_roles={roles[:3]}")
if "system" not in roles:
    raise SystemExit("ERROR: first training pair has no system turn — builder did not inject prompt")
PY

# ── 2) LoRA SFT from 9B base ─────────────────────────────────────────────────
if [[ "$SKIP_TRAIN" != "1" ]]; then
  echo
  echo "[$(date -u '+%F %T UTC')] ===== 2/3 train LoRA ====="
  if [[ -f "$OUT_DIR/adapter_model.safetensors" && -z "${SFT_RESUME:-}" && "${ALLOW_OVERWRITE:-0}" != "1" ]]; then
    echo "ERROR: adapter already exists at $OUT_DIR" >&2
    echo "       ALLOW_OVERWRITE=1 or SKIP_TRAIN=1" >&2
    exit 2
  fi
  unset INIT_ADAPTER INIT_LORA_PATH MODEL_OVERRIDE || true
  bash "$ROOT/scripts/train_sft.sh"
else
  echo "[$(date -u '+%F %T UTC')] SKIP_TRAIN=1"
fi

[[ -f "$OUT_DIR/adapter_model.safetensors" ]] || {
  echo "ERROR: no adapter at $OUT_DIR/adapter_model.safetensors" >&2
  exit 2
}

# ── 3) holdout-102 under the iter1 winner harness ────────────────────────────
if [[ "$SKIP_EVAL" != "1" ]]; then
  echo
  echo "[$(date -u '+%F %T UTC')] ===== 3/3 holdout-102 (same harness) ====="
  command -v docker >/dev/null || { echo "ERROR: docker not in PATH" >&2; exit 2; }
  docker info >/dev/null 2>&1 || { echo "ERROR: docker unavailable" >&2; exit 2; }
  if [[ "$CLEAN_STALE" == "1" ]] && pgrep -f 'vllm.entrypoints.openai.api_server' >/dev/null 2>&1; then
    pkill -f 'vllm.entrypoints.openai.api_server' || true
    sleep 8
  fi
  if [[ ! -f "$ROOT/.benchmarks/tmax/$BASE_JOB/summary.json" ]]; then
    echo "base summary missing at $BASE_JOB — evaluating 9B base under iter1 harness"
    EVAL_SFT=0 JOB_NAME="$BASE_JOB" HARNESS_CONFIG="$HARNESS" \
      bash "$ROOT/scripts/evaluate_tmax.sh"
  else
    echo "reusing base holdout $BASE_JOB"
  fi
  EVAL_SFT=1 LORA_PATH="$OUT_DIR" LORA_NAME="$LORA_NAME" JOB_NAME="$SFT_JOB" \
    HARNESS_CONFIG="$HARNESS" \
    bash "$ROOT/scripts/evaluate_tmax.sh"
  "$PY" - "$ROOT/.benchmarks/tmax/$BASE_JOB/summary.json" \
          "$ROOT/.benchmarks/tmax/$SFT_JOB/summary.json" <<'PY'
import json, sys
base = json.load(open(sys.argv[1]))
sft = json.load(open(sys.argv[2]))
print()
print("===== holdout-102 under iter1 winner harness =====")
print(f"base : {base['n_passed']}/{base['n_tasks']}  ({base['pass_rate']:.3f})")
print(f"sft  : {sft['n_passed']}/{sft['n_tasks']}  ({sft['pass_rate']:.3f})")
print(f"delta: {sft['n_passed'] - base['n_passed']:+d} tasks")
br = {r["task_id"]: r["reward"] for r in base["results"]}
sr = {r["task_id"]: r["reward"] for r in sft["results"]}
gain = [t for t in br if br[t] == 0 and sr.get(t) == 1]
lose = [t for t in br if br[t] == 1 and sr.get(t) == 0]
print(f"flips +{len(gain)} / -{len(lose)}")
PY
else
  echo "[$(date -u '+%F %T UTC')] SKIP_EVAL=1"
fi

echo
echo "[$(date -u '+%F %T UTC')] done"
echo "  corpus  : $DATA_DIR"
echo "  adapter : $OUT_DIR/adapter_model.safetensors"
echo "  log     : /fsx/home/jixuan.chen/logs/tmax_sft_i1w_${SLURM_JOB_ID:-local}.out"
