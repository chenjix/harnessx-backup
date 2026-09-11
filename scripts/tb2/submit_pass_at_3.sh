#!/usr/bin/env bash
# Submit one allocation that sequentially runs trials 2 and 3 for base, rep22
# SFT, and rep46 RL2 on TB2.1 full-89.
# Trial 1 is the completed 2026-09-08 evaluation documented below.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

STAMP="${PASS_AT_3_STAMP:-$(date -u +%Y%m%d-%H%M%S)}"
MANIFEST="${PASS_AT_3_MANIFEST:-$ROOT/outputs/runs/tb21-pass-at-3-$STAMP.tsv}"
mkdir -p "$(dirname "$MANIFEST")"

printf 'model\ttrial\tjob_id\teval_stamp\tfirst_run\n' >"$MANIFEST"

job_id="$(sbatch --parsable --job-name=tb21-pass-at-3 \
  --export="ALL,PASS_AT_3_STAMP=${STAMP}" \
  scripts/slurm/tb2/h200_tb21_pass_at_3.sbatch)"

for trial in 2 3; do
  printf 'base\t%s\t%s\tpass3-%s-t%s\t%s\n' "$trial" "$job_id" "$STAMP" "$trial" \
    base-qwen35-9b-tb21-20260908-071605 >>"$MANIFEST"
  printf 'sft\t%s\t%s\tpass3-%s-t%s\t%s\n' "$trial" "$job_id" "$STAMP" "$trial" \
    rep22-best-tb21-native-20260908-034352 >>"$MANIFEST"
  printf 'rl\t%s\t%s\tpass3-%s-t%s\t%s\n' "$trial" "$job_id" "$STAMP" "$trial" \
    rep46-rl2-rep19h1-tb21-native-20260908-063705 >>"$MANIFEST"
done

echo "Submitted Slurm job: $job_id"
echo "Submission manifest: $MANIFEST"
echo "After all jobs finish, run:"
echo "  python3 scripts/tb2/summarize_pass_at_3.py --manifest '$MANIFEST'"
