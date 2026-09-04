#!/usr/bin/env bash
# Build a LoRA SFT corpus from 9B coevolve rep19 *iter1 winner harness* rollouts,
# using the new filter (winner-only + system prompt + hard-trace quality).
#
# The holdout-accepted iter1 harness is
#   recipe/tb2_evolver/runs/tmax-coev-rep19-i1/R2/config.yaml
# Traj dirs are taken from that run's evolve state (no full-bench glob).
# Later rep19 tags are scanned too so any rollout that used the *same* YAML
# (e.g. i2 R0 seeded from this harness) is kept.
#
# Does not train. GPU node not required.
#
#   bash scripts/tmax/build_sft_rep19_i1_winner.sh
set -Eeuo pipefail

ROOT=/fsx/home/jixuan.chen/harnessx-backup
PY="${SFT_PYTHON:-${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}}"
NAME="${SFT_DATASET_NAME:-tmax_rep19_i1_winner}"
HARNESS="${EVAL_HARNESS:-$ROOT/recipe/tb2_evolver/runs/tmax-coev-rep19-i1/R2/config.yaml}"
TAGS="${RUN_TAGS:-tmax-coev-rep19-i1 tmax-coev-rep19-i2 tmax-coev-rep19-i3}"
PER_TASK="${PER_TASK:-2}"
MAX_PAIRS="${MAX_PAIRS_PER_TRAJ:-32}"
MIN_TRAJS="${MIN_TRAJS:-10}"
EXCLUDE="${HOLDOUT_TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json}"
RUNS_ROOT="$ROOT/recipe/tb2_evolver/runs"

cd "$ROOT"
[[ -x "$PY" ]] || { echo "ERROR: python not executable: $PY" >&2; exit 2; }
[[ -f "$HARNESS" ]] || { echo "ERROR: eval harness missing: $HARNESS" >&2; exit 2; }

mapfile -t TRAJ_DIRS < <("$PY" - "$RUNS_ROOT" $TAGS <<'PY'
import json, sys
from pathlib import Path
runs = Path(sys.argv[1])
seen: set[Path] = set()
for tag in sys.argv[2:]:
    for rel in (
        Path("_meta_v2") / "_meta_scratch" / "harness_evolve_state.json",
        Path("_meta") / "_meta_scratch" / "harness_evolve_state.json",
    ):
        p = runs / tag / rel
        if p.is_file():
            break
    else:
        continue
    try:
        state = json.loads(p.read_text())
    except Exception:
        continue
    for rec in state.get("history") or []:
        if not isinstance(rec, dict):
            continue
        for key in ("trajectories_dir",):
            t = rec.get(key)
            if t:
                tp = Path(t)
                if tp.is_dir() and tp not in seen:
                    seen.add(tp)
                    print(tp)
        for row in rec.get("tournament_last_round") or []:
            if not isinstance(row, dict):
                continue
            t = row.get("trajectories")
            if t:
                tp = Path(t)
                if tp.is_dir() and tp not in seen:
                    seen.add(tp)
                    print(tp)
PY
)

if (( ${#TRAJ_DIRS[@]} == 0 )); then
  echo "ERROR: no traj dirs found in evolve state for: $TAGS" >&2
  exit 2
fi

echo "eval_harness: $HARNESS"
echo "dataset     : $NAME"
echo "per_task    : $PER_TASK  pairs=$MAX_PAIRS  min_trajs=$MIN_TRAJS"
echo "traj dirs   : ${#TRAJ_DIRS[@]}"
for d in "${TRAJ_DIRS[@]}"; do
  echo "  - $(basename "$d")"
done

args=(
  --name "$NAME"
  --eval-harness "$HARNESS"
  --winner-only
  --evolve-runs-root "$RUNS_ROOT"
  --per-task "$PER_TASK"
  --max-pairs-per-traj "$MAX_PAIRS"
  --max-trajs 0
  --min-trajs "$MIN_TRAJS"
  --exclude-tasks "$EXCLUDE"
)
for d in "${TRAJ_DIRS[@]}"; do
  args+=(--traj-dir "$d")
done

PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" \
  "$PY" -m recipe.tb2_sft.src.build_tmax_evolve_sft "${args[@]}"

sum="$ROOT/recipe/tb2_sft/data/$NAME/summary.json"
[[ -f "$sum" ]] || { echo "ERROR: missing $sum" >&2; exit 2; }
"$PY" - "$sum" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
print(
    f"selected_trajs={s.get('n_selected_trajs')} "
    f"tasks={s.get('n_unique_tasks')} "
    f"train_pairs={s.get('n_train_pairs')} "
    f"eval_pairs={s.get('n_eval_pairs')} "
    f"dirs_kept={s.get('n_traj_dirs_kept')}/{s.get('n_traj_dirs_discovered')} "
    f"sys_chars={s.get('system_prompt_chars')}"
)
print("kept dirs:")
for d in s.get("traj_dirs") or []:
    print(" ", d.rsplit("/", 1)[-1])
print("dropped dirs:")
for d in s.get("traj_dirs_dropped") or []:
    print(" ", d.rsplit("/", 1)[-1])
PY
