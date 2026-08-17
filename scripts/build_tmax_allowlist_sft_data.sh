#!/usr/bin/env bash
# Build a pure-Tmax SFT corpus restricted to a fixed task allowlist (default:
# the 102 tasks in tasks_tmax_only200.json), then assert conversational schema.
#
# Usage:
#   TMAX_N=500 bash scripts/build_tmax_allowlist_sft_data.sh
#   TMAX_N=1000 TMAX_PER_TASK=8 bash scripts/build_tmax_allowlist_sft_data.sh
#
# Note: the 102-task allowlist has only ~618 only-success trajectories in the
# parquet; requests above that will silently cap at available after gates/dedupe.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TMAX_N="${TMAX_N:-500}"
TMAX_SEED="${TMAX_SEED:-42}"
# Raise per-task cap so N=500 is reachable on the 102-task allowlist
# (per_task=2 only yields ~200; per_task=8 can yield up to ~618).
TMAX_PER_TASK="${TMAX_PER_TASK:-8}"
MODEL_SIZE="${MODEL_SIZE:-9b}"
# TMAX_TASK_ALLOWLIST=none|off|0|"" → sample from full Tmax pool (can reach N=1000).
_ALLOW_RAW="${TMAX_TASK_ALLOWLIST:-$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json}"
case "${_ALLOW_RAW,,}" in
  none|off|0|false|no|"") USE_ALLOWLIST=0; TMAX_TASK_ALLOWLIST="" ;;
  *) USE_ALLOWLIST=1; TMAX_TASK_ALLOWLIST="$_ALLOW_RAW" ;;
esac
if [[ -z "${SFT_DATASET_NAME:-}" ]]; then
  if [[ "$USE_ALLOWLIST" == "1" ]]; then
    SFT_DATASET_NAME="qwen35_${MODEL_SIZE}_tmax_only${TMAX_N}_on102"
  else
    SFT_DATASET_NAME="qwen35_${MODEL_SIZE}_tmax_only${TMAX_N}"
  fi
fi

export TMAX_ONLY=1
export TMAX_N TMAX_SEED TMAX_PER_TASK MODEL_SIZE SFT_DATASET_NAME
export RUN_TAG="${RUN_TAG:-tmax-sft-allowlist-noop}"
if [[ "$USE_ALLOWLIST" == "1" ]]; then
  export TMAX_TASK_ALLOWLIST
else
  unset TMAX_TASK_ALLOWLIST || true
fi

bash scripts/build_sft_data.sh

DATA_DIR="$ROOT/recipe/tb2_sft/data/$SFT_DATASET_NAME"
PY="${SFT_PYTHON:-${PYTHON_BIN:-$HOME/.venv/bin/python}}"
"$PY" - "$DATA_DIR" "$TMAX_N" "${TMAX_TASK_ALLOWLIST:-}" <<'PY'
import json, sys
from pathlib import Path
data_dir = Path(sys.argv[1])
want_n = int(sys.argv[2])
allow_path = sys.argv[3].strip()
train = data_dir / "train.jsonl"
evalp = data_dir / "eval.jsonl"
assert train.is_file() and evalp.is_file(), data_dir
row = json.loads(train.open().readline())
assert isinstance(row.get("prompt"), list) and isinstance(row.get("completion"), list), row.keys()
prov = json.loads((data_dir / "tmax_provenance.json").read_text())
selected = int(prov["selected_n"])
tasks = set(prov["tasks"])
print(f"OK conversational corpus: {data_dir}")
print(f"  selected_n={selected} (requested={want_n})")
print(f"  distinct_tasks={len(tasks)}")
if allow_path:
    allow = set(json.loads(Path(allow_path).read_text())["task_ids"])
    print(f"  allowlist={len(allow)} subset={tasks <= allow}")
    if not tasks <= allow:
        raise SystemExit(f"ERROR: selected tasks not subset of allowlist: {sorted(tasks - allow)[:5]}")
else:
    print("  allowlist=OFF (full Tmax pool)")
if selected < min(want_n, prov.get("available_after_gates_and_dedupe", want_n)):
    print(f"  WARNING: selected_n < requested; parquet/gates capped the draw", flush=True)
n_train = sum(1 for _ in train.open())
n_eval = sum(1 for _ in evalp.open())
print(f"  pairs train={n_train} eval={n_eval}")
PY
