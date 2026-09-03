#!/usr/bin/env bash
set -euo pipefail
# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
source "$_HX_SCRIPTS/_common.sh"

export META_MODEL="${META_MODEL:-openai/gpt-5.5}"

# Credentials follow the meta-model's provider. Demanding an OpenAI key
# unconditionally rejected Bedrock meta-agents, which authenticate through AWS
# and never read that variable -- and it did so only once the evolve stage was
# reached, discarding the eval that preceded it. Mirrors scripts/tmax/evolve_tmax.sh.
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
export TB2_TASK_TIMEOUT="${TB2_TASK_TIMEOUT:-900}"
export EVOLVE_COST_CAP_USD="${EVOLVE_COST_CAP_USD:-none}"
export EVOLVE_MAX_STEPS="${EVOLVE_MAX_STEPS:-200}"
export EVOLVE_WALL_CLOCK_S="${EVOLVE_WALL_CLOCK_S:-5400}"
NUM_ROUNDS="${NUM_ROUNDS:-4}"
CONCURRENT="${TB2_CONCURRENT:-5}"

# RESUME unset  -> auto-detect: resume iff this RUN_TAG already has evolve state.
# RESUME=1      -> force resume.
# RESUME=0      -> force a fresh run (discards existing state for this tag).
#
# Auto-detect is the safe default because run.py's non-resume branch
# unconditionally overwrites harness_evolve_state.json with a fresh dict.
# Re-invoking a failed run without --resume therefore erased history,
# best_so_far and full_task_results, silently re-baselined the gate, and
# re-ran already-completed rounds' rollouts — hours of GPU time — into the
# same job dirs. Orchestrators (run_pipeline_timed.sh START_STAGE=evolve)
# never set RESUME, so that was the default path for every retry.
STATE_JSON="$ROOT/recipe/tb2_evolver/runs/$RUN_TAG/_meta_v2/_meta_scratch/harness_evolve_state.json"
resume_args=()
if [[ "${RESUME:-auto}" == "1" ]]; then
  resume_args+=(--resume)
elif [[ "${RESUME:-auto}" == "auto" && -f "$STATE_JSON" ]]; then
  resume_args+=(--resume)
  echo "Existing evolve state found for tag '$RUN_TAG' — resuming (set RESUME=0 to start fresh and discard it):"
  "$(python_bin)" - "$STATE_JSON" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
print(f"  status={s.get('status')} next_input_round={s.get('next_input_round')} "
      f"rounds_done={len(s.get('history') or [])}")
bsf = s.get("best_so_far") or {}
if bsf:
    print(f"  best_so_far: R{bsf.get('round')} score={bsf.get('score')}")
PY
fi
evidence_args=()
[[ "${REQUIRE_EVIDENCE:-0}" != "1" ]] && evidence_args+=(--no-require-evidence)
adaptive_args=()
if [[ "${ADAPTIVE_SUBSET:-0}" == "1" ]]; then
  adaptive_args+=(--adaptive-subset --adaptive-canary-size "${ADAPTIVE_CANARY_SIZE:-2}")
fi

# Avoid re-running the (expensive, 15-task) R0 rollout when it has already
# been computed: either by a prior evolve attempt for this same RUN_TAG that
# got past R0 before failing later (e.g. at the meta-agent step), or by the
# run_pipeline_timed.sh "eval_base_pre_evolve" stage, which uses the same
# task set + baseline harness and is a valid substitute for R0's trajectories.
# Only adopt a candidate dir if it actually covers every task in TASKS_JSON —
# a dir left behind by a run killed mid-R0 holds a partial task set, and
# run.py's _load_trials_from_tasks_json then hard-fails with a "Tasks not
# found" error that points at the missing tasks rather than at this
# auto-detect, turning a self-healing situation into a manual rm -rf.
_r0_dir_complete() {
  "$(python_bin)" - "$1" "$TASKS_JSON" <<'PY'
import json, sys
from pathlib import Path
d, tasks_json = Path(sys.argv[1]), Path(sys.argv[2])
want = set(json.loads(tasks_json.read_text()))
have = set()
for p in d.iterdir():
    rp = p / "result.json"
    if p.is_dir() and rp.is_file():
        try:
            have.add(json.loads(rp.read_text()).get("task_name") or p.name.split("__")[0])
        except Exception:
            pass
missing = want - have
print(",".join(sorted(missing)))
sys.exit(1 if missing else 0)
PY
}

R0_DIR="${R0_DIR:-}"
if [[ -z "$R0_DIR" ]]; then
  for cand in "$ROOT/.benchmarks/tb2/${RUN_TAG}-r0-traj" "$ROOT/.benchmarks/tb2/eval-base-preevolve-${RUN_TAG}"; do
    [[ -d "$cand" ]] || continue
    if missing="$(_r0_dir_complete "$cand")"; then
      R0_DIR="$cand"
      echo "Reusing complete R0 trajectories: $R0_DIR"
      break
    else
      echo "Skipping incomplete R0 candidate $cand (missing: ${missing:-?})"
    fi
  done
fi
r0_dir_args=()
[[ -n "$R0_DIR" ]] && r0_dir_args+=(--r0-dir "$R0_DIR")

# SEED_HARNESS chains iterations of the harness->SFT loop: iteration k+1 starts
# from iteration k's gated harness instead of the stock one.
seed_args=()
if [[ -n "${SEED_HARNESS:-}" ]]; then
  [[ -f "$SEED_HARNESS" ]] || { echo "ERROR: SEED_HARNESS not found: $SEED_HARNESS" >&2; exit 2; }
  seed_args+=(--baseline-config "$SEED_HARNESS")
  echo "Seeding R0 harness from: $SEED_HARNESS"
fi

# GPU_POOL="0,1,2,3,4,5,6,7" switches from one vLLM server to one replica per
# listed GPU; each round's TB2 rollout is then dynamically sharded across all
# replicas (recipe/tb2_evolver/scripts/sharded_tb2_eval.py) instead of running
# against a single server. --tb2-eval-concurrent becomes the concurrency
# *per replica*, not the round total, when GPU_POOL is set.
#
# Bedrock unsets PROVIDER_ID on purpose (SigV4, no gateway header). Passing
# --provider-id "$PROVIDER_ID" under `set -u` is what killed tb21-coev 2170
# after the 89-task scan had already finished. Match evolve_tmax.sh: omit it.
provider_args=()
[[ -n "${PROVIDER_ID:-}" ]] && provider_args+=(--provider-id "$PROVIDER_ID")

noop_args=()
if [[ "${EVOLVE_NOOP_ON_META_FAIL:-1}" == "1" ]]; then
  noop_args+=(--noop-on-meta-fail)
else
  noop_args+=(--no-noop-on-meta-fail)
fi

cmd=(
  "$(python_bin)" -m recipe.tb2_evolver.run
  --tasks "$TASKS_JSON"
  --run-tag "$RUN_TAG"
  --num-rounds "$NUM_ROUNDS"
  --model "$META_MODEL"
  "${provider_args[@]}"
  --trajectory-mode rerun
  --tb2-eval-script "$ROOT/benchmarks/terminal_bench_2/scripts/eval_local_docker.sh"
  --tb2-eval-concurrent "$CONCURRENT"
  --tb2-max-steps "${TB2_MAX_STEPS:-120}"
  --regression-tolerance "${REGRESSION_TOLERANCE:-0.0667}"
  "${noop_args[@]}"
  "${evidence_args[@]}"
  "${resume_args[@]}"
  "${adaptive_args[@]}"
  "${r0_dir_args[@]}"
  "${seed_args[@]}"
)

echo "Evolving harness: model=$MODEL rounds=$NUM_ROUNDS tag=$RUN_TAG"
echo "Results: $ROOT/recipe/tb2_evolver/runs/$RUN_TAG"
if [[ -n "${GPU_POOL:-}" ]]; then
  echo "GPU pool: $GPU_POOL (sharded rollout, ${CONCURRENT} concurrent/replica)"
  bash "$ROOT/scripts/with_server_pool.sh" -- "${cmd[@]}" 2>&1 | tee "$LOG_ROOT/evolve.log"
else
  bash "$ROOT/scripts/with_server.sh" -- "${cmd[@]}" 2>&1 | tee "$LOG_ROOT/evolve.log"
fi
