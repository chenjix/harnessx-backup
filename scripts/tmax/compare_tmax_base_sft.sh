#!/usr/bin/env bash
# Train-on-test comparison: base 9B vs SFT adapter on the 102 Tmax tasks
# covered by qwen35_9b_tmax_only200.
#
# Prerequisites:
#   - SFT adapter at outputs/sft/qwen35_9b_tmax_only200 (or set LORA_PATH)
#   - Docker + GPU node
#
# Usage:
#   MODEL_SIZE=9b GPU_POOL=0,1,2,3,4,5,6,7 \
#     bash scripts/compare_tmax_base_sft.sh
#
# Optional:
#   LIMIT=5          smoke both arms on 5 tasks
#   TMAX_CONCURRENT=2
#   RESUME=1

set -euo pipefail
# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"

export MODEL_SIZE="${MODEL_SIZE:-9b}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export TB2_TEMPERATURE="${TB2_TEMPERATURE:-0}"
export TMAX_MAX_STEPS="${TMAX_MAX_STEPS:-80}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-1}"
export RESUME="${RESUME:-0}"

LORA_PATH="${LORA_PATH:-$ROOT/outputs/sft/qwen35_9b_tmax_only200}"
LORA_NAME="${LORA_NAME:-qwen35-9b-tmax200}"
[[ -d "$LORA_PATH" ]] || {
  echo "ERROR: adapter missing: $LORA_PATH" >&2
  echo "       Train first:" >&2
  echo "         MODEL_SIZE=9b SFT_DATASET_NAME=qwen35_9b_tmax_only200 \\" >&2
  echo "           SFT_OUTPUT_DIR=$LORA_PATH bash scripts/train_sft.sh" >&2
  exit 2
}

BASE_JOB="${BASE_JOB:-tmax-base-${MODEL_SIZE}}"
SFT_JOB="${SFT_JOB:-tmax-sft-${MODEL_SIZE}-only200}"

echo "===== 1/2 base ====="
EVAL_SFT=0 JOB_NAME="$BASE_JOB" \
  bash "$ROOT/scripts/evaluate_tmax.sh"

echo
echo "===== 2/2 SFT ====="
EVAL_SFT=1 LORA_PATH="$LORA_PATH" LORA_NAME="$LORA_NAME" JOB_NAME="$SFT_JOB" \
  bash "$ROOT/scripts/evaluate_tmax.sh"

PY="${PYTHON_BIN:-${SFT_PYTHON:-$HOME/.venv/bin/python}}"
"$PY" - <<PY
import json
from pathlib import Path
root = Path("$ROOT") / ".benchmarks/tmax"
base = json.loads((root / "$BASE_JOB" / "summary.json").read_text())
sft  = json.loads((root / "$SFT_JOB"  / "summary.json").read_text())
print()
print("===== train-on-test (Tmax 102 tasks) =====")
print(f"base : {base['n_passed']}/{base['n_tasks']}  ({base['pass_rate']:.3f})")
print(f"sft  : {sft['n_passed']}/{sft['n_tasks']}  ({sft['pass_rate']:.3f})")
print(f"delta: {sft['n_passed'] - base['n_passed']:+d} tasks")
# per-task flips
br = {r["task_id"]: r["reward"] for r in base["results"]}
sr = {r["task_id"]: r["reward"] for r in sft["results"]}
gain = [t for t in br if br[t]==0 and sr.get(t)==1]
lose = [t for t in br if br[t]==1 and sr.get(t)==0]
print(f"flips +{len(gain)} / -{len(lose)}")
if gain[:10]:
    print("  newly solved:", ", ".join(gain[:10]))
if lose[:10]:
    print("  newly failed:", ", ".join(lose[:10]))
PY
