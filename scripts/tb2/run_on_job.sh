#!/usr/bin/env bash
set -euo pipefail

JOB_ID="${1:?usage: run_on_job.sh JOB_ID {4b|9b} [stages]}"
MODEL_SIZE="${2:-4b}"
STAGES="${3:-evolve,sft,eval-sft}"
# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"

command -v srun >/dev/null 2>&1 || { echo "ERROR: srun not found" >&2; exit 2; }
printf -v remote_cmd 'cd %q && bash scripts/run_pipeline.sh %q %q' "$ROOT" "$MODEL_SIZE" "$STAGES"

echo "Launching on allocation $JOB_ID: $remote_cmd"
exec srun \
  --jobid="$JOB_ID" \
  --overlap \
  --nodes=1 \
  --ntasks=1 \
  bash -lc "$remote_cmd"
