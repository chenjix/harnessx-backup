#!/usr/bin/env bash
# Submit the two 4B controls together, without a dependency between them:
#   1) harness-only, four outer iterations
#   2) tournament + mixed-sibling SFT, three outer iterations
set -euo pipefail

SETTINGS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SETTINGS_DIR/../../.." && pwd)"
cd "$ROOT"

HARNESS_REPLICATE="${HARNESS_REPLICATE:-54}"
MIXED_REPLICATE="${MIXED_REPLICATE:-55}"
PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
VLLM_VENV="${VLLM_VENV:-$(dirname "$(dirname "$PYTHON_BIN")")}"
SFT_PYTHON="${SFT_PYTHON:-$PYTHON_BIN}"
SBATCH_SCRIPT="scripts/slurm/tmax/h200_tmax_experiment.sbatch"

[[ "$HARNESS_REPLICATE" != "$MIXED_REPLICATE" ]] || {
  echo "ERROR: HARNESS_REPLICATE and MIXED_REPLICATE must differ" >&2
  exit 2
}
[[ -x "$PYTHON_BIN" ]] || { echo "ERROR: Python is not executable: $PYTHON_BIN" >&2; exit 2; }
"$PYTHON_BIN" -c 'import pandas, pyarrow' || {
  echo "ERROR: $PYTHON_BIN must provide pandas and pyarrow" >&2
  exit 2
}

for rep in "$HARNESS_REPLICATE" "$MIXED_REPLICATE"; do
  [[ ! -e "outputs/tmax_coevolve/rep${rep}" ]] || {
    echo "ERROR: outputs/tmax_coevolve/rep${rep} already exists; choose a fresh replicate" >&2
    exit 2
  }
done

submit_one() {
  local name="$1" pipeline="$2" rollout="$3" rep="$4" iters="$5"
  local -a cmd=(
    sbatch --parsable
    --job-name="$name"
    --export="ALL,MODEL_SIZE=4b,TMAX_PIPELINE=$pipeline,SFT_ROLLOUT_MODE=$rollout,REPLICATE=$rep,N_ITERS=$iters,PYTHON_BIN=$PYTHON_BIN,VLLM_VENV=$VLLM_VENV,SFT_PYTHON=$SFT_PYTHON,INIT_LORA_PATH=,ADAPTER_DIR=,RL_INIT_MODEL="
    "$SBATCH_SCRIPT"
  )
  if [[ "${DRY_RUN:-0}" == 1 ]]; then
    printf 'DRY RUN:'
    printf ' %q' "${cmd[@]}"
    printf '\n'
  else
    "${cmd[@]}"
  fi
}

echo "Submitting independent 4B jobs (Slurm may run them concurrently when quota permits)."
harness_job="$(submit_one tx-4b-harness-none harness none "$HARNESS_REPLICATE" 4)"
mixed_job="$(submit_one tx-4b-sft-cross sft cross "$MIXED_REPLICATE" 3)"

if [[ "${DRY_RUN:-0}" == 1 ]]; then
  printf '%s\n%s\n' "$harness_job" "$mixed_job"
else
  echo "harness-only: replicate=$HARNESS_REPLICATE iterations=4 job=$harness_job"
  echo "mixed-SFT  : replicate=$MIXED_REPLICATE iterations=3 job=$mixed_job"
fi
