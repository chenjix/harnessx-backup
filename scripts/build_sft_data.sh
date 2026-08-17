#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"

RUN_GLOB="${SFT_RUN_GLOB:-${RUN_TAG}-r*-traj}"

# TMAX_N > 0 mixes in that many external Tmax trajectories (allenai/tmax-sft,
# only-success split) alongside this run's own successful TB2 trajectories.
# See recipe/tb2_sft/src/tmax_source.py for the conversion + gating logic.
TMAX_N="${TMAX_N:-0}"
# TMAX_ONLY=1 builds a pure external-distillation corpus: no own-model
# trajectories at all. Useful as a clean arm — with a mixed corpus the own
# trajectories are so few (5-7) next to the Tmax ones that they contribute
# <10% of training pairs anyway, which makes attribution ambiguous.
TMAX_ONLY="${TMAX_ONLY:-0}"
if [[ "$TMAX_ONLY" == "1" ]]; then
  DEFAULT_DATASET_NAME="${MODEL_TAG}_${MODEL_SIZE}_tmax_only${TMAX_N}"
else
  DEFAULT_DATASET_NAME="${MODEL_TAG}_${MODEL_SIZE}_own_success"
  [[ "$TMAX_N" -gt 0 ]] && DEFAULT_DATASET_NAME="${DEFAULT_DATASET_NAME}_plus_tmax${TMAX_N}"
fi
DATASET_NAME="${SFT_DATASET_NAME:-$DEFAULT_DATASET_NAME}"

tmax_args=()
[[ "$TMAX_ONLY" == "1" ]] && tmax_args+=(--tmax-only)
if [[ "$TMAX_N" -gt 0 ]]; then
  TMAX_PARQUET="${TMAX_PARQUET:-$ROOT/data/external/tmax-sft-only-success/skill_tax_20260505_2.2k_combined_balanced_thinking_only_success/train-00000-of-00001.parquet}"
  require_file "$TMAX_PARQUET"
  tmax_args+=(
    --tmax-parquet "$TMAX_PARQUET"
    --tmax-n "$TMAX_N"
    --tmax-seed "${TMAX_SEED:-42}"
    --tmax-per-task "${TMAX_PER_TASK:-2}"
  )
  if [[ -n "${TMAX_TASK_ALLOWLIST:-}" ]]; then
    require_file "$TMAX_TASK_ALLOWLIST"
    tmax_args+=(--tmax-task-allowlist "$TMAX_TASK_ALLOWLIST")
    echo "Restricting Tmax sample to task allowlist: $TMAX_TASK_ALLOWLIST"
  fi
  echo "Mixing in $TMAX_N external Tmax trajectories from $TMAX_PARQUET"
fi

cd "$ROOT/recipe/tb2_sft/src"
"$(python_bin)" build_model_sft.py \
  --model "$MODEL" \
  --run-glob "$RUN_GLOB" \
  --name "$DATASET_NAME" \
  --per-task "${SFT_PER_TASK:-3}" \
  --max-pairs-per-traj "${SFT_MAX_PAIRS_PER_TRAJ:-8}" \
  "${tmax_args[@]}" \
  2>&1 | tee "$LOG_ROOT/build_sft_data.log"

echo "SFT data: $ROOT/recipe/tb2_sft/data/$DATASET_NAME"
