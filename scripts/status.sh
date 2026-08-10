#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"

echo "run_tag=$RUN_TAG model=$MODEL"
state="$ROOT/recipe/tb2_evolver/runs/$RUN_TAG/_meta_v2/_meta_scratch/harness_evolve_state.json"
if [[ -f "$state" ]]; then
  "$(python_bin)" - "$state" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("evolve_status=", d.get("status"))
print("last_output_round=", d.get("last_output_round"))
print("last_output_config=", d.get("last_output_config"))
for row in d.get("history", []):
    print(
        f"R{row.get('input_round')} score={row.get('score')} "
        f"gate={row.get('gate_decision')} best={row.get('best_so_far')}"
    )
PY
else
  echo "No evolve state yet: $state"
fi

echo
"$(python_bin)" "$ROOT/recipe/tb2_sft/src/score_tb2.py" "${RUN_TAG}-r*-traj" "eval-${RUN_TAG}*"

echo
if command -v squeue >/dev/null 2>&1; then squeue --me || true; fi
