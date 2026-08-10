#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"

# Launch one vLLM replica per GPU in $GPU_POOL (comma-separated physical GPU
# indices, e.g. GPU_POOL=0,1,2,3,4,5,6,7), all serving the SAME model, on
# sequential ports starting at $POOL_BASE_PORT. Every replica is probed for a
# real qwen3_xml tool-call response (same guard as with_server.sh) before it
# is considered ready. Only endpoints that pass are written to
# $ENDPOINTS_FILE, so a round can still proceed on N-1 endpoints if one
# replica fails to come up, instead of the whole pool refusing to start.

IFS=',' read -r -a GPUS <<< "${GPU_POOL:?GPU_POOL is required, e.g. GPU_POOL=0,1,2,3,4,5,6,7}"
BASE_PORT="${POOL_BASE_PORT:-8300}"
ENDPOINTS_FILE="${ENDPOINTS_FILE:?ENDPOINTS_FILE is required}"

PY="$(python_bin)"
VENV_BIN="$(dirname "$PY")"
export PATH="$VENV_BIN:$PATH"

LORA_ARGS=()
SERVED_MODEL="$MODEL"
if [[ -n "${LORA_PATH:-}" ]]; then
  LORA_NAME="${LORA_NAME:-qwen35-${MODEL_SIZE}-sft}"
  LORA_ARGS=(--enable-lora --max-lora-rank 32 --lora-modules "$LORA_NAME=$LORA_PATH")
  SERVED_MODEL="$LORA_NAME"
fi

declare -a PIDS=()
declare -a PORTS=()
declare -a LOGS=()

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in "${PIDS[@]:-}"; do
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

for i in "${!GPUS[@]}"; do
  gpu="${GPUS[$i]}"
  port=$((BASE_PORT + i))
  log="$LOG_ROOT/vllm-pool-gpu${gpu}.log"
  PORTS+=("$port")
  LOGS+=("$log")

  if curl -fsS --max-time 2 "http://127.0.0.1:$port/v1/models" >/dev/null 2>&1; then
    echo "ERROR: port $port already has an OpenAI endpoint; refusing to reuse it." >&2
    exit 1
  fi

  echo "[serve_pool] launching GPU=$gpu PORT=$port model=$MODEL${LORA_PATH:+ (+lora $LORA_NAME)}"
  (
    export CUDA_VISIBLE_DEVICES="$gpu"
    export SAGEMAKER_MODEL_PATH="${SAGEMAKER_MODEL_PATH:-/tmp/${USER:-user}_sm_model_empty}"
    mkdir -p "$SAGEMAKER_MODEL_PATH"
    exec "$PY" -m vllm.entrypoints.openai.api_server \
      --model "$MODEL" \
      --served-model-name "$MODEL" \
      "${LORA_ARGS[@]}" \
      --host 127.0.0.1 \
      --port "$port" \
      --max-model-len "${MAX_MODEL_LEN:-32768}" \
      --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.90}" \
      --dtype auto \
      --enable-auto-tool-choice \
      --tool-call-parser qwen3_xml \
      --trust-remote-code
  ) >"$log" 2>&1 &
  PIDS+=("$!")
done

echo "[serve_pool] waiting for ${#PORTS[@]} server(s) to become ready..."
ready_endpoints=()
for i in "${!PORTS[@]}"; do
  port="${PORTS[$i]}"
  gpu="${GPUS[$i]}"
  log="${LOGS[$i]}"
  pid="${PIDS[$i]}"
  ok=0
  for _ in $(seq 1 240); do
    if curl -fsS --max-time 2 "http://127.0.0.1:$port/v1/models" >/dev/null 2>&1; then
      ok=1
      break
    fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 5
  done
  if [[ "$ok" != 1 ]]; then
    echo "WARN: vLLM on GPU=$gpu PORT=$port failed to become ready; see $log (skipping this replica)" >&2
    tail -40 "$log" >&2 || true
    continue
  fi

  models="$(curl -fsS --max-time 5 "http://127.0.0.1:$port/v1/models")"
  if ! grep -Fq "\"$SERVED_MODEL\"" <<<"$models"; then
    echo "WARN: GPU=$gpu PORT=$port does not expose '$SERVED_MODEL': $models (skipping)" >&2
    continue
  fi

  probe="$(
    curl -fsS --max-time 120 "http://127.0.0.1:$port/v1/chat/completions" \
      -H 'Content-Type: application/json' \
      -d '{
        "model": "'"$SERVED_MODEL"'",
        "messages": [{"role":"user","content":"List files in /app using Bash."}],
        "tools": [{"type":"function","function":{"name":"Bash","description":"Execute a shell command","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}}],
        "max_tokens": 200,
        "temperature": 0
      }' |
      "$PY" -c 'import json,sys; d=json.load(sys.stdin); print("OK" if d["choices"][0]["message"].get("tool_calls") else "NO_TOOL_CALLS")'
  )"
  if [[ "$probe" != "OK" ]]; then
    echo "WARN: qwen3_xml tool-call probe failed on GPU=$gpu PORT=$port ($probe); skipping this replica." >&2
    continue
  fi
  echo "[serve_pool] GPU=$gpu PORT=$port ready + probe OK (model=$SERVED_MODEL)"
  ready_endpoints+=("\"http://127.0.0.1:$port/v1\"")
done

if [[ "${#ready_endpoints[@]}" -eq 0 ]]; then
  echo "ERROR: no replica became ready; see per-GPU logs under $LOG_ROOT" >&2
  exit 1
fi

printf '[%s]\n' "$(IFS=,; echo "${ready_endpoints[*]}")" > "$ENDPOINTS_FILE"
echo "[serve_pool] wrote ${#ready_endpoints[@]}/${#GPUS[@]} endpoint(s) -> $ENDPOINTS_FILE"

# This process holds the server PIDs and the cleanup trap; keep it alive
# until the parent (with_server_pool.sh) kills it.
wait
