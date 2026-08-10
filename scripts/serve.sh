#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"

GPU="${GPU:-0}"
PORT="${PORT:-8200}"
PY="$(python_bin)"
VENV_BIN="$(dirname "$PY")"
export PATH="$VENV_BIN:$PATH"
export CUDA_VISIBLE_DEVICES="$GPU"
export SAGEMAKER_MODEL_PATH="${SAGEMAKER_MODEL_PATH:-/tmp/${USER:-user}_sm_model_empty}"
mkdir -p "$SAGEMAKER_MODEL_PATH"

if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
  echo "ERROR: port $PORT already has an OpenAI endpoint; refusing to reuse it." >&2
  curl -fsS --max-time 2 "http://127.0.0.1:$PORT/v1/models" || true
  exit 1
fi

LORA_ARGS=()
SERVED_MODEL="$MODEL"
if [[ -n "${LORA_PATH:-}" ]]; then
  LORA_NAME="${LORA_NAME:-qwen35-${MODEL_SIZE}-sft}"
  LORA_ARGS=(--enable-lora --max-lora-rank 32 --lora-modules "$LORA_NAME=$LORA_PATH")
  SERVED_MODEL="$LORA_NAME"
fi
export SERVED_MODEL

exec "$PY" -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --served-model-name "$MODEL" \
  "${LORA_ARGS[@]}" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --max-model-len "${MAX_MODEL_LEN:-32768}" \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.90}" \
  --dtype auto \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml \
  --trust-remote-code
