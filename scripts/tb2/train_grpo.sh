#!/usr/bin/env bash
set -euo pipefail
# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
source "$_HX_SCRIPTS/_common.sh"

if [[ "${ALLOW_OFFLINE_TB2_REPLAY_GRPO:-0}" != "1" ]]; then
  cat >&2 <<'EOF'
Refusing to start by default.

This repository's current TB2 GRPO path is OFFLINE replay GRPO:
  - prompts come from completed TB2 episodes;
  - Bash executes in the HarnessX training process, not in a fresh Harbor task container;
  - reward is behavioral (valid Bash use + final output), not the TB2 verifier.

Set ALLOW_OFFLINE_TB2_REPLAY_GRPO=1 only if that is the intended experiment.
EOF
  exit 2
fi

require_var SLIME_ROOT
require_var MEGATRON_ROOT
require_var DATA_ROOT

upper="${MODEL_SIZE^^}"
model_args_var="MODEL_ARGS_SCRIPT_${upper}"
hf_var="HF_CHECKPOINT_${upper}"
ref_var="REF_LOAD_${upper}"
MODEL_ARGS_SCRIPT="${MODEL_ARGS_SCRIPT:-${!model_args_var:-}}"
HF_CHECKPOINT="${HF_CHECKPOINT:-${!hf_var:-}}"
REF_LOAD="${REF_LOAD:-${!ref_var:-}}"
require_var MODEL_ARGS_SCRIPT
require_var HF_CHECKPOINT
require_var REF_LOAD

PROMPT_DATA="${PROMPT_DATA:-$ROOT/data/grpo/${RUN_TAG}.jsonl}"
[[ -s "$PROMPT_DATA" ]] || {
  echo "ERROR: replay buffer missing: $PROMPT_DATA (run scripts/build_grpo_replay.sh)" >&2
  exit 2
}

export HX_ROOT="$ROOT"
export PROMPT_DATA MODEL_ARGS_SCRIPT HF_CHECKPOINT REF_LOAD
export SAVE_CKPT="${SAVE_CKPT:-$ROOT/checkpoints/grpo/${RUN_TAG}-${MODEL_SIZE}}"
export HARNESSX_SLIME_TASK_TYPE=tb2_replay
export ROLLOUT_BATCH_SIZE="${ROLLOUT_BATCH_SIZE:-${GRPO_ROLLOUT_BATCH_SIZE:-32}}"
export SAMPLES_PER_PROMPT="${SAMPLES_PER_PROMPT:-${GRPO_SAMPLES_PER_PROMPT:-8}}"

echo "Starting OFFLINE TB2 replay GRPO: model=$MODEL prompts=$PROMPT_DATA"
bash "$ROOT/recipe/slime/launch/run_tb2_replay_grpo.sh" \
  2>&1 | tee "$LOG_ROOT/train_grpo.log"
