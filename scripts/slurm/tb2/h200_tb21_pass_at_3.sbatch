#!/bin/bash
# One 8xH200 allocation, six sequential TB2.1 full-89 evaluations.
#SBATCH --job-name=tb21-pass-at-3
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=1-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tb21_pass_at_3_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tb21_pass_at_3_%j.err

set -Euo pipefail
REPO="${REPO:-/fsx/home/jixuan.chen/harnessx-backup}"
STAMP="${PASS_AT_3_STAMP:?PASS_AT_3_STAMP is required}"
cd "$REPO"
mkdir -p /fsx/home/jixuan.chen/logs

failures=0
run_one() {
  local model="$1" trial="$2" launcher="$3"
  echo "===== $(date -u +%FT%TZ) model=$model trial=$trial ====="
  if EVAL_STAMP="pass3-${STAMP}-t${trial}" \
      TARGET=tb21 TB21_HARNESS_MODE=native bash "$launcher"; then
    echo "===== PASS model=$model trial=$trial ====="
  else
    rc=$?
    echo "===== FAIL model=$model trial=$trial rc=$rc; continuing =====" >&2
    failures=$((failures + 1))
  fi
}

for trial in 2 3; do
  run_one base "$trial" "$REPO/scripts/eval_base9b_tb21.sh"
  run_one sft "$trial" "$REPO/scripts/eval_rep22_best.sh"
  run_one rl "$trial" "$REPO/scripts/eval_rep46_rl_best_tb21.sh"
done

if (( failures > 0 )); then
  echo "ERROR: $failures of 6 evaluations failed" >&2
  exit 1
fi
echo "All six evaluations completed."
