#!/usr/bin/env bash
set -euo pipefail

# End-to-end, timed run of: base eval (pre-evolve) -> harness evolve ->
# build SFT data (own trajectories + TMAX_N external Tmax trajectories) ->
# train SFT -> eval the SFT model on the baseline harness -> eval the SFT
# model on the evolved harness. Every stage's wall-clock time is recorded to
# outputs/runs/$RUN_TAG/timings.{json,md}.
#
# Usage:
#   bash scripts/run_pipeline_timed.sh {4b|9b}
#
# Common overrides (env vars):
#   TMAX_N=200            number of external Tmax trajectories to mix into SFT (0 disables)
#   NUM_ROUNDS=4          evolve rounds
#   GPU_POOL=0,1,2,3,4,5,6,7   shard evolve/eval rollout across these GPUs (see evolve.sh/evaluate.sh)
#   RUN_TAG=<tag>         reuse a specific run tag instead of a fresh timestamp
#   START_STAGE=evolve    skip every stage before this one (use with RUN_TAG to resume
#                         a run that already completed earlier stages, e.g. after fixing
#                         a config error without re-paying for a 30+ minute base eval)
#
# Stage names, in order: eval_base_pre_evolve, evolve, build_sft_data, train_sft,
# eval_sft_baseline_harness, eval_sft_evolved_harness
#
# A failed stage stops the pipeline immediately; timings.json still records
# every stage that ran (including the failed one) so partial runs are not lost.

# Resolve scripts/ whether this file lives in scripts/ or scripts/tb2/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
MODEL_SIZE="${1:-${MODEL_SIZE:-4b}}"
case "$MODEL_SIZE" in 4b|9b) ;; *) echo "usage: run_pipeline_timed.sh {4b|9b}" >&2; exit 2 ;; esac

set -a
# shellcheck disable=SC1090
source "$ROOT/configs/models/$MODEL_SIZE.env"
set +a
export MODEL_SIZE
export RUN_TAG="${RUN_TAG:-tb2-qwen35-${MODEL_SIZE}-tmax-$(date +%Y%m%d-%H%M%S)}"

TMAX_N="${TMAX_N:-200}"
TMAX_PARQUET="${TMAX_PARQUET:-$ROOT/data/external/tmax-sft-only-success/skill_tax_20260505_2.2k_combined_balanced_thinking_only_success/train-00000-of-00001.parquet}"
DEFAULT_DATASET_NAME="qwen35_${MODEL_SIZE}_own_success"
[[ "$TMAX_N" -gt 0 ]] && DEFAULT_DATASET_NAME="${DEFAULT_DATASET_NAME}_plus_tmax${TMAX_N}"
DATASET_NAME="${SFT_DATASET_NAME:-$DEFAULT_DATASET_NAME}"
SFT_NAME="qwen35_${MODEL_SIZE}_${DATASET_NAME}"
LORA_OUT="${SFT_OUTPUT_DIR:-$ROOT/outputs/sft/$SFT_NAME}"

RUN_ROOT="$ROOT/outputs/runs/$RUN_TAG"
LOG_ROOT="$ROOT/logs/$RUN_TAG"
mkdir -p "$RUN_ROOT" "$LOG_ROOT"

# ── Preflight: fail in <1s, not after the base-eval stage burns 30-60 min,
# if the evolve stage's meta-agent key isn't actually resolvable. Mirrors
# the exact fallback evolve.sh uses (OPENAI_API_KEY, else SFG_API_KEY).
_key_check="$(
  source "$_HX_SCRIPTS/_common.sh" >/dev/null
  printf '%s' "${OPENAI_API_KEY:-${SFG_API_KEY:-}}"
)"
if [[ -z "$_key_check" ]]; then
  echo "ERROR: preflight failed — OPENAI_API_KEY / SFG_API_KEY both resolve empty." >&2
  echo "       Fill one of them in $ROOT/.env before running this pipeline — the 'evolve'" >&2
  echo "       stage needs it for the meta-agent. Failing now instead of after the" >&2
  echo "       base-eval stage burns 30-60 minutes first." >&2
  exit 2
fi
echo "Preflight OK: meta-agent API key resolves (${#_key_check} chars)."
unset _key_check

TIMINGS_JSON="$RUN_ROOT/timings.json"
TIMINGS_MD="$RUN_ROOT/timings.md"

STAGE_ORDER_FIRST="eval_base_pre_evolve"
# Each invocation is one "attempt". Resumed attempts append rather than
# overwrite so a run's full history survives, but every attempt is fenced off
# with its own sub-header — without that, rows from 4 failed resumes pile up
# in one undifferentiated table and it becomes impossible to tell which
# duration belongs to which attempt, or which rows are stale.
ATTEMPT_STAMP="$(date -u '+%Y-%m-%d %H:%M:%SZ')"
if [[ -f "$TIMINGS_JSON" && -n "${START_STAGE:-}" && "${START_STAGE:-$STAGE_ORDER_FIRST}" != "$STAGE_ORDER_FIRST" ]]; then
  echo "Resuming run_tag=$RUN_TAG from stage=$START_STAGE — appending to existing $TIMINGS_JSON / $TIMINGS_MD"
  ATTEMPT_NO="$(python3 -c "
import json
try:
    d = json.load(open('$TIMINGS_JSON'))
    print(max([r.get('attempt', 1) for r in d] or [0]) + 1)
except Exception:
    print(1)
")"
  {
    echo
    echo "## Attempt $ATTEMPT_NO — resumed from \`$START_STAGE\` at $ATTEMPT_STAMP"
    echo
    echo "| stage | duration | status |"
    echo "|---|---|---|"
  } >> "$TIMINGS_MD"
else
  ATTEMPT_NO=1
  echo "[]" > "$TIMINGS_JSON"
  {
    echo "# Pipeline timing — $RUN_TAG"
    echo
    echo "model_size=$MODEL_SIZE tmax_n=$TMAX_N dataset=$DATASET_NAME"
    echo
    echo "## Attempt 1 — started $ATTEMPT_STAMP"
    echo
    echo "| stage | duration | status |"
    echo "|---|---|---|"
  } > "$TIMINGS_MD"
fi

_fmt_duration() {
  local s="$1"
  printf '%dh%02dm%02ds' "$((s/3600))" "$(((s%3600)/60))" "$((s%60))"
}

_record_stage() {
  local name="$1" start="$2" end="$3" status="$4"
  local dur=$((end - start))
  python3 - "$TIMINGS_JSON" "$name" "$start" "$end" "$dur" "$status" "$ATTEMPT_NO" <<'PY'
import json, sys
path, name, start, end, dur, status, attempt = sys.argv[1:8]
try:
    data = json.load(open(path))
except Exception:
    data = []
data.append({
    "attempt": int(attempt),
    "stage": name,
    "start_epoch_s": int(start),
    "end_epoch_s": int(end),
    "duration_s": int(dur),
    "status": status,
})
json.dump(data, open(path, "w"), indent=2)
PY
  printf '| %-28s | %s | %s |\n' "$name" "$(_fmt_duration "$dur")" "$status" >> "$TIMINGS_MD"
}

STAGE_ORDER=(eval_base_pre_evolve evolve eval_base_evolved_harness build_sft_data train_sft eval_sft_baseline_harness eval_sft_evolved_harness)
START_STAGE="${START_STAGE:-${STAGE_ORDER[0]}}"
if ! printf '%s\n' "${STAGE_ORDER[@]}" | grep -qx "$START_STAGE"; then
  echo "ERROR: START_STAGE='$START_STAGE' is not a known stage. Valid: ${STAGE_ORDER[*]}" >&2
  exit 2
fi
_STAGES_ACTIVE=0

_run_stage() {
  local name="$1"; shift
  if [[ "$name" == "$START_STAGE" ]]; then
    _STAGES_ACTIVE=1
  fi
  if [[ "$_STAGES_ACTIVE" != 1 ]]; then
    echo
    echo "========== [timed] stage=$name run=$RUN_TAG — SKIPPED (before START_STAGE=$START_STAGE) =========="
    # Record skipped stages in the JSON too, not just the markdown — otherwise
    # the structured record silently omits them and a consumer cannot tell
    # "this stage was skipped on resume" from "this stage never existed".
    local now
    now=$(date +%s)
    _record_stage "$name" "$now" "$now" "skipped"
    return 0
  fi
  echo
  echo "========== [timed] stage=$name run=$RUN_TAG =========="
  local t0 t1
  t0=$(date +%s)
  if "$@"; then
    t1=$(date +%s)
    _record_stage "$name" "$t0" "$t1" "ok"
    echo "----- stage=$name OK ($(_fmt_duration $((t1 - t0)))) -----"
  else
    local rc=$?
    t1=$(date +%s)
    _record_stage "$name" "$t0" "$t1" "FAILED"
    echo "ERROR: stage '$name' failed (rc=$rc) after $(_fmt_duration $((t1 - t0))); see $LOG_ROOT" >&2
    exit "$rc"
  fi
}

PIPELINE_START=$(date +%s)

_run_stage "eval_base_pre_evolve" \
  env EVAL_SFT=0 USE_EVOLVED_HARNESS=0 \
      HARNESS_CONFIG="$ROOT/configs/baseline_harness.yaml" \
      JOB_NAME="eval-base-preevolve-$RUN_TAG" \
      bash "$ROOT/scripts/evaluate.sh"

_run_stage "evolve" \
  bash "$ROOT/scripts/evolve.sh"

# Completes the 2x2 {base, SFT} x {baseline harness, evolved harness}.
# Without this cell the final "SFT + evolved harness" number confounds two
# interventions: you cannot tell whether a drop came from the harness or from
# SFT. It also gives the only apples-to-apples check on the evolve loop's own
# claimed score, which is computed on adaptive subsets with carried-forward
# per-task results rather than a fresh full-set measurement.
# Runs before SFT so the base model is still the thing being served.
_run_stage "eval_base_evolved_harness" \
  env EVAL_SFT=0 USE_EVOLVED_HARNESS=1 \
      JOB_NAME="eval-base-evolved-$RUN_TAG" \
      bash "$ROOT/scripts/evaluate.sh"

_run_stage "build_sft_data" \
  env TMAX_N="$TMAX_N" TMAX_PARQUET="$TMAX_PARQUET" SFT_DATASET_NAME="$DATASET_NAME" \
      bash "$ROOT/scripts/build_sft_data.sh"

_run_stage "train_sft" \
  env SFT_DATASET_NAME="$DATASET_NAME" SFT_OUTPUT_DIR="$LORA_OUT" \
      bash "$ROOT/scripts/train_sft.sh"

_run_stage "eval_sft_baseline_harness" \
  env EVAL_SFT=1 USE_EVOLVED_HARNESS=0 \
      HARNESS_CONFIG="$ROOT/configs/baseline_harness.yaml" \
      LORA_PATH="$LORA_OUT" JOB_NAME="eval-sft-r0h-$RUN_TAG" \
      bash "$ROOT/scripts/evaluate.sh"

_run_stage "eval_sft_evolved_harness" \
  env EVAL_SFT=1 USE_EVOLVED_HARNESS=1 \
      LORA_PATH="$LORA_OUT" JOB_NAME="eval-sft-evolved-$RUN_TAG" \
      bash "$ROOT/scripts/evaluate.sh"

PIPELINE_END=$(date +%s)
TOTAL=$((PIPELINE_END - PIPELINE_START))
printf '| %-28s | %s | %s |\n' "TOTAL" "$(_fmt_duration "$TOTAL")" "-" >> "$TIMINGS_MD"

echo
echo "================================================================"
echo "Pipeline complete: run_tag=$RUN_TAG total=$(_fmt_duration "$TOTAL")"
echo "Timings: $TIMINGS_JSON"
echo "         $TIMINGS_MD"
echo "SFT dataset: $ROOT/recipe/tb2_sft/data/$DATASET_NAME"
echo "SFT adapter: $LORA_OUT"
echo "The 2x2 {base,SFT} x {baseline,evolved harness}:"
echo "  base + baseline harness:   .benchmarks/tb2/eval-base-preevolve-$RUN_TAG"
echo "  base + evolved harness:    .benchmarks/tb2/eval-base-evolved-$RUN_TAG"
echo "  SFT  + baseline harness:   .benchmarks/tb2/eval-sft-r0h-$RUN_TAG"
echo "  SFT  + evolved harness:    .benchmarks/tb2/eval-sft-evolved-$RUN_TAG"
echo
echo "Score them all with:  bash scripts/report_2x2.sh $RUN_TAG"
echo "================================================================"
cat "$TIMINGS_MD"
