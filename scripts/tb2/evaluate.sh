#!/usr/bin/env bash
set -euo pipefail
# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
source "$_HX_SCRIPTS/_common.sh"

HARNESS_CONFIG="${HARNESS_CONFIG:-}"
if [[ -z "$HARNESS_CONFIG" && "${USE_EVOLVED_HARNESS:-1}" == "1" ]]; then
  state="$ROOT/recipe/tb2_evolver/runs/$RUN_TAG/_meta_v2/_meta_scratch/harness_evolve_state.json"
  if [[ -f "$state" ]]; then
    # Prefer best_so_far (the newest config the gate actually measured and
    # accepted) over last_output_config (the meta-agent's most recent
    # successor, which is produced AFTER the final measurement and has never
    # been rolled out or gated). Reporting the ungated successor as "the
    # evolved harness" reintroduces exactly the regression the gate exists to
    # prevent — the final number can come from a config nobody ever scored.
    HARNESS_CONFIG="$("$(python_bin)" - "$state" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
bsf = s.get("best_so_far") or {}
cfg = bsf.get("config")
if cfg:
    print(f"[evaluate] using gated best_so_far: R{bsf.get('round')} "
          f"score={bsf.get('score')}", file=sys.stderr)
else:
    cfg = s.get("last_output_config") or ""
    if cfg:
        print("[evaluate] WARNING: no gated best_so_far in evolve state; falling back "
              "to last_output_config, which was never measured.", file=sys.stderr)
print(cfg)
PY
)"
  else
    echo "WARNING: USE_EVOLVED_HARNESS=1 but no evolve state found for tag '$RUN_TAG'" >&2
    echo "         ($state)" >&2
    echo "         Falling back to the BASELINE harness — this eval will NOT measure an" >&2
    echo "         evolved harness, and any result labelled 'evolved' would be mislabelled." >&2
    if [[ "${ALLOW_BASELINE_FALLBACK:-0}" != "1" ]]; then
      echo "ERROR: refusing to run. Set ALLOW_BASELINE_FALLBACK=1 to override." >&2
      exit 2
    fi
  fi
fi
HARNESS_CONFIG="${HARNESS_CONFIG:-$ROOT/configs/baseline_harness.yaml}"
require_file "$HARNESS_CONFIG"

if [[ "${EVAL_SFT:-0}" == "1" ]]; then
  export LORA_PATH="${LORA_PATH:-$ROOT/outputs/sft/qwen35_${MODEL_SIZE}_qwen35_${MODEL_SIZE}_own_success}"
  export LORA_NAME="${LORA_NAME:-qwen35-${MODEL_SIZE}-sft}"
  [[ -d "$LORA_PATH" ]] || { echo "ERROR: LoRA adapter not found: $LORA_PATH" >&2; exit 2; }
  eval_suffix="-sft"
else
  eval_suffix=""
fi

JOB_NAME="${JOB_NAME:-eval-${RUN_TAG}${eval_suffix}}"

# GPU_POOL="0,1,2,3,4,5,6,7" shards this eval's task list dynamically across
# one vLLM replica per listed GPU instead of a single server. TB2_CONCURRENT
# becomes the concurrency *per replica* in that case.
if [[ -n "${GPU_POOL:-}" ]]; then
  export TB2_HARNESS_CONFIG="$HARNESS_CONFIG"
  cmd=(
    "$(python_bin)" -m recipe.tb2_evolver.scripts.sharded_tb2_eval
    --eval-script "$ROOT/benchmarks/terminal_bench_2/scripts/eval_local_docker.sh"
    --tasks-json "$TASKS_JSON"
    --job-name "$JOB_NAME"
    --jobs-dir "$ROOT/.benchmarks/tb2"
    --concurrent-per-endpoint "${TB2_CONCURRENT:-2}"
    --max-steps "${TB2_MAX_STEPS:-120}"
  )
  # Bounded Docker footprint. Each TB2 task pulls its own multi-GB image and the
  # store is on the root disk, not the shared filesystem, so a large task set
  # exhausts it mid-run: `docker compose up` then fails during the pull and the
  # task records a plain FAIL with no agent tokens — indistinguishable from a
  # model that solved nothing.
  [[ "${TB2_DELETE_IMAGES:-0}" == "1" ]] && cmd+=(--delete-images)
  cmd+=(--prune-every "${TB2_PRUNE_EVERY:-10}" --min-free-gb "${TB2_MIN_FREE_GB:-30}")
else
  cmd=(
    bash "$ROOT/benchmarks/terminal_bench_2/scripts/eval_local_docker.sh"
    --harness-config "$HARNESS_CONFIG"
    --tasks "$TASKS_JSON"
    --job-name "$JOB_NAME"
    -n "${TB2_CONCURRENT:-5}"
    --max-steps "${TB2_MAX_STEPS:-120}"
  )
fi

echo "Evaluating: model=$MODEL job=$JOB_NAME harness=$HARNESS_CONFIG"
if [[ -n "${GPU_POOL:-}" ]]; then
  echo "GPU pool: $GPU_POOL (sharded eval, ${TB2_CONCURRENT:-2} concurrent/replica)"
  bash "$ROOT/scripts/with_server_pool.sh" -- "${cmd[@]}" 2>&1 | tee "$LOG_ROOT/evaluate.log"
else
  bash "$ROOT/scripts/with_server.sh" -- "${cmd[@]}" 2>&1 | tee "$LOG_ROOT/evaluate.log"
fi
