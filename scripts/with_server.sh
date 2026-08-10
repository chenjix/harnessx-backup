#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"

[[ "${1:-}" == "--" ]] || { echo "usage: with_server.sh -- COMMAND..." >&2; exit 2; }
shift
[[ $# -gt 0 ]] || { echo "ERROR: command required after --" >&2; exit 2; }

PORT="${PORT:-8200}"
SERVED_MODEL="$MODEL"
[[ -n "${LORA_PATH:-}" ]] && SERVED_MODEL="${LORA_NAME:-qwen35-${MODEL_SIZE}-sft}"
SERVER_LOG="${SERVER_LOG:-$LOG_ROOT/vllm.log}"
mkdir -p "$(dirname "$SERVER_LOG")"

bash "$ROOT/scripts/serve.sh" >"$SERVER_LOG" 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Waiting for vLLM (GPU=${GPU:-0} PORT=$PORT, model=$MODEL) to become ready; live progress: tail -f $SERVER_LOG"
ready=0
for i in $(seq 1 240); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
    ready=1
    break
  fi
  kill -0 "$server_pid" 2>/dev/null || break
  if (( i % 12 == 0 )); then
    echo "  ... still waiting on vLLM ($((i * 5))s elapsed; tail -f $SERVER_LOG for detail)"
  fi
  sleep 5
done
if [[ "$ready" != 1 ]]; then
  echo "ERROR: vLLM failed to become ready; see $SERVER_LOG" >&2
  tail -40 "$SERVER_LOG" >&2 || true
  exit 1
fi

models="$(curl -fsS --max-time 5 "http://127.0.0.1:$PORT/v1/models")"
grep -Fq "\"$SERVED_MODEL\"" <<<"$models" || {
  echo "ERROR: endpoint does not expose requested model '$SERVED_MODEL': $models" >&2
  exit 1
}

probe="$(
  curl -fsS --max-time 120 "http://127.0.0.1:$PORT/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d '{
      "model": "'"$SERVED_MODEL"'",
      "messages": [{"role":"user","content":"List files in /app using Bash."}],
      "tools": [{"type":"function","function":{"name":"Bash","description":"Execute a shell command","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}}],
      "max_tokens": 200,
      "temperature": 0
    }' |
    "$(python_bin)" -c 'import json,sys; d=json.load(sys.stdin); print("OK" if d["choices"][0]["message"].get("tool_calls") else "NO_TOOL_CALLS")'
)"
[[ "$probe" == "OK" ]] || {
  echo "ERROR: qwen3_xml tool-call probe failed ($probe); refusing to run." >&2
  exit 1
}

export TB2_API_BASE="http://127.0.0.1:$PORT/v1"
export TB2_MODEL="$SERVED_MODEL"
echo "vLLM ready: model=$SERVED_MODEL port=$PORT parser=qwen3_xml"
"$@"
