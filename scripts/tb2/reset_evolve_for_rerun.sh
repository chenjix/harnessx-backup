#!/usr/bin/env bash
set -euo pipefail
# Discard the evolve artifacts of a run that was produced under buggy code,
# while KEEPING its clean pre-evolve baseline eval (which is expensive to
# reproduce and is reused as R0).
#
# Usage: bash scripts/reset_evolve_for_rerun.sh <RUN_TAG> [--yes]

# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
RUN_TAG="${1:?usage: reset_evolve_for_rerun.sh <RUN_TAG> [--yes]}"
CONFIRM="${2:-}"

BASE_EVAL="$ROOT/.benchmarks/tb2/eval-base-preevolve-${RUN_TAG}"
STATE_DIR="$ROOT/recipe/tb2_evolver/runs/${RUN_TAG}"
mapfile -t ROUND_DIRS < <(ls -d "$ROOT/.benchmarks/tb2/${RUN_TAG}"-r*-traj 2>/dev/null || true)

echo "Run tag: $RUN_TAG"
echo
echo "WILL DELETE:"
[[ -d "$STATE_DIR" ]] && echo "  $STATE_DIR" || echo "  (no evolve state dir)"
if ((${#ROUND_DIRS[@]})); then
  printf '  %s\n' "${ROUND_DIRS[@]}"
else
  echo "  (no round trajectory dirs)"
fi
echo
echo "WILL KEEP:"
if [[ -d "$BASE_EVAL" ]]; then
  n_res=$(find "$BASE_EVAL" -mindepth 2 -maxdepth 2 -name result.json | wc -l)
  echo "  $BASE_EVAL  ($n_res task result(s) — reused as R0)"
else
  echo "  WARNING: no baseline eval found at $BASE_EVAL"
  echo "           evolve will have to run its own R0 rollout from scratch."
fi
echo

if [[ "$CONFIRM" != "--yes" ]]; then
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Aborted."; exit 1; }
fi

[[ -d "$STATE_DIR" ]] && rm -rf "$STATE_DIR" && echo "removed $STATE_DIR"
for d in "${ROUND_DIRS[@]:-}"; do
  [[ -n "$d" && -d "$d" ]] || continue
  rm -rf "$d"
  echo "removed $d"
done
echo
echo "Done. Baseline eval preserved; the next evolve run will adopt it as R0."
