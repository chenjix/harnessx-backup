#!/usr/bin/env bash
# Rebuild qwen35_9b_tmax_only200 with conversational prompt/completion columns
# (required after the Tmax-aligned SFT data/train changes).
#
#   bash scripts/rebuild_tmax_only200_sft_data.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TMAX_ONLY=1 TMAX_N=200 TMAX_SEED=42 TMAX_PER_TASK=2 \
MODEL_SIZE=9b \
SFT_DATASET_NAME=qwen35_9b_tmax_only200 \
RUN_TAG=tmax-sft-rebuild-noop \
  bash scripts/build_sft_data.sh

python3 - <<'PY'
import json
from pathlib import Path
p = Path("recipe/tb2_sft/data/qwen35_9b_tmax_only200/train.jsonl")
row = json.loads(p.open().readline())
assert isinstance(row.get("prompt"), list), row.keys()
assert isinstance(row.get("completion"), list), row.keys()
print("OK conversational train.jsonl:", p)
print("  prompt turns:", len(row["prompt"]), "completion turns:", len(row["completion"]))
PY
