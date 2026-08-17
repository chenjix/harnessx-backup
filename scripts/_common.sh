#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROOT

ENV_FILE="${ENV_FILE:-$ROOT/.env}"
# .env holds on-disk DEFAULTS; anything the caller set explicitly must win.
#
# `set -a; source "$ENV_FILE"` clobbers the caller's value for every name .env
# mentions. That silently discarded command-line overrides: `NUM_ROUNDS=6 bash
# scripts/...` ran 4 rounds because .env pins NUM_ROUNDS=4, and `MODEL_SIZE=9b`
# served the 4B model for the same reason. Both failures are invisible — the
# run proceeds happily with the wrong value.
#
# So: snapshot every name .env assigns that is ALREADY set and non-empty in the
# environment, source .env, then restore the snapshot on top.
declare -A _HX_PRESET=()
if [[ -f "$ENV_FILE" ]]; then
  if grep -Eq '^[[:space:]]*(bash|sh|python|python3|source|\.)[[:space:]]' "$ENV_FILE"; then
    echo "ERROR: $ENV_FILE must contain assignments/exports only." >&2
    exit 2
  fi
  while IFS= read -r _hx_name; do
    [[ -n "$_hx_name" ]] || continue
    if [[ -n "${!_hx_name:-}" ]]; then
      _HX_PRESET["$_hx_name"]="${!_hx_name}"
    fi
  done < <(sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "$ENV_FILE")

  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a

  for _hx_name in "${!_HX_PRESET[@]}"; do
    export "$_hx_name=${_HX_PRESET[$_hx_name]}"
  done
  if [[ "${HX_DEBUG_ENV:-0}" == "1" && "${#_HX_PRESET[@]}" -gt 0 ]]; then
    echo "[_common] caller overrides kept over $ENV_FILE: ${!_HX_PRESET[*]}" >&2
  fi
fi
unset _HX_PRESET _hx_name

MODEL_SIZE="${MODEL_SIZE:-4b}"
# Optional full-checkpoint override (e.g. post-RL merged weights). Prefer this
# over the stock HF id when set; LoRA still layers on via LORA_PATH separately.
case "$MODEL_SIZE" in
  4b) MODEL="${MODEL_OVERRIDE:-${MODEL_4B:-Qwen/Qwen3.5-4B}}" ;;
  9b) MODEL="${MODEL_OVERRIDE:-${MODEL_9B:-Qwen/Qwen3.5-9B}}" ;;
  # Qwen3.6-27B: same qwen3_5 architecture family as the 3.5 models (hybrid
  # linear/full attention, mrope, vision+video preprocessors), so the
  # qwen3_xml tool parser and the base+LoRA serving rule both still apply.
  27b) MODEL="${MODEL_OVERRIDE:-${MODEL_27B:-Qwen/Qwen3.6-27B}}" ;;
  *) echo "ERROR: MODEL_SIZE must be 4b, 9b or 27b (got $MODEL_SIZE)" >&2; exit 2 ;;
esac
# Naming prefix for run tags / dataset dirs / adapter dirs. The 4B and 9B
# models are Qwen3.5; 27B is Qwen3.6. Hardcoding "qwen35" everywhere would
# label 27B artifacts as 3.5 data, which is exactly the kind of provenance lie
# that makes an ablation table unusable six weeks later.
case "$MODEL_SIZE" in
  27b) MODEL_TAG="${MODEL_TAG:-qwen36}" ;;
  *)   MODEL_TAG="${MODEL_TAG:-qwen35}" ;;
esac
export MODEL MODEL_SIZE MODEL_TAG

export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
cd "$ROOT"
export TB2_DATASET="${TB2_DATASET:-terminal-bench@2.0}"
export TB2_API_KEY="${TB2_API_KEY:-EMPTY}"
export TASKS_JSON="${TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_sample16_seed42_act15.json}"
export RUN_TAG="${RUN_TAG:-tb2-${MODEL_TAG}-${MODEL_SIZE}-$(date +%Y%m%d-%H%M%S)}"
export RUN_ROOT="${RUN_ROOT:-$ROOT/outputs/runs/$RUN_TAG}"
export LOG_ROOT="${LOG_ROOT:-$ROOT/logs/$RUN_TAG}"
mkdir -p "$RUN_ROOT" "$LOG_ROOT"

require_var() {
  local name="$1"
  [[ -n "${!name:-}" ]] || { echo "ERROR: $name is required" >&2; exit 2; }
}

require_file() {
  local path="$1"
  [[ -f "$path" ]] || { echo "ERROR: file not found: $path" >&2; exit 2; }
}

python_bin() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s\n' "$PYTHON_BIN"
  elif [[ -x "${VLLM_VENV:-$HOME/.venv}/bin/python" ]]; then
    printf '%s\n' "${VLLM_VENV:-$HOME/.venv}/bin/python"
  else
    command -v python3
  fi
}
