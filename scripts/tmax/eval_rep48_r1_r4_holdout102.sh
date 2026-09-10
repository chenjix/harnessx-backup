#!/usr/bin/env bash
# Evaluate each promoted rep48 harness (R1..R4) on the same holdout-102 with
# frozen Qwen3.5-9B weights. Intended to run inside one shared vLLM pool.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN="$ROOT/recipe/tb2_evolver/runs/tmax-coev-rep48-i1"

export MODEL_SIZE=9b
export TASKS_JSON="$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json"
export ENVS_JSONL="$ROOT/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl"
export TB2_TEMPERATURE=0
export TMAX_MAX_STEPS=80
export TMAX_MAX_TOKENS=4096
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
export RESUME="${RESUME:-0}"
export SKIP_VLLM=1

for round in R1 R2 R3 R4; do
  cfg="$RUN/$round/config.yaml"
  [[ -f "$cfg" ]] || { echo "ERROR: missing $cfg" >&2; exit 2; }
  job="rep48-${round,,}-holdout102-base9b"
  echo "===== $round: $cfg -> $job ====="
  HARNESS_CONFIG="$cfg" JOB_NAME="$job" \
    bash "$ROOT/scripts/tmax/evaluate_tmax.sh"
done

"${PYTHON_BIN:-python3}" - "$ROOT" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1]) / ".benchmarks" / "tmax"
print("\n===== rep48 R1-R4 holdout-102 =====")
for r in range(1, 5):
    job = f"rep48-r{r}-holdout102-base9b"
    p = root / job / "summary.json"
    s = json.loads(p.read_text())
    print(f"R{r}\t{s['n_passed']}/{s['n_tasks']}\t{s['pass_rate']:.4f}\t{p}")
PY
