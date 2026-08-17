#!/usr/bin/env bash
set -Eeuo pipefail
# Move one loop3 iteration's artifacts aside so it can be re-run clean.
#
# Moves rather than deletes: the contaminated run still has forensic value (it is
# the evidence that two chains interleaved), a rename on the same filesystem is
# instant, and an accidental over-broad match is recoverable. Reclaim the space
# with `rm -rf outputs/loop3/_quarantine/<stamp>` once the re-run has landed.
#
# Usage:  bash scripts/quarantine_loop3_iter.sh <replicate> <iter> [stamp]

# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"

REP="${1:?usage: quarantine_loop3_iter.sh <replicate> <iter> [stamp]}"
ITER="${2:?usage: quarantine_loop3_iter.sh <replicate> <iter> [stamp]}"
STAMP="${3:-$(date +%Y%m%d-%H%M%S)}"

STATE="$ROOT/outputs/loop3/rep${REP}"
Q="$ROOT/outputs/loop3/_quarantine/${STAMP}/rep${REP}-i${ITER}"

if [[ -e "$STATE/.lock" ]] && command -v flock >/dev/null && ! flock -n "$STATE/.lock" true; then
  echo "ERROR: rep${REP} is still running (lock held). Stop it before quarantining." >&2
  exit 2
fi

mkdir -p "$Q"
moved=0
mv_if() {  # mv_if <path> <subdir>
  [[ -e "$1" ]] || return 0
  mkdir -p "$Q/$2"
  mv "$1" "$Q/$2/"
  echo "  moved $(basename "$1")"
  moved=$((moved + 1))
}

echo "quarantining rep${REP} iteration ${ITER} -> $Q"

mv_if "$ROOT/recipe/tb2_evolver/runs/loop3-rep${REP}-i${ITER}" evolve_run
for d in "$ROOT"/.benchmarks/tb2/loop3-rep${REP}-i${ITER}-*; do mv_if "$d" benchmarks; done
mv_if "$ROOT/recipe/tb2_evolver/fault_routing_loop3_rep${REP}_i${ITER}" routing
for d in "$ROOT"/recipe/tb2_sft/data/loop3_rep${REP}_i${ITER}*; do mv_if "$d" sft_data; done
mv_if "$ROOT/outputs/sft/loop3_rep${REP}_i${ITER}" adapter

# Stage markers for this iteration only, so an earlier completed iteration is
# left intact and its stages are still skipped on the re-run.
shopt -s nullglob
for m in "$STATE"/.done-*"i${ITER}"*; do mv_if "$m" markers; done
shopt -u nullglob

# Score/timing rows for this iteration. Rewritten in place rather than deleted:
# the header and any other iteration's rows must survive.
for f in "$STATE/scores.tsv" "$STATE/timings.tsv"; do
  [[ -f "$f" ]] || continue
  cp "$f" "$Q/$(basename "$f").orig"
  awk -v it="$ITER" 'NR==1 || $1 != it' "$f" >"$f.tmp" && mv "$f.tmp" "$f"
  echo "  pruned iteration $ITER rows from $(basename "$f")"
done

rm -f "$STATE/STATUS"
echo "done — $moved path(s) moved. Re-run with REPLICATE=$REP to redo iteration $ITER."
