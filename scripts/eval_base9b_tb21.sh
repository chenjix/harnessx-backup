#!/usr/bin/env bash
# Evaluate untouched Qwen3.5-9B + native baseline harness on all 89 TB2.1 tasks.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export MODEL_SIZE=9b
unset MODEL_OVERRIDE LORA_PATH LORA_NAME
export EVAL_SFT=0
export USE_EVOLVED_HARNESS=0
export HARNESS_CONFIG="$ROOT/configs/baseline_harness.yaml"
export TASKS_JSON="$ROOT/recipe/tb2_evolver/tasks_all_tb2.json"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export TB2_CONCURRENT="${TB2_CONCURRENT:-2}"
export TB2_MAX_STEPS="${TB2_MAX_STEPS:-120}"
export TB2_DELETE_IMAGES="${TB2_DELETE_IMAGES:-0}"
export TB2_PRUNE_EVERY="${TB2_PRUNE_EVERY:-10}"
export TB2_MIN_FREE_GB="${TB2_MIN_FREE_GB:-30}"
export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export PATH="$VLLM_VENV/bin:$PATH"

stamp="${EVAL_STAMP:-$(date -u +%Y%m%d-%H%M%S)}"
export RUN_TAG="base-qwen35-9b-tb21-$stamp"
export JOB_NAME="$RUN_TAG"

echo "===== Base Qwen3.5-9B TB2.1 full-89 ====="
echo "model   : Qwen/Qwen3.5-9B (no adapter, no checkpoint override)"
echo "harness : $HARNESS_CONFIG"
exec bash "$ROOT/scripts/tb2/evaluate.sh"
