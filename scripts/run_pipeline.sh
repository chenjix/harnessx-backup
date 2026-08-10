#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_SIZE="${1:-${MODEL_SIZE:-4b}}"
STAGES="${2:-evolve,sft,eval-sft}"
case "$MODEL_SIZE" in 4b|9b) ;; *) echo "usage: run_pipeline.sh {4b|9b} [stages]" >&2; exit 2 ;; esac

set -a
# shellcheck disable=SC1090
source "$ROOT/configs/models/$MODEL_SIZE.env"
set +a
export MODEL_SIZE
export RUN_TAG="${RUN_TAG:-tb2-qwen35-${MODEL_SIZE}-$(date +%Y%m%d-%H%M%S)}"

IFS=',' read -r -a stage_list <<<"$STAGES"
for stage in "${stage_list[@]}"; do
  echo
  echo "========== stage=$stage model=$MODEL_SIZE run=$RUN_TAG =========="
  case "$stage" in
    doctor) bash "$ROOT/scripts/doctor.sh" ;;
    evolve) bash "$ROOT/scripts/evolve.sh" ;;
    build-sft) bash "$ROOT/scripts/build_sft_data.sh" ;;
    train-sft) bash "$ROOT/scripts/train_sft.sh" ;;
    sft)
      bash "$ROOT/scripts/build_sft_data.sh"
      bash "$ROOT/scripts/train_sft.sh"
      ;;
    eval-base) EVAL_SFT=0 bash "$ROOT/scripts/evaluate.sh" ;;
    eval-sft) EVAL_SFT=1 bash "$ROOT/scripts/evaluate.sh" ;;
    build-grpo) bash "$ROOT/scripts/build_grpo_replay.sh" ;;
    train-grpo) bash "$ROOT/scripts/train_grpo.sh" ;;
    grpo)
      bash "$ROOT/scripts/build_grpo_replay.sh"
      bash "$ROOT/scripts/train_grpo.sh"
      ;;
    *) echo "ERROR: unknown stage '$stage'" >&2; exit 2 ;;
  esac
done

echo "Pipeline stages completed: $STAGES"
echo "Run tag: $RUN_TAG"
