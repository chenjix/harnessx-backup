#!/usr/bin/env bash
# Harness evolve on Tmax taxonomy tasks (NOT TB2 Harbor).
#
#   MODEL_SIZE=9b EVOLVE_ROUNDS=5 \
#     TASKS_JSON=recipe/tb2_evolver/tasks_tmax_evolve50_list.json \
#     TMAX_ENVS_JSONL=recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl \
#     bash scripts/evolve_tmax.sh
#
# Meta-agent: META_MODEL=bedrock/us.anthropic.claude-opus-4-8 (from .env)
# Base agent: local vLLM Qwen via GPU_POOL

set -euo pipefail
# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
source "$_HX_SCRIPTS/_common.sh"

export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export EVOLVE_EVAL_BACKEND=tmax

case "$META_MODEL" in
  bedrock/*)
    export AWS_REGION_NAME="${AWS_REGION_NAME:-${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}}"
    unset PROVIDER_ID || true
    echo "Meta-agent: $META_MODEL via AWS Bedrock (region=$AWS_REGION_NAME)"
    ;;
  anthropic/*)
    require_var ANTHROPIC_API_KEY
    export PROVIDER_ID="${PROVIDER_ID:-anthropic}"
    ;;
  *)
    export OPENAI_API_BASE="${OPENAI_API_BASE:-${GATEWAY_URL:-https://gateway.salesforceresearch.ai}/openai/process/v1}"
    export OPENAI_API_KEY="${OPENAI_API_KEY:-${SFG_API_KEY:-}}"
    require_var OPENAI_API_KEY
    export PROVIDER_ID="${PROVIDER_ID:-openai}"
    ;;
esac

export TASKS_JSON="${TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
export TMAX_ENVS_JSONL="${TMAX_ENVS_JSONL:-$ROOT/recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl}"
require_file "$TASKS_JSON"
require_file "$TMAX_ENVS_JSONL"

export EVOLVE_COST_CAP_USD="${EVOLVE_COST_CAP_USD:-none}"
export EVOLVE_MAX_STEPS="${EVOLVE_MAX_STEPS:-200}"
# Tmax meta rounds dig through 50 traj dirs; 90m was too tight once a tool hung.
export EVOLVE_WALL_CLOCK_S="${EVOLVE_WALL_CLOCK_S:-10800}"
# On meta timeout / missing config.yaml, keep going with a no-op copy of the
# incumbent config instead of failing the whole multi-hour job.
export EVOLVE_NOOP_ON_META_FAIL="${EVOLVE_NOOP_ON_META_FAIL:-1}"
# Per-call generation cap for base-agent rollouts (harness_runner / agent_loop).
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
NUM_ROUNDS="${NUM_ROUNDS:-${EVOLVE_ROUNDS:-5}}"
# Concurrent Tmax docker+agent workers across the whole pool (not per-GPU).
# With 8 vLLM replicas, 8–16 is a good starting point on 96 CPUs.
CONCURRENT="${TMAX_CONCURRENT:-${TB2_CONCURRENT:-8}}"

STATE_JSON="$ROOT/recipe/tb2_evolver/runs/$RUN_TAG/_meta_v2/_meta_scratch/harness_evolve_state.json"
resume_args=()
if [[ "${RESUME:-auto}" == "1" ]]; then
  resume_args+=(--resume --tb2-eval-resume)
elif [[ "${RESUME:-auto}" == "auto" && -f "$STATE_JSON" ]]; then
  resume_args+=(--resume --tb2-eval-resume)
  echo "Resuming evolve state: $STATE_JSON"
fi

evidence_args=()
[[ "${REQUIRE_EVIDENCE:-0}" != "1" ]] && evidence_args+=(--no-require-evidence)

noop_args=()
if [[ "${EVOLVE_NOOP_ON_META_FAIL:-1}" == "1" ]]; then
  noop_args+=(--noop-on-meta-fail)
else
  noop_args+=(--no-noop-on-meta-fail)
fi

seed_args=()
if [[ -n "${SEED_HARNESS:-}" ]]; then
  require_file "$SEED_HARNESS"
  seed_args+=(--baseline-config "$SEED_HARNESS")
fi

provider_args=()
[[ -n "${PROVIDER_ID:-}" ]] && provider_args+=(--provider-id "$PROVIDER_ID")

r0_dir_args=()
[[ -n "${R0_DIR:-}" ]] && r0_dir_args+=(--r0-dir "$R0_DIR")

# Free-form pass-through to recipe.tb2_evolver.run, word-split on spaces, e.g.
#   EVOLVE_EXTRA_ARGS="--fanout 8 --fanout-keep 2 --explore-every 3"
# Exists so a new run.py flag can be A/B'd from sbatch without editing this
# script for each one. Not quoted on purpose — the whole point is word splitting.
extra_args=()
if [[ -n "${EVOLVE_EXTRA_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  extra_args=(${EVOLVE_EXTRA_ARGS})
fi

cmd=(
  "$(python_bin)" -m recipe.tb2_evolver.run
  --eval-backend tmax
  --tmax-envs-jsonl "$TMAX_ENVS_JSONL"
  --tasks "$TASKS_JSON"
  --run-tag "$RUN_TAG"
  --num-rounds "$NUM_ROUNDS"
  --model "$META_MODEL"
  "${provider_args[@]}"
  --trajectory-mode rerun
  --tb2-eval-concurrent "$CONCURRENT"
  --tb2-max-steps "${TMAX_MAX_STEPS:-80}"
  --regression-tolerance "${REGRESSION_TOLERANCE:-0.04}"
  --evolve-cost "$EVOLVE_COST_CAP_USD"
  --evolve-steps "$EVOLVE_MAX_STEPS"
  --evolve-wall-clock "$EVOLVE_WALL_CLOCK_S"
  "${noop_args[@]}"
  "${evidence_args[@]}"
  "${resume_args[@]}"
  "${r0_dir_args[@]}"
  "${seed_args[@]}"
  "${extra_args[@]}"
)

echo "Evolving harness on Tmax: base=$MODEL meta=$META_MODEL rounds=$NUM_ROUNDS tag=$RUN_TAG"
echo "  tasks : $TASKS_JSON"
echo "  envs  : $TMAX_ENVS_JSONL"
echo "  conc  : $CONCURRENT"
echo "Results: $ROOT/recipe/tb2_evolver/runs/$RUN_TAG"

if [[ -n "${GPU_POOL:-}" ]]; then
  echo "GPU pool: $GPU_POOL"
  bash "$ROOT/scripts/with_server_pool.sh" -- "${cmd[@]}" 2>&1 | tee "$LOG_ROOT/evolve_tmax.log"
else
  bash "$ROOT/scripts/with_server.sh" -- "${cmd[@]}" 2>&1 | tee "$LOG_ROOT/evolve_tmax.log"
fi
