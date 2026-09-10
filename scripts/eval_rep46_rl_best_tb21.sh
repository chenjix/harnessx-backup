#!/usr/bin/env bash
# Evaluate the best accepted Harness+RL pair on all 89 TB2.1 tasks.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RL_MODEL="$ROOT/outputs/rl/tmax_coev_rep46_i2"
HARNESS_CONFIG="$ROOT/configs/rep46_rl_best_tb21_transfer.yaml"
[[ -f "$RL_MODEL/model.safetensors" && -f "$RL_MODEL/config.json" ]] || {
  echo "ERROR: RL2 full checkpoint is incomplete: $RL_MODEL" >&2; exit 2;
}
[[ -f "$HARNESS_CONFIG" ]] || { echo "ERROR: missing harness: $HARNESS_CONFIG" >&2; exit 2; }

export MODEL_SIZE=9b
export MODEL_OVERRIDE="$RL_MODEL"
export EVAL_SFT=0
export USE_EVOLVED_HARNESS=0
export HARNESS_CONFIG
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
export RUN_TAG="rep46-rl2-rep19h1-tb21-native-$stamp"
export JOB_NAME="$RUN_TAG"

echo "Evaluating RL model: $RL_MODEL"
echo "Evaluating harness : $HARNESS_CONFIG"
exec bash "$ROOT/scripts/tb2/evaluate.sh"
