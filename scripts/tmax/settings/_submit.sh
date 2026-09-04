#!/usr/bin/env bash
# Shared implementation for the six documented Tmax experiment settings.
set -euo pipefail

SETTINGS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SETTINGS_DIR/../../.." && pwd)"
cd "$ROOT"

: "${TMAX_SETTING:?TMAX_SETTING must be set by a public setting script}"
: "${REPLICATE:?set a fresh REPLICATE number (see outputs/tmax_coevolve/)}"
N_ITERS="${N_ITERS:-3}"

case "$TMAX_SETTING" in
  9b_sft_single) model=9b; pipeline=sft; rollout=single ;;
  4b_sft_single) model=4b; pipeline=sft; rollout=single ;;
  9b_sft_cross)  model=9b; pipeline=sft; rollout=cross ;;
  4b_sft_cross)  model=4b; pipeline=sft; rollout=cross ;;
  9b_rl)         model=9b; pipeline=rl;  rollout=none ;;
  4b_rl)         model=4b; pipeline=rl;  rollout=none ;;
  *) echo "ERROR: unknown TMAX_SETTING=$TMAX_SETTING" >&2; exit 2 ;;
esac

[[ ! -e "outputs/tmax_coevolve/rep${REPLICATE}" || "${ALLOW_RESUME:-0}" == "1" ]] || {
  echo "ERROR: outputs/tmax_coevolve/rep${REPLICATE} already exists." >&2
  echo "Use a fresh REPLICATE, or set ALLOW_RESUME=1 to resume it deliberately." >&2
  exit 2
}

echo "Submitting Tmax setting=$TMAX_SETTING model=$model pipeline=$pipeline rollout=$rollout"
echo "replicate=$REPLICATE iterations=$N_ITERS"
submit_cmd=(
  sbatch
  --job-name="tx-${model}-${pipeline}-${rollout}"
  --export="ALL,MODEL_SIZE=$model,TMAX_PIPELINE=$pipeline,SFT_ROLLOUT_MODE=$rollout,REPLICATE=$REPLICATE,N_ITERS=$N_ITERS,INIT_LORA_PATH=,ADAPTER_DIR=,RL_INIT_MODEL="
  scripts/slurm/tmax/h200_tmax_experiment.sbatch
)
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  printf 'DRY RUN:'
  printf ' %q' "${submit_cmd[@]}"
  printf '\n'
  exit 0
fi
exec "${submit_cmd[@]}"
