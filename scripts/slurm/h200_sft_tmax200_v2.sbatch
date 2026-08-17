#!/bin/bash
#SBATCH --job-name=tmax-sft9b-v2
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=1-00:00:00
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_sft9b_v2_%j.out

# Fresh LoRA SFT on conversational tmax_only200 (Tmax-aligned recipe).
# Does NOT resume the old qwen35_9b_tmax_only200 adapter — new output dir.
#
#   sbatch h200_sft_tmax200_v2.sbatch
#
# Optional overrides (sbatch --export=ALL,...):
#   SFT_MAX_SEQ_LENGTH=32768
#   SFT_EFFECTIVE_BATCH=64
#   SFT_EPOCHS=2
#   SFT_LEARNING_RATE=2.0e-5
#   SFT_RESUME=<checkpoint-dir>   # only if continuing THIS v2 run

set -euo pipefail

ROOT=/fsx/home/jixuan.chen/harnessx-backup
VENV="${VENV:-/fsx/home/jixuan.chen/.venv}"
DATA_DIR="$ROOT/recipe/tb2_sft/data/qwen35_9b_tmax_only200"
OUT_DIR="${SFT_OUTPUT_DIR:-$ROOT/outputs/sft/qwen35_9b_tmax_only200_v2}"

export MODEL_SIZE="${MODEL_SIZE:-9b}"
export SFT_DATASET_NAME="${SFT_DATASET_NAME:-qwen35_9b_tmax_only200}"
export SFT_OUTPUT_DIR="$OUT_DIR"
export SFT_GPUS="${SFT_GPUS:-0,1,2,3,4,5,6,7}"
export SFT_PYTHON="${SFT_PYTHON:-$VENV/bin/python}"
export SFT_PYTHON

# Aligned defaults (also baked into train_sft.sh; export so logs show intent).
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export SFT_LEARNING_RATE="${SFT_LEARNING_RATE:-2.0e-5}"
export SFT_LR_SCHEDULER="${SFT_LR_SCHEDULER:-linear}"
export SFT_MAX_SEQ_LENGTH="${SFT_MAX_SEQ_LENGTH:-16384}"
export SFT_EFFECTIVE_BATCH="${SFT_EFFECTIVE_BATCH:-32}"
export SFT_ATTN_IMPLEMENTATION="${SFT_ATTN_IMPLEMENTATION:-flash_attention_2}"

echo "[$(date)] job $SLURM_JOB_ID started on $(hostname)"
echo "[$(date)] attach: srun --jobid=$SLURM_JOB_ID --overlap --pty bash"
nvidia-smi -L || true

[[ -x "$SFT_PYTHON" ]] || { echo "ERROR: missing $SFT_PYTHON" >&2; exit 2; }
[[ -f "$DATA_DIR/train.jsonl" && -f "$DATA_DIR/eval.jsonl" ]] || {
  echo "ERROR: missing conversational SFT data under $DATA_DIR" >&2
  echo "       run: bash $ROOT/scripts/rebuild_tmax_only200_sft_data.sh" >&2
  exit 2
}

# Refuse to train if train.jsonl is still legacy string prompts.
"$SFT_PYTHON" - "$DATA_DIR/train.jsonl" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
row = json.loads(p.open().readline())
if not isinstance(row.get("prompt"), list) or not isinstance(row.get("completion"), list):
    raise SystemExit(
        f"ERROR: {p} is not conversational (prompt/completion must be lists). "
        "Rebuild with scripts/rebuild_tmax_only200_sft_data.sh"
    )
print(f"data OK: {p} prompt_turns={len(row['prompt'])}")
PY

# Do not clobber an unfinished v2 run unless explicitly allowed.
if [[ -f "$OUT_DIR/adapter_model.safetensors" && -z "${SFT_RESUME:-}" && "${ALLOW_OVERWRITE:-0}" != "1" ]]; then
  echo "ERROR: adapter already exists at $OUT_DIR" >&2
  echo "       set ALLOW_OVERWRITE=1 to retrain, or SFT_RESUME=<ckpt> to continue" >&2
  exit 2
fi

cd "$ROOT"
mkdir -p /fsx/home/jixuan.chen/logs "$OUT_DIR"

echo "[$(date)] Tmax-aligned SFT (v2)"
echo "  dataset   : $SFT_DATASET_NAME  ($DATA_DIR)"
echo "  output    : $OUT_DIR"
echo "  gpus      : $SFT_GPUS"
echo "  epochs    : $SFT_EPOCHS  lr=$SFT_LEARNING_RATE  sched=$SFT_LR_SCHEDULER"
echo "  max_seq   : $SFT_MAX_SEQ_LENGTH  eff_batch=$SFT_EFFECTIVE_BATCH"
echo "  attn      : $SFT_ATTN_IMPLEMENTATION"
[[ -n "${SFT_RESUME:-}" ]] && echo "  resume    : $SFT_RESUME"

bash "$ROOT/scripts/train_sft.sh"

echo
echo "[$(date)] done"
echo "  adapter   : $OUT_DIR/adapter_model.safetensors"
echo "  slurm log : /fsx/home/jixuan.chen/logs/tmax_sft9b_v2_${SLURM_JOB_ID}.out"
