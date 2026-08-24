#!/usr/bin/env bash
# Materialize an `eval_task_set_with_envs.jsonl` for a task-id list from the
# 2.2k Tmax taxonomy parquet.
#
# `recipe/tb2_sft/data/` is gitignored, so a fresh clone has no env jsonl and
# every Tmax eval / evolve stage refuses to start. The taxonomy parquet carries
# container_def + description + test_{initial,final}_state, which is exactly
# what `recipe/tmax_eval/run_eval.py` reads, so both standard sets can be
# regenerated instead of hunted for.
#
#   # holdout-102 (evaluation yardstick — required by every stage)
#   IDS_JSON=recipe/tb2_evolver/tasks_tmax_only200.json \
#   OUT_DIR=recipe/tb2_sft/data/qwen35_9b_tmax_only200 \
#     bash scripts/tmax/build_tmax_envs_from_taxonomy.sh
#
#   # evolve-50 (iteration-1 set; later iterations generate their own)
#   IDS_JSON=recipe/tb2_evolver/tasks_tmax_evolve50_list.json \
#   OUT_DIR=recipe/tb2_sft/data/tmax_evolve50 \
#     bash scripts/tmax/build_tmax_envs_from_taxonomy.sh

set -euo pipefail
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"

IDS_JSON="${IDS_JSON:?set IDS_JSON=path to a task-id list json}"
OUT_DIR="${OUT_DIR:?set OUT_DIR=recipe/tb2_sft/data/<name>}"
TAXONOMY_PARQUET="${TAXONOMY_PARQUET:-$ROOT/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"
POOL_ENVS_JSONL="${POOL_ENVS_JSONL:-}"
PY="${PYTHON_BIN:-${VLLM_VENV:-$HOME/.venv}/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3)"

IDS_JSON="$IDS_JSON" OUT_DIR="$OUT_DIR" TAXONOMY_PARQUET="$TAXONOMY_PARQUET" \
POOL_ENVS_JSONL="$POOL_ENVS_JSONL" PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" "$PY" - <<'PY'
import json, os, sys
from pathlib import Path

from build_tmax_evolve_task_set import load_pool  # noqa: E402
from tmax_mastery import load_task_ids  # noqa: E402

ids_json = Path(os.environ["IDS_JSON"])
out_dir = Path(os.environ["OUT_DIR"])
parquet = Path(os.environ["TAXONOMY_PARQUET"])
extra = [Path(p) for p in os.environ.get("POOL_ENVS_JSONL", "").split() if p]

ids = load_task_ids(ids_json)
if not ids:
    raise SystemExit(f"ERROR: no task ids in {ids_json}")
pool, rejects = load_pool(taxonomy_parquet=parquet, pool_envs=extra)
if not pool:
    raise SystemExit(f"ERROR: no usable rows in {parquet} (rejects={rejects})")

missing = [t for t in ids if t not in pool]
rows = [pool[t] for t in ids if t in pool]
out_dir.mkdir(parents=True, exist_ok=True)
envs = out_dir / "eval_task_set_with_envs.jsonl"
with envs.open("w", encoding="utf-8") as fh:
    for row in rows:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
(out_dir / "envs_provenance.json").write_text(
    json.dumps(
        {
            "ids_json": str(ids_json),
            "taxonomy_parquet": str(parquet),
            "extra_pool_envs": [str(p) for p in extra],
            "n_requested": len(ids),
            "n_written": len(rows),
            "missing_task_ids": missing,
            "pool_rejects": rejects,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
print(f"wrote {envs}: {len(rows)}/{len(ids)} task env row(s)")
if missing:
    print(
        f"WARNING: {len(missing)} id(s) not in the pool, e.g. {missing[:5]} — "
        "the eval will run on fewer tasks than the list names, which shifts the "
        "denominator of every holdout score.",
        file=sys.stderr,
    )
    sys.exit(1)
PY
