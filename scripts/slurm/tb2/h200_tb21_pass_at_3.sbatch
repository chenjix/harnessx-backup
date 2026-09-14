#!/bin/bash
# One 8xH200 allocation, six sequential TB2.1 full-89 evaluations.
#SBATCH --job-name=tb21-pass-at-3
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=3-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tb21_pass_at_3_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tb21_pass_at_3_%j.err

set -Euo pipefail
REPO="${REPO:-/fsx/home/jixuan.chen/harnessx-backup}"
STAMP="${PASS_AT_3_STAMP:?PASS_AT_3_STAMP is required}"
cd "$REPO"
mkdir -p /fsx/home/jixuan.chen/logs

# Run-tag prefixes must stay in sync with PREFIX in scripts/tb2/summarize_pass_at_3.py.
declare -A RUN_PREFIX=(
  [base]="base-qwen35-9b-tb21-"
  [sft]="rep22-best-tb21-native-"
  [rl]="rep46-rl2-rep19h1-tb21-native-"
)

failures=0
run_one() {
  local model="$1" trial="$2" launcher="$3"
  local eval_stamp="pass3-${STAMP}-t${trial}"
  local run_dir="$REPO/.benchmarks/tb2/${RUN_PREFIX[$model]}${eval_stamp}"
  local done_count=0

  # This job is --requeue-able and may be resubmitted with the same stamp after a
  # timeout, so skip any evaluation whose 89 results are already on disk instead
  # of redoing it (and colliding with the existing run directory).
  if [[ -d "$run_dir" ]]; then
    done_count="$(find "$run_dir" -mindepth 2 -maxdepth 2 -name result.json | wc -l)"
    if (( done_count == 89 )); then
      echo "===== SKIP model=$model trial=$trial (89 results already in $run_dir) ====="
      return 0
    fi
    echo "===== WARN model=$model trial=$trial: $run_dir exists with $done_count/89 results; re-running =====" >&2
  fi

  echo "===== $(date -u +%FT%TZ) model=$model trial=$trial ====="
  if EVAL_STAMP="$eval_stamp" \
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
