#!/usr/bin/env bash
# Evaluate rep22's promoted SFT model+harness pair on Tmax and/or TB2.1.
# Usage: TARGET=tmax|tb21|both TB21_HARNESS_MODE=native|exact bash scripts/eval_rep22_best.sh
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TARGET="${TARGET:-both}"
TB21_HARNESS_MODE="${TB21_HARNESS_MODE:-native}"
case "$TARGET" in tmax|tb21|both) ;; *) echo "ERROR: TARGET must be tmax, tb21, or both" >&2; exit 2 ;; esac
case "$TB21_HARNESS_MODE" in native|exact) ;; *) echo "ERROR: TB21_HARNESS_MODE must be native or exact" >&2; exit 2 ;; esac

REP22_MODEL="$ROOT/outputs/sft/tmax_coev_rep22_i1"
REP22_HARNESS="$ROOT/recipe/tb2_evolver/runs/tmax-coev-rep22-i2/R2/config.yaml"
TB21_NATIVE_HARNESS="$ROOT/configs/rep22_best_tb21_transfer.yaml"

[[ -d "$REP22_MODEL" && -f "$REP22_MODEL/adapter_model.safetensors" ]] || {
  echo "ERROR: rep22 adapter is missing: $REP22_MODEL" >&2; exit 2;
}
[[ -f "$REP22_HARNESS" ]] || { echo "ERROR: rep22 harness is missing: $REP22_HARNESS" >&2; exit 2; }
[[ -f "$TB21_NATIVE_HARNESS" ]] || { echo "ERROR: TB2 transfer harness is missing" >&2; exit 2; }

export MODEL_SIZE=9b
export EVAL_SFT=1
export LORA_PATH="$REP22_MODEL"
export LORA_NAME=qwen35-9b-rep22-best
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export PATH="$VLLM_VENV/bin:$PATH"

stamp="${EVAL_STAMP:-$(date -u +%Y%m%d-%H%M%S)}"

if [[ "$TARGET" == tmax || "$TARGET" == both ]]; then
  export HARNESS_CONFIG="$REP22_HARNESS"
  export JOB_NAME="rep22-best-tmax-h102-$stamp"
  export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
  echo "===== Tmax holdout-102: exact promoted rep22 pair ====="
  bash "$ROOT/scripts/tmax/evaluate_tmax.sh"
fi

if [[ "$TARGET" == tb21 || "$TARGET" == both ]]; then
  if [[ "$TB21_HARNESS_MODE" == exact ]]; then
    export HARNESS_CONFIG="$REP22_HARNESS"
    echo "WARNING: exact mode preserves Tmax's /home/user prompt contract on TB2 (/app)." >&2
  else
    export HARNESS_CONFIG="$TB21_NATIVE_HARNESS"
  fi
  export TASKS_JSON="$ROOT/recipe/tb2_evolver/tasks_all_tb2.json"
  export RUN_TAG="rep22-best-tb21-${TB21_HARNESS_MODE}-$stamp"
  export JOB_NAME="$RUN_TAG"
  export TB2_CONCURRENT="${TB2_CONCURRENT:-2}"
  export TB2_MAX_STEPS="${TB2_MAX_STEPS:-120}"
  export TB2_DELETE_IMAGES="${TB2_DELETE_IMAGES:-0}"
  export TB2_PRUNE_EVERY="${TB2_PRUNE_EVERY:-10}"
  export TB2_MIN_FREE_GB="${TB2_MIN_FREE_GB:-30}"
  echo "===== Terminal-Bench 2.1 (registry terminal-bench@2.0): $TB21_HARNESS_MODE transfer ====="
  bash "$ROOT/scripts/tb2/evaluate.sh"
fi
