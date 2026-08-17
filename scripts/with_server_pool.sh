#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"

# Multi-GPU counterpart to with_server.sh: launches one vLLM replica per GPU
# in $GPU_POOL via serve_pool.sh, waits for the replica pool to publish its
# ready endpoints, then runs COMMAND with TB2_ENDPOINTS_FILE (and, for
# backward compatibility, TB2_API_BASE pointed at the first ready replica)
# exported.
#
# Usage:
#   GPU_POOL=0,1,2,3,4,5,6,7 bash scripts/with_server_pool.sh -- COMMAND...

[[ "${1:-}" == "--" ]] || { echo "usage: with_server_pool.sh -- COMMAND..." >&2; exit 2; }
shift
[[ $# -gt 0 ]] || { echo "ERROR: command required after --" >&2; exit 2; }

: "${GPU_POOL:?GPU_POOL is required, e.g. GPU_POOL=0,1,2,3,4,5,6,7}"
ENDPOINTS_FILE="${ENDPOINTS_FILE:-$RUN_ROOT/endpoints.json}"
export ENDPOINTS_FILE
SERVER_LOG="${SERVER_LOG:-$LOG_ROOT/vllm_pool.log}"
mkdir -p "$(dirname "$SERVER_LOG")" "$(dirname "$ENDPOINTS_FILE")"
rm -f "$ENDPOINTS_FILE"

bash "$ROOT/scripts/serve_pool.sh" >"$SERVER_LOG" 2>&1 &
pool_pid=$!
cleanup() {
  kill "$pool_pid" 2>/dev/null || true
  wait "$pool_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Waiting for GPU pool ($GPU_POOL, model=$MODEL) to become ready — this involves loading model weights on every listed GPU and can take several minutes; live progress: tail -f $SERVER_LOG"
ready=0
for i in $(seq 1 300); do
  if [[ -s "$ENDPOINTS_FILE" ]]; then
    ready=1
    break
  fi
  kill -0 "$pool_pid" 2>/dev/null || break
  if (( i % 12 == 0 )); then
    echo "  ... still waiting on GPU pool ($((i * 5))s elapsed; tail -f $SERVER_LOG for detail)"
  fi
  sleep 5
done
if [[ "$ready" != 1 ]]; then
  echo "ERROR: GPU pool failed to become ready; see $SERVER_LOG" >&2
  tail -80 "$SERVER_LOG" >&2 || true
  exit 1
fi

PY="$(python_bin)"
n_endpoints="$("$PY" -c "import json;print(len(json.load(open('$ENDPOINTS_FILE'))))")"
first_endpoint="$("$PY" -c "import json;print(json.load(open('$ENDPOINTS_FILE'))[0])")"
echo "vLLM pool ready: $n_endpoints endpoint(s) -> $ENDPOINTS_FILE"

export TB2_ENDPOINTS_FILE="$ENDPOINTS_FILE"
# Fallback for any code path that only knows about a single endpoint.
export TB2_API_BASE="$first_endpoint"
export TB2_MODEL="$MODEL"
[[ -n "${LORA_PATH:-}" ]] && export TB2_MODEL="${LORA_NAME:-${MODEL_TAG}-${MODEL_SIZE}-sft}"
"$@"
