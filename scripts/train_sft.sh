#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"

DATASET_NAME="${SFT_DATASET_NAME:-${MODEL_TAG}_${MODEL_SIZE}_own_success}"
DATA_DIR="$ROOT/recipe/tb2_sft/data/$DATASET_NAME"
require_file "$DATA_DIR/train.jsonl"
require_file "$DATA_DIR/eval.jsonl"

SFT_NAME="${SFT_NAME:-${MODEL_TAG}_${MODEL_SIZE}_${DATASET_NAME}}"
OUTPUT_DIR="${SFT_OUTPUT_DIR:-$ROOT/outputs/sft/$SFT_NAME}"
CONFIG_PATH="$RUN_ROOT/sft_${MODEL_SIZE}.yaml"

# ── Training interpreter: plain venv OR conda env ────────────────────────────
# Originally conda-only, which made the stage unrunnable once miniconda3 was
# removed from the host. SFT_PYTHON short-circuits that: point it at any
# interpreter that has trl/peft and no conda is involved at all.
#
# Either way the interpreter is invoked by ABSOLUTE PATH rather than relying on
# a bare `python` resolving correctly. The runner below uses `bash -lc`, a
# login shell that sources the user's rc files — if a vLLM-serving virtualenv
# is active there, `python` can resolve into it, and that venv deliberately has
# no trl/peft (it exists to serve vLLM). The failure mode is a bare
# `ModuleNotFoundError: No module named 'trl'` after the stage has already been
# entered, which reads like a missing dependency rather than the wrong
# interpreter.
USE_CONDA=1
if [[ -n "${SFT_PYTHON:-}" ]]; then
  USE_CONDA=0
  # Do NOT readlink -f this. A uv-created venv's bin/python is a symlink to the
  # base interpreter (.local/share/uv/python/cpython-3.12.../bin/python3.12);
  # resolving it invokes that base interpreter directly, so sys.prefix is the
  # base install and the venv's site-packages — trl, peft, transformers — are
  # invisible. The dependency check below then reported every package missing
  # for an env that has all of them.
  CONDA_ENV_PY="$SFT_PYTHON"
  if [[ ! -x "$CONDA_ENV_PY" ]]; then
    echo "ERROR: SFT_PYTHON is not an executable interpreter: $SFT_PYTHON" >&2
    exit 2
  fi
else
  CONDA_SH="${CONDA_SH:-/fsx/home/jixuan.chen/miniconda3/etc/profile.d/conda.sh}"
  SFT_CONDA_ENV="${SFT_CONDA_ENV:-vllm-019-cu128-clean}"
  if [[ ! -f "$CONDA_SH" ]]; then
    echo "ERROR: no conda at $CONDA_SH and SFT_PYTHON is unset." >&2
    echo "       Set SFT_PYTHON=/path/to/venv/bin/python (needs trl, peft, datasets," >&2
    echo "       transformers, torch), or CONDA_SH to a real conda profile script." >&2
    exit 2
  fi
  CONDA_ENV_PY="${CONDA_ENV_PY:-$(dirname "$(dirname "$CONDA_SH")")/../envs/$SFT_CONDA_ENV/bin/python}"
  CONDA_ENV_PY="$(readlink -f "$CONDA_ENV_PY" 2>/dev/null || echo "$CONDA_ENV_PY")"
  if [[ ! -x "$CONDA_ENV_PY" ]]; then
    echo "ERROR: no python found for conda env '$SFT_CONDA_ENV' at: $CONDA_ENV_PY" >&2
    echo "       Set CONDA_ENV_PY explicitly, SFT_CONDA_ENV to an env that has trl/peft," >&2
    echo "       or SFT_PYTHON to bypass conda entirely." >&2
    exit 2
  fi
fi

# Fail in seconds with an actionable message instead of after weights load.
missing="$("$CONDA_ENV_PY" - <<'PY'
import importlib
missing = []
for m in ("trl", "peft", "datasets", "transformers", "torch"):
    try:
        importlib.import_module(m)
    except Exception:
        missing.append(m)
print(" ".join(missing))
PY
)"
if [[ -n "$missing" ]]; then
  # SFT_CONDA_ENV only exists on the conda branch. Interpolating it
  # unconditionally tripped `set -u` on the SFT_PYTHON branch, so the process
  # died with a bare "SFT_CONDA_ENV: unbound variable" and the message naming
  # the actual missing packages never printed.
  echo "ERROR: SFT training deps missing from ${SFT_CONDA_ENV:-$CONDA_ENV_PY} ($CONDA_ENV_PY): $missing" >&2
  echo "       Install them there, or point SFT_PYTHON / SFT_CONDA_ENV at an env that has them." >&2
  exit 2
fi

# ── Resolve the model to a LOCAL snapshot directory, once, in this process ────
# Under torchrun every rank would otherwise call from_pretrained("Qwen/...")
# concurrently. huggingface_hub re-validates and can re-create the snapshot
# symlinks while doing so, and a rank whose existence check lands in that
# window dies with a misleading
#   OSError: <repo> does not appear to have a file named model.safetensors-...
# even though the cache is complete (observed: 5/8 ranks loaded, 3 died on
# different shards). Handing every rank a plain directory path takes the Hub
# out of the hot path entirely, so there is nothing left to race on.
# MODEL_OVERRIDE (full RL ckpt) wins over the stock HF id from MODEL_SIZE.
MODEL="${MODEL_OVERRIDE:-$MODEL}"
MODEL_PATH="$("$CONDA_ENV_PY" - "$MODEL" <<'PY'
import os, sys
from pathlib import Path
ref = sys.argv[1]
if Path(ref).is_dir():
    print(ref)
else:
    from huggingface_hub import snapshot_download
    # No-op when already fully cached; downloads only what is missing.
    print(snapshot_download(ref))
PY
)"
[[ -d "$MODEL_PATH" ]] || { echo "ERROR: could not resolve model '$MODEL' to a local dir (got: $MODEL_PATH)" >&2; exit 2; }
# Per-size defaults, looked up by name.
# Defaults aligned with official tmax SFT (scripts/tmax/SFT/sft_qwen35_9b_*.sh):
#   LR 2e-5, linear schedule, 2 epochs, long context, larger global batch.
# We still train LoRA (official uses full FT + ZeRO-3 on 32 GPUs); override via env.
case "$MODEL_SIZE" in
  2b)  _def_epochs="${SFT_EPOCHS_2B:-2}";  _def_lr="${SFT_LR_2B:-2.0e-5}" ;;
  4b)  _def_epochs="${SFT_EPOCHS_4B:-2}";  _def_lr="${SFT_LR_4B:-2.0e-5}" ;;
  9b)  _def_epochs="${SFT_EPOCHS_9B:-2}";  _def_lr="${SFT_LR_9B:-2.0e-5}" ;;
  27b) _def_epochs="${SFT_EPOCHS_27B:-2}"; _def_lr="${SFT_LR_27B:-2.0e-5}" ;;
  *)   echo "ERROR: no SFT defaults for MODEL_SIZE=$MODEL_SIZE" >&2; exit 2 ;;
esac
EPOCHS="${SFT_EPOCHS:-$_def_epochs}"
LR="${SFT_LEARNING_RATE:-$_def_lr}"
LR_SCHED="${SFT_LR_SCHEDULER:-linear}"
# Official uses 32768; LoRA+DDP on one node often needs a lower cap — override
# with SFT_MAX_SEQ_LENGTH=32768 when memory allows.
MAX_SEQ="${SFT_MAX_SEQ_LENGTH:-16384}"
ATTN_IMPL="${SFT_ATTN_IMPLEMENTATION:-flash_attention_2}"

# ── Multi-GPU (DDP) ──────────────────────────────────────────────────────────
# Data-parallel across every GPU in GPU_POOL (or SFT_GPUS). Each rank holds a
# full copy of the model and processes different samples; only LoRA adapter
# gradients are all-reduced, so communication is cheap.
SFT_GPUS="${SFT_GPUS:-${GPU_POOL:-${GPU:-0}}}"
NPROC="$(awk -F',' '{print NF}' <<<"$SFT_GPUS")"
PER_DEVICE_BS="${SFT_PER_DEVICE_BS:-1}"

# Official global batch ≈ 128 (1×4×8×4 nodes). On one node aim for 32 by default
# (1 × grad_accum × nproc); raise with SFT_EFFECTIVE_BATCH when memory allows.
TARGET_EFF_BATCH="${SFT_EFFECTIVE_BATCH:-32}"
GRAD_ACCUM="${SFT_GRAD_ACCUM:-$(( TARGET_EFF_BATCH / (PER_DEVICE_BS * NPROC) ))}"
(( GRAD_ACCUM < 1 )) && GRAD_ACCUM=1
EFF_BATCH=$(( PER_DEVICE_BS * GRAD_ACCUM * NPROC ))

cat >"$CONFIG_PATH" <<EOF
model_name_or_path: "$MODEL_PATH"
output_dir: "$OUTPUT_DIR"
dataset_train_file: "$DATA_DIR/train.jsonl"
dataset_eval_file: "$DATA_DIR/eval.jsonl"
max_seq_length: $MAX_SEQ
packing: false
per_device_train_batch_size: $PER_DEVICE_BS
per_device_eval_batch_size: $PER_DEVICE_BS
gradient_accumulation_steps: $GRAD_ACCUM
num_train_epochs: $EPOCHS
learning_rate: $LR
lr_scheduler_type: $LR_SCHED
warmup_ratio: 0.03
weight_decay: 0.0
max_grad_norm: 1.0
logging_steps: 5
save_steps: 100
eval_steps: 25
save_total_limit: 2
bf16: true
gradient_checkpointing: ${SFT_GRAD_CHECKPOINT:-true}
eval_strategy: epoch
save_strategy: epoch
completion_only_loss: true
lora:
  r: 32
  alpha: 64
  dropout: 0.05
  target_modules: [q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj]
attn_implementation: $ATTN_IMPL
chat_template_style: qwen
EOF

# INIT_ADAPTER continues training an existing LoRA instead of initialising a
# fresh one. This is what makes iteration N+1 of the harness->SFT loop build on
# iteration N rather than relearning from base: without it each round trains a
# new adapter on that round's data alone, and later rounds — whose corpora are
# smaller, being top-ups rather than full rebuilds — can score *worse* than
# earlier ones purely from having seen less data.
#
# LoRA rank/alpha/targets come from the loaded adapter in this path, so the
# `lora:` block above is ignored; changing r between rounds silently does
# nothing, which is why it is checked rather than assumed.
if [[ -n "${INIT_ADAPTER:-}" ]]; then
  [[ -d "$INIT_ADAPTER" ]] || { echo "ERROR: INIT_ADAPTER not a directory: $INIT_ADAPTER" >&2; exit 2; }
  ls "$INIT_ADAPTER"/adapter_model.safetensors >/dev/null 2>&1 \
    || ls "$INIT_ADAPTER"/adapter_model.bin >/dev/null 2>&1 \
    || { echo "ERROR: no adapter weights under INIT_ADAPTER=$INIT_ADAPTER" >&2; exit 2; }
  if [[ "$(readlink -f "$INIT_ADAPTER")" == "$(readlink -f "$OUTPUT_DIR")" ]]; then
    echo "ERROR: INIT_ADAPTER and OUTPUT_DIR are the same directory." >&2
    echo "       Trainer checkpoints would overwrite the weights being read." >&2
    exit 2
  fi
  echo "init_adapter: \"$INIT_ADAPTER\"" >>"$CONFIG_PATH"
fi

if (( NPROC > 1 )); then
  TORCHRUN="$(dirname "$CONDA_ENV_PY")/torchrun"
  [[ -x "$TORCHRUN" ]] || { echo "ERROR: torchrun not found at $TORCHRUN" >&2; exit 2; }
  # Random-ish free port so two concurrent SFT jobs (e.g. 4B and 9B on the same
  # host) don't collide on the rendezvous port.
  MASTER_PORT="${SFT_MASTER_PORT:-$(( 29500 + (RANDOM % 2000) ))}"
  LAUNCHER="'$TORCHRUN' --nproc_per_node=$NPROC --master_port=$MASTER_PORT"
else
  LAUNCHER="'$CONDA_ENV_PY'"
fi

echo "Training LoRA: model=$MODEL data=$DATASET_NAME output=$OUTPUT_DIR"
echo "  interpreter : $CONDA_ENV_PY"
echo "  GPUs        : $SFT_GPUS (nproc=$NPROC)"
echo "  batch       : per_device=$PER_DEVICE_BS x grad_accum=$GRAD_ACCUM x nproc=$NPROC = effective $EFF_BATCH"
echo "  epochs      : $EPOCHS   grad_checkpointing=${SFT_GRAD_CHECKPOINT:-true}"
[[ -n "${INIT_ADAPTER:-}" ]] && echo "  init from   : $INIT_ADAPTER (continuing, not re-initialising LoRA)"
if (( USE_CONDA )); then
  ACTIVATE="source '$CONDA_SH' && conda activate '$SFT_CONDA_ENV' && "
else
  # No conda: put the venv's bin first so torchrun's spawned ranks resolve the
  # same interpreter, and neutralise any venv already active in the login shell.
  ACTIVATE="export VIRTUAL_ENV='$(dirname "$(dirname "$CONDA_ENV_PY")")' && export PATH=\"\$VIRTUAL_ENV/bin:\$PATH\" && unset PYTHONHOME && "
fi
bash -lc "${ACTIVATE}\
  cd '$ROOT/recipe/tb2_sft' && \
  CUDA_VISIBLE_DEVICES='$SFT_GPUS' PYTHONPATH='$ROOT' \
  SFT_RESUME='${SFT_RESUME:-0}' \
  OMP_NUM_THREADS=\${OMP_NUM_THREADS:-8} \
  $LAUNCHER src/train_sft_lora.py --config '$CONFIG_PATH'" \
  2>&1 | tee "$LOG_ROOT/train_sft.log"

echo "SFT adapter: $OUTPUT_DIR"
