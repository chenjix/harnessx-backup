#!/usr/bin/env bash
# Evaluate base or LoRA model on the Tmax taxonomy task set (NOT Terminal-Bench).
#
# Default task set = 102 tasks covered by qwen35_9b_tmax_only200 (train-on-test).
#
# Usage:
#   MODEL_SIZE=9b JOB_NAME=tmax-base9b bash scripts/tmax/evaluate_tmax.sh
#   MODEL_SIZE=9b EVAL_SFT=1 LORA_PATH=... LORA_NAME=... JOB_NAME=tmax-sft \
#     bash scripts/tmax/evaluate_tmax.sh
#
# Env:
#   TASKS_JSON / ENVS_JSONL / GPU_POOL / TMAX_CONCURRENT / TMAX_MAX_STEPS /
#   LIMIT / RESUME / SKIP_VLLM / HARNESS_CONFIG / MODEL_OVERRIDE

set -euo pipefail

# Capture BEFORE sourcing _common.sh: that file defaults TASKS_JSON to a TB2
# list, which would otherwise override this script's tmax defaults.
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
_ROOT_EARLY="$(cd "$_HX_SCRIPTS/.." && pwd)"
_TMAX_DEFAULT_TASKS="$_ROOT_EARLY/recipe/tb2_evolver/tasks_tmax_only200.json"
_TMAX_DEFAULT_ENVS="$_ROOT_EARLY/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl"
_CALLER_TASKS_JSON="${TMAX_TASKS_JSON:-${TASKS_JSON:-}}"
_CALLER_ENVS_JSONL="${ENVS_JSONL:-}"

source "$_HX_SCRIPTS/_common.sh"

export TASKS_JSON="${_CALLER_TASKS_JSON:-$_TMAX_DEFAULT_TASKS}"
export ENVS_JSONL="${_CALLER_ENVS_JSONL:-$_TMAX_DEFAULT_ENVS}"
JOB_NAME="${JOB_NAME:-tmax-eval-${MODEL_TAG}-${MODEL_SIZE}-$(date +%Y%m%d-%H%M%S)}"
export TB2_TEMPERATURE="${TB2_TEMPERATURE:-0}"
export TMAX_MAX_STEPS="${TMAX_MAX_STEPS:-80}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-1}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"

if [[ "${EVAL_SFT:-0}" == "1" ]]; then
  export LORA_PATH="${LORA_PATH:?EVAL_SFT=1 requires LORA_PATH}"
  export LORA_NAME="${LORA_NAME:-${MODEL_TAG}-${MODEL_SIZE}-tmax-sft}"
  [[ -d "$LORA_PATH" ]] || { echo "ERROR: LORA_PATH not a directory: $LORA_PATH" >&2; exit 2; }
fi

require_file "$TASKS_JSON"
require_file "$ENVS_JSONL"

extra=()
[[ "${LIMIT:-0}" != "0" ]] && extra+=(--limit "$LIMIT")
[[ "${RESUME:-0}" == "1" ]] && extra+=(--resume)
[[ -n "${TASK_ID:-}" ]] && extra+=(--task-id "$TASK_ID")
if [[ -n "${HARNESS_CONFIG:-}" ]]; then
  require_file "$HARNESS_CONFIG"
  extra+=(--harness-config "$HARNESS_CONFIG")
fi
if [[ -n "${TMAX_SYSTEM_PROMPT_FILE:-}" ]]; then
  require_file "$TMAX_SYSTEM_PROMPT_FILE"
  extra+=(--system-prompt-file "$TMAX_SYSTEM_PROMPT_FILE")
fi

PY="$(python_bin)"
cmd=(
  "$PY" -m recipe.tmax_eval.run_eval
  --tasks-json "$TASKS_JSON"
  --envs-jsonl "$ENVS_JSONL"
  --job-name "$JOB_NAME"
  --jobs-dir "$ROOT/.benchmarks/tmax"
  --work-root "$ROOT/.tmax_eval_work"
  --max-steps "$TMAX_MAX_STEPS"
  --temperature "$TB2_TEMPERATURE"
  --concurrent "$TMAX_CONCURRENT"
  "${extra[@]}"
)

echo "[evaluate_tmax] model_size=$MODEL_SIZE job=$JOB_NAME tasks=$TASKS_JSON"
echo "[evaluate_tmax] eval_sft=${EVAL_SFT:-0} lora=${LORA_PATH:-none}"
echo "[evaluate_tmax] harness=${HARNESS_CONFIG:-none}"
if [[ "${SKIP_VLLM:-0}" == "1" ]]; then
  export TB2_MODEL="${TB2_MODEL:-$MODEL}"
  [[ -n "${LORA_PATH:-}" ]] && export TB2_MODEL="${LORA_NAME}"
  "${cmd[@]}"
elif [[ -n "${GPU_POOL:-}" ]]; then
  bash "$ROOT/scripts/with_server_pool.sh" -- "${cmd[@]}"
else
  if [[ -f "$ROOT/scripts/with_server.sh" ]]; then
    bash "$ROOT/scripts/with_server.sh" -- "${cmd[@]}"
  else
    echo "ERROR: set GPU_POOL=0,1,... or SKIP_VLLM=1 with TB2_API_BASE" >&2
    exit 2
  fi
fi

SUMMARY="$ROOT/.benchmarks/tmax/$JOB_NAME/summary.json"
echo
echo "Results: $SUMMARY"
if [[ -f "$SUMMARY" ]]; then
  # Quoted heredoc — no bash quote nesting with Python f-strings.
  SUMMARY_PATH="$SUMMARY" "$PY" - <<'PY' | column -t || true
import json, os
from pathlib import Path
s = json.loads(Path(os.environ["SUMMARY_PATH"]).read_text())
print(f"passed\t{s['n_passed']}/{s['n_tasks']}\t{s['pass_rate']:.3f}")
for d, v in s.get("by_domain", {}).items():
    print(f"{d}\t{v['passed']}/{v['total']}")
PY
fi
