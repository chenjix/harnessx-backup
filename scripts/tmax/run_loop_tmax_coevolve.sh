#!/usr/bin/env bash
# Tmax harness ↔ SFT co-evolution loop.
#
# Every outer iteration starts from the *incumbent pair* — the best model AND
# the best harness measured so far on holdout-102 — and tries to move one of
# them forward:
#
#   A0 rotate the evolve set (EVOLVE_SET_SIZE, default 50):
#        retire tasks the model has mastered (solved AND already harvested into
#        an SFT corpus), refill from the taxonomy pool. holdout-102 never enters.
#   A  harness evolve on that set (EVOLVE_ROUNDS, default 5)
#        model_best + harness_best → harness_k (+ success trajs)
#   B  holdout-102 with model_best + harness_k → HARNESS ratchet:
#        better, or tied on a clean run  → harness_best := harness_k
#        worse                           → keep harness_best, drop harness_k
#   C  filter high-quality unique success trajs → SFT corpus_k
#        (this iteration's trajs fill the traj budget before older ones)
#   D  quick LoRA SFT continuing from model_best → model_k
#   D2 optional online RL (DPPO/GRPO-fast) on ≤RL_N_TASKS taxonomy tasks
#        → model_k_rl. NEVER trains on holdout-102 (eval-only, matches Tmax).
#   E  holdout-102 with model_k(+rl) + harness_best → MODEL ratchet:
#        better, or tied on a clean run  → model_best := model_k
#        worse                           → reject, run EXTRA_EVOLVE_ROUNDS more,
#                                          rebuild corpus, re-SFT / re-eval
#                                          (up to MAX_SFT_RETRIES), then move on
#
# Ratchet, relaxed (ACCEPT_TIES=1, default): a candidate that *ties* the
# incumbent is accepted as long as its own holdout job was clean — no more than
# TIE_MAX_SYSTEM_ERRORS tasks whose status is in SYSTEM_ERROR_STATUSES, and no
# missing results. A flat score produced by a run where containers or endpoints
# died is not evidence of parity, so it is still rejected. Set ACCEPT_TIES=0 to
# go back to requiring a strict improvement.
#
# Usage:
#   REPLICATE=1 CLEAN_STALE=1 bash scripts/tmax/run_loop_tmax_coevolve.sh
#   REPLICATE=1 START_ITER=2 bash scripts/tmax/run_loop_tmax_coevolve.sh
#   REPLICATE=1 N_ITERS=1 EVOLVE_ROUNDS=2 bash scripts/tmax/run_loop_tmax_coevolve.sh  # smoke
#
# Key env:
#   REPLICATE          required; isolates output paths
#   N_ITERS=3
#   EVOLVE_ROUNDS=5
#   EXTRA_EVOLVE_ROUNDS=2   # appended when SFT does not improve holdout
#   MAX_SFT_RETRIES=2
#   MIN_TRAJS=20 MAX_TRAJS=100
#   ACCEPT_TIES=1           # accept a tie when the candidate's eval is clean
#   TIE_MAX_SYSTEM_ERRORS=0 # how many error/agent_error tasks a tie tolerates
#   SYSTEM_ERROR_STATUSES=error,agent_error
#   MODEL_RATCHET_TOL=0     # legacy: >0 accepts ties without the clean-run check
#   HARNESS_RATCHET=1       # ratchet the harness on holdout too (not just carry)
#   INIT_LORA_PATH=...      # optional warm start (e.g. SFT-500 adapter)
#   SEED_HARNESS=...        # optional; default baseline_tmax_harness.yaml
#   HOLDOUT_TASKS_JSON=.../tasks_tmax_only200.json
#   EVOLVE_TASKS_JSON=.../tasks_tmax_evolve50_list.json   # iteration-1 set
#   ENABLE_RL=0|1           # after SFT, run official-style DPPO (train≠holdout)
#   RL_EPISODES=512         # keep small inside the coevolve loop
#   RL_N_TASKS=100          # taxonomy sample size for RL (excludes holdout-102)
#
# Evolve-set rotation:
#   ROTATE_EVOLVE_TASKS=1   # 0 restores the fixed-50 behaviour
#   ROTATE_FROM_ITER=2      # first iteration that rotates (1 = from the start)
#   EVOLVE_SET_SIZE=50
#   ROTATE_MIN_SET_SIZE=20  # fail rather than evolve on a nearly empty set
#   ROTATE_MIN_NEW=<size>   # preflight: fresh tasks the pool must be able to draw
#   ROTATE_SEED=42
#   MASTERY_MIN_SUCCESSES=1 # successes needed before a task is called mastered
#   MASTERY_MIN_SUCCESS_RATE=0
#   MASTERY_REQUIRE_CORPUS=1  # also require the task to be in an SFT corpus
#   TAXONOMY_PARQUET=data/external/tmax-taxonomy/data/train-00000-of-00001.parquet
#                           # 2.2k pool new tasks are drawn from (has container_def)
#   POOL_ENVS_JSONL="a.jsonl b.jsonl"   # optional extra env-row sources, space
#                                       # separated (see docs/DATA.md)

set -Eeuo pipefail

# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"

REPLICATE="${REPLICATE:?set REPLICATE=1|2|3 — must differ per concurrent chain}"
N_ITERS="${N_ITERS:-3}"
START_ITER="${START_ITER:-1}"
EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}"
EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-2}"
MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-2}"
MIN_TRAJS="${MIN_TRAJS:-20}"
MAX_TRAJS="${MAX_TRAJS:-100}"
PER_TASK="${PER_TASK:-3}"
MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
SFT_EPOCHS="${SFT_EPOCHS:-2}"
# When building corpus for iter k, also include trajs from iters 1..k-1 so the
# SFT pool can grow toward 50–100 even though each evolve set has only 50 tasks.
CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
# With rotation on, the cumulative pool is mostly *older* tasks. Rank this
# iteration's trajectories into the traj budget first so the fresh material the
# rotation exists to collect cannot be crowded out by high-quality old demos.
CORPUS_PREFER_CURRENT="${CORPUS_PREFER_CURRENT:-1}"

# ── ratchet ─────────────────────────────────────────────────────────────────
ACCEPT_TIES="${ACCEPT_TIES:-1}"
TIE_MAX_SYSTEM_ERRORS="${TIE_MAX_SYSTEM_ERRORS:-0}"
SYSTEM_ERROR_STATUSES="${SYSTEM_ERROR_STATUSES:-error,agent_error}"
HARNESS_RATCHET="${HARNESS_RATCHET:-1}"
# Re-measure (and re-ratchet) the harness after EXTRA_EVOLVE_ROUNDS. Off by
# default: another holdout-102 pass per retry is expensive, and swapping the
# harness mid-iteration would confound the model comparison whose BEFORE score
# was measured with the older harness.
EVAL_HARNESS_AFTER_EXTRA="${EVAL_HARNESS_AFTER_EXTRA:-0}"

# ── evolve-set rotation ─────────────────────────────────────────────────────
ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
ROTATE_FROM_ITER="${ROTATE_FROM_ITER:-2}"
EVOLVE_SET_SIZE="${EVOLVE_SET_SIZE:-50}"
ROTATE_MIN_SET_SIZE="${ROTATE_MIN_SET_SIZE:-20}"
ROTATE_SEED="${ROTATE_SEED:-42}"
MASTERY_MIN_SUCCESSES="${MASTERY_MIN_SUCCESSES:-1}"
MASTERY_MIN_SUCCESS_RATE="${MASTERY_MIN_SUCCESS_RATE:-0}"
MASTERY_REQUIRE_CORPUS="${MASTERY_REQUIRE_CORPUS:-1}"
TAXONOMY_PARQUET="${TAXONOMY_PARQUET:-$ROOT/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"
POOL_ENVS_JSONL="${POOL_ENVS_JSONL:-}"

# Online RL after SFT (official open-instruct grpo_fast / DPPO). Default off —
# needs docker + open-instruct deps; train split is taxonomy (≤RL_N_TASKS),
# never the 102 holdout.
ENABLE_RL="${ENABLE_RL:-0}"
RL_EPISODES="${RL_EPISODES:-512}"
RL_N_TASKS="${RL_N_TASKS:-100}"

export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"
case "$MODEL_SIZE" in
  27b) export MODEL_TAG="${MODEL_TAG:-qwen36}" ;;
  *)   export MODEL_TAG="${MODEL_TAG:-qwen35}" ;;
esac

export EVOLVE_TASKS_JSON="${EVOLVE_TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
export EVOLVE_ENVS_JSONL="${EVOLVE_ENVS_JSONL:-$ROOT/recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl}"
export HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json}"
export HOLDOUT_ENVS_JSONL="${HOLDOUT_ENVS_JSONL:-$ROOT/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl}"
export TMAX_MAX_STEPS="${TMAX_MAX_STEPS:-80}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-2}"
export TB2_TEMPERATURE="${TB2_TEMPERATURE:-0}"
export REGRESSION_TOLERANCE="${REGRESSION_TOLERANCE:-0.04}"
export EVOLVE_NOOP_ON_META_FAIL="${EVOLVE_NOOP_ON_META_FAIL:-1}"
PY="${PYTHON_BIN:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "${VLLM_VENV:-$HOME/.venv}/bin/python" ]]; then
    PY="${VLLM_VENV:-$HOME/.venv}/bin/python"
  else
    PY="$(command -v python3)"
  fi
fi
export PYTHON_BIN="$PY"

# Stage D trains with trl/peft, which the vLLM-serving venv deliberately does not
# have — pointing SFT_PYTHON at it (the old default) fails after the stage has
# already been entered. Pick an interpreter that can actually import trl, and only
# fall back to the serving venv if nothing better exists, so the failure message
# names the real problem.
_has_mod() { [[ -x "$1" ]] && "$1" -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('$2') else 1)" 2>/dev/null; }
if [[ -z "${SFT_PYTHON:-}" ]]; then
  for _cand in     "$HOME/miniconda3/envs/${SFT_CONDA_ENV:-vllm-019-cu128-clean}/bin/python"     "/fsx/home/jixuan.chen/miniconda3/envs/${SFT_CONDA_ENV:-vllm-019-cu128-clean}/bin/python"     "$PY"; do
    if _has_mod "$_cand" trl; then SFT_PYTHON="$_cand"; break; fi
  done
  SFT_PYTHON="${SFT_PYTHON:-$PY}"
fi
export SFT_PYTHON
# train_sft.sh defaults to flash_attention_2; no interpreter on this cluster has
# flash-attn, and sdpa is correct (if slower) on A100.
if [[ -z "${SFT_ATTN_IMPLEMENTATION:-}" ]]; then
  if _has_mod "$SFT_PYTHON" flash_attn; then
    SFT_ATTN_IMPLEMENTATION=flash_attention_2
  else
    SFT_ATTN_IMPLEMENTATION=sdpa
  fi
fi
export SFT_ATTN_IMPLEMENTATION

BASE="tmax-coev-rep${REPLICATE}"
STATE="$ROOT/outputs/tmax_coevolve/rep${REPLICATE}"
mkdir -p "$STATE"
TIMINGS="$STATE/timings.tsv"
SCORES="$STATE/scores.tsv"
[[ -f "$TIMINGS" ]] || printf 'iter\tstage\tseconds\tstarted\n' >"$TIMINGS"
[[ -f "$SCORES" ]]  || printf 'iter\tstage\tmodel\tharness\tpassed\ttotal\tjob\n' >"$SCORES"

BASELINE_HARNESS="${SEED_HARNESS:-$ROOT/configs/baseline_tmax_harness.yaml}"
LORA_SERVE_NAME="${MODEL_TAG}-${MODEL_SIZE}-tmax-coev-r${REPLICATE}"

log()  { printf '\n\033[1m[tmax-coev rep%s] %s\033[0m\n' "$REPLICATE" "$*"; }

CURRENT_STAGE="startup"
STATUS_FILE="$STATE/STATUS"
on_err() {
  local rc=$?
  {
    echo "FAILED"
    echo "stage    : $CURRENT_STAGE"
    echo "exit     : $rc"
    echo "at       : $(date '+%F %T')"
  } >"$STATUS_FILE"
  printf '\n\033[1;31m[tmax-coev rep%s] FAILED in %s (exit %s)\033[0m\n' \
    "$REPLICATE" "$CURRENT_STAGE" "$rc"
  printf 'Rerun the same command to resume — completed stages are skipped via .done markers.\n'
}
trap on_err ERR

# Exclusive lock per REPLICATE
LOCKFILE="$STATE/.lock"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "ERROR: another tmax-coevolve chain with REPLICATE=$REPLICATE is already running." >&2
  exit 2
fi
printf 'RUNNING\nstarted: %s\npid: %s\n' "$(date '+%F %T')" "$$" >"$STATUS_FILE"

# Stale vLLM ports
BASE_PORT="${POOL_BASE_PORT:-8300}"
N_GPUS_PF="$(awk -F',' '{print NF}' <<<"$GPU_POOL")"
stale=()
for (( i=0; i<N_GPUS_PF; i++ )); do
  p=$((BASE_PORT + i))
  curl -fsS --max-time 1 "http://127.0.0.1:$p/v1/models" >/dev/null 2>&1 && stale+=("$p")
done
if (( ${#stale[@]} )); then
  if [[ "${CLEAN_STALE:-0}" == "1" ]]; then
    log "cleaning ${#stale[@]} stale vLLM server(s) on ports ${stale[*]}"
    pkill -f "vllm.*--port 83" || true
    sleep 8
  else
    echo "ERROR: stale vLLM on ports ${stale[*]}. Re-run with CLEAN_STALE=1." >&2
    exit 2
  fi
fi

[[ -f "$EVOLVE_TASKS_JSON" ]] || { echo "ERROR: missing $EVOLVE_TASKS_JSON" >&2; exit 2; }
[[ -f "$HOLDOUT_TASKS_JSON" ]] || { echo "ERROR: missing $HOLDOUT_TASKS_JSON" >&2; exit 2; }
[[ -f "$BASELINE_HARNESS" ]] || { echo "ERROR: missing $BASELINE_HARNESS" >&2; exit 2; }
[[ -x "$PY" ]] || { echo "ERROR: bad PYTHON_BIN=$PY" >&2; exit 2; }

mark() { touch "$STATE/.done-$1"; }
done_p() { [[ -f "$STATE/.done-$1" ]]; }

time_stage() {
  local it="$1" st="$2"; shift 2
  local t0 t1
  CURRENT_STAGE="iter${it}/${st}"
  printf 'RUNNING\nstage: %s\nsince: %s\npid: %s\n' \
    "$CURRENT_STAGE" "$(date '+%F %T')" "$$" >"$STATUS_FILE"
  t0=$(date +%s)
  "$@"
  t1=$(date +%s)
  printf '%s\t%s\t%s\t%s\n' "$it" "$st" "$((t1-t0))" "$(date -d "@$t0" '+%F %T' 2>/dev/null || date -r "$t0" '+%F %T')" >>"$TIMINGS"
  log "stage '$st' (iter $it) took $(( (t1-t0)/60 )) min"
}

resolve_evolved() {
  local tag="$1"
  local st="$ROOT/recipe/tb2_evolver/runs/$tag/_meta_v2/_meta_scratch/harness_evolve_state.json"
  [[ -f "$st" ]] || { echo "ERROR: no evolve state at $st" >&2; return 1; }
  "$PY" - "$st" <<'PY'
import json, os, sys
s = json.load(open(sys.argv[1]))
cfg = (s.get("best_so_far") or {}).get("config")
if not cfg or not os.path.isfile(cfg):
    sys.exit("NO_GATED_CONFIG")
print(cfg)
PY
}

score_holdout() {  # score_holdout <job_name> -> passed\ttotal
  "$PY" - "$ROOT/.benchmarks/tmax/$1" "$HOLDOUT_TASKS_JSON" <<'PY'
import json, sys
from pathlib import Path
d, tj = Path(sys.argv[1]), Path(sys.argv[2])
raw = json.loads(tj.read_text())
if isinstance(raw, list):
    total = len(raw)
else:
    ids = raw.get("task_ids") or raw.get("tasks") or raw.get("task_names") or []
    total = len(ids) or int(raw.get("n_tasks") or 0)
passed = 0
summary = d / "summary.json"
if summary.is_file():
    s = json.loads(summary.read_text())
    print(f"{int(s.get('n_passed', 0))}\t{int(s.get('n_tasks', total))}")
    raise SystemExit
if d.is_dir():
    for p in d.glob("*.result.json"):
        if p.name == "result.json":
            continue
        try:
            r = json.loads(p.read_text()).get("reward")
        except Exception:
            continue
        if isinstance(r, (int, float)) and r > 0:
            passed += 1
print(f"{passed}\t{total}")
PY
}

# How many tasks in a holdout job failed for *infrastructure* reasons rather
# than because the agent got the answer wrong: status in SYSTEM_ERROR_STATUSES,
# plus tasks that produced no result at all. A tie is only trustworthy when this
# is 0 — otherwise "same score" may just mean "the same N containers died".
eval_system_errors() {  # eval_system_errors <job> -> n_bad\tn_results\tn_expected
  "$PY" - "$ROOT/.benchmarks/tmax/$1" "$HOLDOUT_TASKS_JSON" "$SYSTEM_ERROR_STATUSES" <<'PY'
import json, sys
from pathlib import Path
d, tj = Path(sys.argv[1]), Path(sys.argv[2])
bad_statuses = {s.strip() for s in sys.argv[3].split(",") if s.strip()}
raw = json.loads(tj.read_text())
if isinstance(raw, list):
    expected = len(raw)
else:
    ids = raw.get("task_ids") or raw.get("tasks") or raw.get("task_names") or []
    expected = len(ids) or int(raw.get("n_tasks") or 0)
rows = []
summary = d / "summary.json"
if summary.is_file():
    rows = list(json.loads(summary.read_text()).get("results") or [])
elif d.is_dir():
    for p in sorted(d.glob("*.result.json")):
        if p.name == "result.json":
            continue
        try:
            rows.append(json.loads(p.read_text()))
        except Exception:
            rows.append({"status": "error"})
n_bad = sum(1 for r in rows if str(r.get("status") or "error") in bad_statuses)
missing = max(0, expected - len(rows)) if expected else 0
print(f"{n_bad + missing}\t{len(rows)}\t{expected}")
PY
}

# ratchet_accept <after> <before> <candidate_job> <what>
# 0 = accept, 1 = reject. Sets RATCHET_REASON.
ratchet_accept() {
  local after="$1" before="$2" job="$3" what="$4"
  local errs n_bad
  if (( after > before )); then
    RATCHET_REASON="improved ${before} → ${after}"
    return 0
  fi
  if (( after == before )) && [[ "$ACCEPT_TIES" == "1" ]]; then
    errs="$(eval_system_errors "$job")"
    n_bad="$(awk '{print $1}' <<<"$errs")"
    if (( n_bad <= TIE_MAX_SYSTEM_ERRORS )); then
      RATCHET_REASON="tied at ${after} on a clean run (system_errors=${n_bad} <= ${TIE_MAX_SYSTEM_ERRORS})"
      return 0
    fi
    RATCHET_REASON="tied at ${after} but ${n_bad} system error(s) > ${TIE_MAX_SYSTEM_ERRORS} — not evidence of parity"
    return 1
  fi
  if (( MODEL_RATCHET_TOL > 0 )) && (( after >= before )); then
    RATCHET_REASON="tied at ${after}, accepted by legacy MODEL_RATCHET_TOL=${MODEL_RATCHET_TOL}"
    return 0
  fi
  RATCHET_REASON="${what} did not improve (${before} → ${after})"
  return 1
}

record_score() {  # record_score <iter> <stage> <model> <harness> <job>
  if awk -F'\t' -v i="$1" -v st="$2" 'NR>1 && $1==i && $2==st {found=1} END{exit !found}' "$SCORES"; then
    log "score for iter=$1 stage=$2 already recorded — not re-appending"
    return 0
  fi
  local sc; sc="$(score_holdout "$5")"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$sc" "$5" >>"$SCORES"
  log "SCORE  iter=$1 stage=$2 model=$3 harness=$4 -> $sc"
}

run_holdout_eval() {  # run_holdout_eval <job> <harness_cfg> <adapter_or_model_or_empty>
  local job="$1" cfg="$2" ad="${3:-}"
  if done_p "$job"; then log "skip holdout $job (already done)"; return 0; fi
  (
    export JOB_NAME="$job"
    export RUN_TAG="$job"
    export TASKS_JSON="$HOLDOUT_TASKS_JSON"
    export ENVS_JSONL="$HOLDOUT_ENVS_JSONL"
    export HARNESS_CONFIG="$cfg"
    export TMAX_CONCURRENT="$HOLDOUT_CONCURRENT"
    export RESUME="${RESUME_HOLDOUT:-1}"
    # LoRA adapter vs full HF ckpt (post-RL) vs base.
    if [[ -n "$ad" && -f "$ad/adapter_config.json" ]]; then
      export EVAL_SFT=1 LORA_PATH="$ad" LORA_NAME="$LORA_SERVE_NAME"
      unset MODEL_OVERRIDE || true
    elif [[ -n "$ad" && -f "$ad/config.json" ]]; then
      export EVAL_SFT=0 MODEL_OVERRIDE="$ad"
      unset LORA_PATH LORA_NAME || true
    else
      export EVAL_SFT=0
      unset LORA_PATH LORA_NAME MODEL_OVERRIDE || true
    fi
    bash "$ROOT/scripts/evaluate_tmax.sh"
  )
  mark "$job"
}

run_evolve() {  # run_evolve <run_tag> <num_rounds> <seed_harness> <adapter_or_model_or_empty> <resume 0|1>
  local tag="$1" rounds="$2" seed="$3" ad="${4:-}" resume="${5:-0}"
  (
    export RUN_TAG="$tag"
    export TASKS_JSON="$EVOLVE_TASKS_JSON"
    export TMAX_ENVS_JSONL="$EVOLVE_ENVS_JSONL"
    export EVOLVE_ROUNDS="$rounds"
    export NUM_ROUNDS="$rounds"
    export SEED_HARNESS="$seed"
    export RESUME="$resume"
    export TMAX_CONCURRENT
    export GPU_POOL
    export MODEL_SIZE
    export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
    if [[ -n "$ad" && -f "$ad/adapter_config.json" ]]; then
      export LORA_PATH="$ad" LORA_NAME="$LORA_SERVE_NAME"
      unset MODEL_OVERRIDE || true
    elif [[ -n "$ad" && -f "$ad/config.json" ]]; then
      export MODEL_OVERRIDE="$ad"
      unset LORA_PATH LORA_NAME || true
    else
      unset LORA_PATH LORA_NAME MODEL_OVERRIDE || true
    fi
    bash "$ROOT/scripts/evolve_tmax.sh"
  )
}

build_corpus() {  # build_corpus <dataset_name> <run_tag...>
  local name="$1"; shift
  local tags=("$@")
  local args=(--name "$name" --min-trajs "$MIN_TRAJS" --max-trajs "$MAX_TRAJS"
              --per-task "$PER_TASK"
              --exclude-tasks "$HOLDOUT_TASKS_JSON")
  local t
  for t in "${tags[@]}"; do
    args+=(--run-tag "$t")
  done
  # tags[0] is always this iteration's tag (see caller).
  if [[ "$CORPUS_PREFER_CURRENT" == "1" && ${#tags[@]} -gt 1 ]]; then
    args+=(--prefer-run-tag "${tags[0]}")
  fi
  PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" \
    "$PY" -m recipe.tb2_sft.src.build_tmax_evolve_sft "${args[@]}"
}

# ── evolve-set rotation ─────────────────────────────────────────────────────
SETLOG="$STATE/task_sets.tsv"
[[ -f "$SETLOG" ]] || printf 'iter\tn_tasks\tn_kept\tn_new\tn_retired\ttasks_json\n' >"$SETLOG"

set_file_for_iter() { printf '%s/evolve_set_i%s.env\n' "$STATE" "$1"; }

record_task_set() {  # record_task_set <iter> <tasks_json> <envs_jsonl>
  local f; f="$(set_file_for_iter "$1")"
  {
    printf 'EVOLVE_TASKS_JSON=%s\n' "$2"
    printf 'EVOLVE_ENVS_JSONL=%s\n' "$3"
  } >"$f"
}

pool_envs_args() {  # echoes --pool-envs-jsonl args for every configured source
  local p
  for p in $POOL_ENVS_JSONL; do
    if [[ -n "$p" ]]; then
      printf -- '--pool-envs-jsonl\n%s\n' "$p"
    fi
  done
  # This iteration's env file is a valid row source for the tasks already in
  # play; newly drawn tasks come from the taxonomy parquet.
  if [[ -f "$EVOLVE_ENVS_JSONL" ]]; then
    printf -- '--pool-envs-jsonl\n%s\n' "$EVOLVE_ENVS_JSONL"
  fi
  return 0
}

rotate_task_set() {  # rotate_task_set <iter>
  local k="$1"
  local prev=$(( k - 1 ))
  local mastered="$STATE/mastered_i${k}.json"
  local out_dir="$ROOT/recipe/tb2_sft/data/${BASE//-/_}_i${k}_evolveset"
  local prev_set_file prev_tasks_json
  prev_set_file="$(set_file_for_iter "$prev")"
  if [[ -f "$prev_set_file" ]]; then
    # shellcheck disable=SC1090
    prev_tasks_json="$(sed -nE 's/^EVOLVE_TASKS_JSON=//p' "$prev_set_file" | tail -1)"
  fi
  prev_tasks_json="${prev_tasks_json:-$EVOLVE_TASKS_JSON}"

  # 1. which tasks has the model mastered across every earlier iteration?
  local m_args=(--bench-root "$ROOT/.benchmarks/tmax"
                --min-successes "$MASTERY_MIN_SUCCESSES"
                --min-success-rate "$MASTERY_MIN_SUCCESS_RATE"
                --out "$mastered")
  [[ "$MASTERY_REQUIRE_CORPUS" == "1" ]] || m_args+=(--no-require-corpus)
  local pk c
  for (( pk=1; pk<k; pk++ )); do
    m_args+=(--run-tag "${BASE}-i${pk}")
    for c in "$ROOT"/recipe/tb2_sft/data/"${BASE//-/_}_i${pk}"*; do
      [[ -f "$c/summary.json" ]] && m_args+=(--corpus "$c")
    done
  done
  if [[ -f "$STATE/mastered_i${prev}.json" ]]; then
    m_args+=(--also-include "$STATE/mastered_i${prev}.json")
  fi
  PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" \
    "$PY" -m recipe.tb2_sft.src.tmax_mastery "${m_args[@]}"

  # 2. keep the un-mastered part of last iteration's set, refill from taxonomy
  local b_args=(--out-dir "$out_dir"
                --size "$EVOLVE_SET_SIZE"
                --min-size "$ROTATE_MIN_SET_SIZE"
                --keep-tasks "$prev_tasks_json"
                --retire-tasks "$mastered"
                --exclude-tasks "$HOLDOUT_TASKS_JSON"
                --taxonomy-parquet "$TAXONOMY_PARQUET"
                --seed "$ROTATE_SEED")
  local ea
  while IFS= read -r ea; do
    [[ -n "$ea" ]] && b_args+=("$ea")
  done < <(pool_envs_args)
  PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" \
    "$PY" -m recipe.tb2_sft.src.build_tmax_evolve_task_set "${b_args[@]}"

  record_task_set "$k" "$out_dir/task_ids.json" "$out_dir/eval_task_set_with_envs.jsonl"
  local n_tasks n_kept n_new n_ret
  n_tasks="$("$PY" -c "import json;print(json.load(open('$out_dir/summary.json'))['n_tasks'])")"
  n_kept="$("$PY" -c "import json;print(json.load(open('$out_dir/summary.json'))['n_kept'])")"
  n_new="$("$PY" -c "import json;print(json.load(open('$out_dir/summary.json'))['n_new'])")"
  n_ret="$("$PY" -c "import json;print(json.load(open('$mastered'))['n'])")"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$k" "$n_tasks" "$n_kept" "$n_new" "$n_ret" \
    "$out_dir/task_ids.json" >>"$SETLOG"
  log "  evolve set i${k}: ${n_tasks} tasks (kept ${n_kept}, new ${n_new}); retired ${n_ret} mastered"
}

use_task_set_for_iter() {  # use_task_set_for_iter <iter>
  local f; f="$(set_file_for_iter "$1")"
  [[ -f "$f" ]] || return 1
  local tj ej
  tj="$(sed -nE 's/^EVOLVE_TASKS_JSON=//p' "$f" | tail -1)"
  ej="$(sed -nE 's/^EVOLVE_ENVS_JSONL=//p' "$f" | tail -1)"
  [[ -f "$tj" && -f "$ej" ]] || return 1
  export EVOLVE_TASKS_JSON="$tj"
  export EVOLVE_ENVS_JSONL="$ej"
  return 0
}

# Rotation preflight: fail now, not six hours in, if new tasks cannot be drawn.
if [[ "$ROTATE_EVOLVE_TASKS" == "1" ]] && (( ROTATE_FROM_ITER <= N_ITERS )); then
  # --avoid-tasks: the tasks already in play must not be counted as *fresh*
  # candidates, or a pool consisting of nothing but the current set passes.
  chk_args=(--check-only --size "$EVOLVE_SET_SIZE" --min-size "$ROTATE_MIN_SET_SIZE"
            --min-new "${ROTATE_MIN_NEW:-$EVOLVE_SET_SIZE}"
            --exclude-tasks "$HOLDOUT_TASKS_JSON"
            --avoid-tasks "$EVOLVE_TASKS_JSON"
            --taxonomy-parquet "$TAXONOMY_PARQUET" --seed "$ROTATE_SEED")
  while IFS= read -r ea; do
    [[ -n "$ea" ]] && chk_args+=("$ea")
  done < <(pool_envs_args)
  if ! PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" \
      "$PY" -m recipe.tb2_sft.src.build_tmax_evolve_task_set "${chk_args[@]}"; then
    echo "ERROR: ROTATE_EVOLVE_TASKS=1 but no usable task pool at" >&2
    echo "       TAXONOMY_PARQUET=$TAXONOMY_PARQUET" >&2
    echo "       Stage that parquet (docs/DATA.md), add POOL_ENVS_JSONL sources," >&2
    echo "       or set ROTATE_EVOLVE_TASKS=0 to keep the fixed-50 set." >&2
    exit 2
  fi
fi

# ── bootstrap incumbent ─────────────────────────────────────────────────────
prev_harness="$BASELINE_HARNESS"
prev_adapter="${INIT_LORA_PATH:-}"
BEST_FILE="$STATE/best_model.tsv"
BEST_HARNESS_FILE="$STATE/best_harness.tsv"
INCUMBENT_FILE="$STATE/incumbent.tsv"
[[ -f "$BEST_FILE" ]] || printf 'iter\tscore\tadapter\n' >"$BEST_FILE"
[[ -f "$BEST_HARNESS_FILE" ]] || printf 'iter\tscore\tharness\n' >"$BEST_HARNESS_FILE"
[[ -f "$INCUMBENT_FILE" ]] || printf 'iter\tstage\tscore\tmodel\tharness\n' >"$INCUMBENT_FILE"

INC_SCORE=-1
save_incumbent() {  # save_incumbent <iter> <stage>
  printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$INC_SCORE" "${prev_adapter:-BASE}" "$prev_harness" \
    >>"$INCUMBENT_FILE"
}

load_incumbent() {
  local line
  line="$(awk -F'\t' 'NR>1 && NF>=5 {l=$0} END{print l}' "$INCUMBENT_FILE")"
  [[ -n "$line" ]] || return 1
  INC_SCORE="$(cut -f3 <<<"$line")"
  local m h
  m="$(cut -f4 <<<"$line")"
  h="$(cut -f5 <<<"$line")"
  if [[ "$m" == "BASE" || ! -d "$m" ]]; then
    m=""
  fi
  if [[ ! -f "$h" ]]; then
    h="$BASELINE_HARNESS"
  fi
  prev_adapter="$m"
  prev_harness="$h"
  return 0
}

if load_incumbent; then
  log "resuming from recorded incumbent: score=$INC_SCORE model=${prev_adapter:-<base>} harness=$prev_harness"
elif (( START_ITER > 1 )); then
  # Legacy resume path for chains started before incumbent.tsv existed.
  p=$(( START_ITER - 1 ))
  if new="$(resolve_evolved "${BASE}-i${p}" 2>/dev/null)"; then
    prev_harness="$new"
  fi
  cand="$ROOT/outputs/sft/${BASE//-/_}_i${p}"
  [[ -d "$cand" ]] && prev_adapter="$cand"
  BEST_SCORE="$(awk -F'\t' 'NR>1 {print $2}' "$BEST_FILE" | sort -n | tail -1)"
  BEST_ADAPTER="$(awk -F'\t' -v b="${BEST_SCORE:--1}" 'NR>1 && $2==b {a=$3} END{print a}' "$BEST_FILE")"
  [[ -n "${BEST_ADAPTER:-}" ]] && prev_adapter="$BEST_ADAPTER"
  INC_SCORE="${BEST_SCORE:--1}"
  log "resuming at iter $START_ITER harness=$prev_harness adapter=${prev_adapter:-<base>}"
fi

# Iteration 1 (or any non-rotating iteration) uses the configured set; record it
# so the first rotation knows what to keep.
[[ -f "$(set_file_for_iter $(( START_ITER - 1 )))" ]] || \
  record_task_set "$(( START_ITER - 1 ))" "$EVOLVE_TASKS_JSON" "$EVOLVE_ENVS_JSONL"

# Optional: measure starting holdout once (base or INIT_LORA)
if ! done_p "holdout-anchor0"; then
  time_stage 0 "holdout_anchor" \
    run_holdout_eval "${BASE}-anchor0" "$prev_harness" "$prev_adapter"
  record_score 0 "holdout_anchor" "${prev_adapter:+init_lora}${prev_adapter:-base}" "harness0" "${BASE}-anchor0"
  # seed best_model from anchor if empty
  if ! awk -F'\t' 'NR>1{found=1} END{exit !found}' "$BEST_FILE"; then
    sc="$(score_holdout "${BASE}-anchor0" | awk '{print $1}')"
    printf '%s\t%s\t%s\n' "0" "$sc" "${prev_adapter:-BASE}" >>"$BEST_FILE"
  fi
  mark "holdout-anchor0"
fi
if (( INC_SCORE < 0 )); then
  INC_SCORE="$(score_holdout "${BASE}-anchor0" | awk '{print $1}')"
  printf '%s\t%s\t%s\n' "0" "$INC_SCORE" "$prev_harness" >>"$BEST_HARNESS_FILE"
  save_incumbent 0 "anchor"
  log "incumbent pair: score=$INC_SCORE model=${prev_adapter:-<base>} harness=$prev_harness"
fi

for (( k=START_ITER; k<=N_ITERS; k++ )); do
  TAG="${BASE}-i${k}"
  ADAPTER_OUT="$ROOT/outputs/sft/${BASE//-/_}_i${k}"
  CORPUS="${BASE//-/_}_i${k}"

  if [[ -n "$prev_adapter" && "$prev_adapter" != "BASE" ]]; then
    IN_MODEL="best_model"
  else
    IN_MODEL="base"
    prev_adapter=""
  fi

  # ── A0. rotate this iteration's evolve set ───────────────────────────────
  if [[ "$ROTATE_EVOLVE_TASKS" == "1" ]] && (( k >= ROTATE_FROM_ITER )); then
    if ! done_p "taskset-i${k}"; then
      time_stage "$k" "A0_rotate_tasks" rotate_task_set "$k"
      mark "taskset-i${k}"
    fi
    # Resume must reuse the recorded set — re-sampling would evolve iteration k
    # on a different 50 tasks than the trajectories already on disk came from.
    use_task_set_for_iter "$k" || {
      echo "ERROR: rotated task set for iter $k missing; delete $STATE/.done-taskset-i${k} to rebuild" >&2
      exit 2
    }
  else
    use_task_set_for_iter "$k" || record_task_set "$k" "$EVOLVE_TASKS_JSON" "$EVOLVE_ENVS_JSONL"
  fi

  log "════════ iteration $k / $N_ITERS ════════"
  log "  in-harness : $prev_harness"
  log "  in-model   : ${prev_adapter:-<base $MODEL_SIZE>}  (holdout ${INC_SCORE})"
  log "  evolve set : $EVOLVE_TASKS_JSON"

  # ── A. harness evolve ────────────────────────────────────────────────────
  if ! done_p "evolve-i${k}"; then
    # Resume if this iter's evolve tag already has state / partial trajs
    # (e.g. job cancelled mid-R0). Passing RESUME=0 would wipe the chance to
    # continue and re-burn a full 5-round evolve.
    ev_state="$ROOT/recipe/tb2_evolver/runs/$TAG/_meta_v2/_meta_scratch/harness_evolve_state.json"
    if [[ -f "$ev_state" || -d "$ROOT/.benchmarks/tmax/${TAG}-r0-traj" ]]; then
      ev_resume=1
      log "  partial evolve for $TAG detected — resuming"
    else
      ev_resume=0
    fi
    time_stage "$k" "A_evolve" \
      run_evolve "$TAG" "$EVOLVE_ROUNDS" "$prev_harness" "$prev_adapter" "$ev_resume"
    mark "evolve-i${k}"
  else
    log "skip evolve-i${k}"
  fi

  if cand_harness="$(resolve_evolved "$TAG")"; then
    log "  gated harness_$k = $cand_harness"
  else
    cand_harness="$prev_harness"
    log "  WARNING: no gated harness in iter $k; carrying previous harness"
  fi

  # ── B. holdout with incumbent model + candidate harness → harness ratchet ─
  harness_used="$prev_harness"
  BEFORE_SCORE="$INC_SCORE"
  if [[ "$cand_harness" == "$prev_harness" ]]; then
    log "  harness unchanged this iteration — reusing incumbent holdout ${INC_SCORE}"
  else
    time_stage "$k" "B_holdout_harness" \
      run_holdout_eval "${TAG}-B-harness" "$cand_harness" "$prev_adapter"
    record_score "$k" "B_holdout_harness" "$IN_MODEL" "harness_i${k}" "${TAG}-B-harness"
    SCORE_B="$(score_holdout "${TAG}-B-harness" | awk '{print $1}')"
    if [[ "$HARNESS_RATCHET" != "1" ]]; then
      harness_used="$cand_harness"
      BEFORE_SCORE="$SCORE_B"
      prev_harness="$cand_harness"
      (( SCORE_B > INC_SCORE )) && INC_SCORE="$SCORE_B"
      log "  HARNESS_RATCHET=0 — adopting harness_i${k} unconditionally (holdout ${SCORE_B})"
    elif ratchet_accept "$SCORE_B" "$INC_SCORE" "${TAG}-B-harness" "harness"; then
      log "  harness ACCEPTED: $RATCHET_REASON"
      harness_used="$cand_harness"
      prev_harness="$cand_harness"
      (( SCORE_B > INC_SCORE )) && INC_SCORE="$SCORE_B"
      BEFORE_SCORE="$INC_SCORE"
      printf '%s\t%s\t%s\n' "$k" "$SCORE_B" "$cand_harness" >>"$BEST_HARNESS_FILE"
      save_incumbent "$k" "B_harness"
    else
      log "  harness REJECTED: $RATCHET_REASON — keeping $prev_harness (holdout ${INC_SCORE})"
      harness_used="$prev_harness"
      BEFORE_SCORE="$INC_SCORE"
    fi
  fi

  # ── C/D/E with optional extra-evolve retries when SFT does not lift holdout ─
  attempt=0
  adopted=0
  while (( attempt <= MAX_SFT_RETRIES )); do
    CORPUS_ATTEMPT="${CORPUS}_a${attempt}"
    ADAPTER_ATTEMPT="${ADAPTER_OUT}_a${attempt}"
    # First attempt uses primary adapter path (cleaner for next INIT).
    if (( attempt == 0 )); then
      CORPUS_ATTEMPT="$CORPUS"
      ADAPTER_ATTEMPT="$ADAPTER_OUT"
    fi

    if ! done_p "corpus-i${k}-a${attempt}"; then
      # Collect tags for this corpus build. tags[0] must be this iteration's.
      corpus_tags=("$TAG")
      if [[ "$CUMULATIVE_CORPUS" == "1" ]]; then
        for (( pk=1; pk<k; pk++ )); do
          corpus_tags+=("${BASE}-i${pk}")
        done
      fi
      time_stage "$k" "C_corpus_a${attempt}" \
        build_corpus "$CORPUS_ATTEMPT" "${corpus_tags[@]}"
      mark "corpus-i${k}-a${attempt}"
    fi

    n_sel="$("$PY" -c "import json;print(json.load(open('$ROOT/recipe/tb2_sft/data/$CORPUS_ATTEMPT/summary.json'))['n_selected_trajs'])" 2>/dev/null || echo 0)"
    log "  corpus $CORPUS_ATTEMPT selected_trajs=$n_sel"
    if (( n_sel < 1 )); then
      log "  ERROR: empty corpus — cannot SFT"
      break
    fi

    if ! done_p "sft-i${k}-a${attempt}"; then
      # prev_adapter may be a LoRA dir (SFT) or a full HF ckpt (post-RL).
      sft_env=(
        SFT_DATASET_NAME="$CORPUS_ATTEMPT"
        SFT_OUTPUT_DIR="$ADAPTER_ATTEMPT"
        SFT_EPOCHS="$SFT_EPOCHS"
        SFT_GPUS="$GPU_POOL"
      )
      if [[ -n "$prev_adapter" && -f "$prev_adapter/adapter_config.json" ]]; then
        sft_env+=(INIT_ADAPTER="$prev_adapter")
      elif [[ -n "$prev_adapter" && -f "$prev_adapter/config.json" ]]; then
        # Continue SFT LoRA on top of the RL-merged full weights.
        sft_env+=(MODEL_OVERRIDE="$prev_adapter")
      fi
      time_stage "$k" "D_sft_a${attempt}" env "${sft_env[@]}" \
        bash "$ROOT/scripts/train_sft.sh"
      mark "sft-i${k}-a${attempt}"
    fi
    [[ -d "$ADAPTER_ATTEMPT" ]] || { echo "ERROR: no adapter at $ADAPTER_ATTEMPT" >&2; exit 2; }

    # Optional online RL (DPPO) on up to RL_N_TASKS taxonomy tasks — never holdout-102.
    EVAL_MODEL="$ADAPTER_ATTEMPT"
    RL_LABEL=""
    if [[ "$ENABLE_RL" == "1" ]]; then
      RL_NAME="${CORPUS_ATTEMPT}_rl"
      RL_OUT="$ROOT/outputs/rl/${BASE//-/_}_i${k}_a${attempt}"
      if ! done_p "rl-i${k}-a${attempt}"; then
        time_stage "$k" "D2_rl_a${attempt}" env \
          RL_DATASET_NAME="$RL_NAME" \
          RL_N_TASKS="$RL_N_TASKS" \
          HOLDOUT_TASKS_JSON="$HOLDOUT_TASKS_JSON" \
          PREFER_TASKS_JSON="$EVOLVE_TASKS_JSON" \
          ADAPTER_DIR="$ADAPTER_ATTEMPT" \
          RL_OUTPUT_DIR="$RL_OUT" \
          RL_EPISODES="$RL_EPISODES" \
          RL_GPUS="$GPU_POOL" \
          RL_EXP_NAME="${BASE}-i${k}-a${attempt}" \
          bash "$ROOT/scripts/train_rl_grpo.sh"
        mark "rl-i${k}-a${attempt}"
      fi
      # Check for the CHECKPOINT, not the directory: train_rl_grpo.sh mkdir -p's
      # RL_OUT before training, so `-d` is true even when grpo_fast crashed. And
      # run_holdout_eval falls back to the BASE model when it finds no
      # config.json — which would be recorded under a "..._rl" label. A silently
      # mislabelled score is worse than a loud skip.
      if [[ -f "$RL_OUT/config.json" ]]; then
        EVAL_MODEL="$RL_OUT"
        log "  RL ckpt ready → holdout will use $RL_OUT (train n<=$RL_N_TASKS, holdout excluded)"
        RL_LABEL="_rl"
      else
        log "  WARNING: ENABLE_RL=1 but no final checkpoint at $RL_OUT/config.json"
        log "           (step dirs: $(ls -d "$RL_OUT"/step_* 2>/dev/null | tr '\n' ' ' || true))"
        log "           → evaluating the SFT adapter instead, labelled without _rl"
        RL_LABEL=""
      fi
    fi

    job_e="${TAG}-E-sft-a${attempt}"
    time_stage "$k" "E_holdout_sft_a${attempt}" \
      run_holdout_eval "$job_e" "$harness_used" "$EVAL_MODEL"
    record_score "$k" "E_holdout_sft_a${attempt}" "sft_i${k}_a${attempt}${RL_LABEL}" "harness_used_i${k}" "$job_e"
    AFTER_SCORE="$(score_holdout "$job_e" | awk '{print $1}')"

    log "  holdout before(SFT)=${BEFORE_SCORE} after=${AFTER_SCORE} (accept_ties=${ACCEPT_TIES}, tol=${MODEL_RATCHET_TOL})"

    if ratchet_accept "$AFTER_SCORE" "$BEFORE_SCORE" "$job_e" "model"; then
      log "  model ACCEPTED: $RATCHET_REASON — adopting $EVAL_MODEL"
      # normalize primary adapter/ckpt path for next iter
      if [[ "$EVAL_MODEL" != "$ADAPTER_OUT" ]]; then
        rm -rf "$ADAPTER_OUT"
        cp -a "$EVAL_MODEL" "$ADAPTER_OUT"
      fi
      printf '%s\t%s\t%s\n' "$k" "$AFTER_SCORE" "$ADAPTER_OUT" >>"$BEST_FILE"
      prev_adapter="$ADAPTER_OUT"
      (( AFTER_SCORE > INC_SCORE )) && INC_SCORE="$AFTER_SCORE"
      adopted=1
      break
    fi

    log "  model REJECTED: $RATCHET_REASON"
    if (( attempt >= MAX_SFT_RETRIES )); then
      log "  max SFT retries reached — keeping incumbent model ${prev_adapter:-base}"
      break
    fi

    # Extra evolve rounds on the SAME tag to collect more success trajs, then retry SFT.
    attempt=$((attempt + 1))
    log "  collecting more data: +${EXTRA_EVOLVE_ROUNDS} evolve rounds (attempt $attempt)"
    if ! done_p "evolve-extra-i${k}-a${attempt}"; then
      # Resume extend: run.py --resume with more rounds from current state.
      # evolve_tmax uses NUM_ROUNDS as *this invocation's* round count; resume
      # continues from next_input_round. Request EXTRA rounds.
      time_stage "$k" "A_extra_evolve_a${attempt}" \
        run_evolve "$TAG" "$EXTRA_EVOLVE_ROUNDS" "$harness_used" "$prev_adapter" 1
      mark "evolve-extra-i${k}-a${attempt}"
    fi
    # The harness the model comparison is measured against stays fixed for the
    # whole iteration unless explicitly re-measured: swapping it between
    # attempts would compare an AFTER from harness B against a BEFORE from
    # harness A and credit the difference to the model.
    if newer="$(resolve_evolved "$TAG" 2>/dev/null)" && [[ "$newer" != "$harness_used" ]]; then
      if [[ "$EVAL_HARNESS_AFTER_EXTRA" == "1" ]]; then
        job_bx="${TAG}-B-harness-a${attempt}"
        time_stage "$k" "B_holdout_harness_a${attempt}" \
          run_holdout_eval "$job_bx" "$newer" "$prev_adapter"
        record_score "$k" "B_holdout_harness_a${attempt}" "$IN_MODEL" "harness_i${k}_a${attempt}" "$job_bx"
        SCORE_BX="$(score_holdout "$job_bx" | awk '{print $1}')"
        if ratchet_accept "$SCORE_BX" "$INC_SCORE" "$job_bx" "harness"; then
          log "  extra-round harness ACCEPTED: $RATCHET_REASON"
          harness_used="$newer"
          prev_harness="$newer"
          (( SCORE_BX > INC_SCORE )) && INC_SCORE="$SCORE_BX"
          BEFORE_SCORE="$INC_SCORE"
          printf '%s\t%s\t%s\n' "$k" "$SCORE_BX" "$newer" >>"$BEST_HARNESS_FILE"
        else
          log "  extra-round harness REJECTED: $RATCHET_REASON"
        fi
      else
        log "  note: extra rounds produced $newer (unmeasured — not adopted; set EVAL_HARNESS_AFTER_EXTRA=1 to gate it)"
      fi
    fi
  done

  if (( ! adopted )); then
    log "  iteration $k: model unchanged (${prev_adapter:-base}); harness = $prev_harness"
  fi
  save_incumbent "$k" "end"
  log "  incumbent after iter $k: score=$INC_SCORE model=${prev_adapter:-<base>} harness=$prev_harness"
done

trap - ERR
printf 'DONE\nfinished: %s\n' "$(date '+%F %T')" >"$STATUS_FILE"
log "════════ loop complete ════════"
echo
column -t "$SCORES" || cat "$SCORES"
echo
column -t "$TIMINGS" || cat "$TIMINGS"
echo
echo "best models:"
column -t "$BEST_FILE" || cat "$BEST_FILE"
echo
echo "best harnesses:"
column -t "$BEST_HARNESS_FILE" || cat "$BEST_HARNESS_FILE"
echo
echo "incumbent trail:"
column -t "$INCUMBENT_FILE" || cat "$INCUMBENT_FILE"
echo
echo "evolve task sets:"
column -t "$SETLOG" || cat "$SETLOG"
