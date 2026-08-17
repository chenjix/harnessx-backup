#!/usr/bin/env bash
# Tmax harness ↔ SFT co-evolution loop.
#
# Goal: keep raising the *model* baseline. Each outer iteration:
#   A  harness evolve on 50 tasks (EVOLVE_ROUNDS, default 5)
#      model_{k-1} + harness_{k-1} → harness_k (+ success trajs)
#   B  filter 50–100 high-quality unique success trajs → SFT corpus_k
#   C  quick LoRA SFT (continue from model_{k-1} when present) → model_k
#   C2 optional online RL (DPPO/GRPO-fast) on ≤RL_N_TASKS taxonomy tasks
#      (prefers evolve-50, excludes holdout-102) → model_k_rl
#      NEVER trains on holdout-102 (eval-only; matches official Tmax).
#   D  holdout-102 eval: model_k(+rl) + harness_k
#   E  ratchet:
#        if holdout improved vs incumbent → adopt model_k for next evolve
#        else reject model_k, run EXTRA_EVOLVE_ROUNDS more, rebuild corpus,
#             re-SFT / re-eval (up to MAX_SFT_RETRIES), then move on
#
# Usage:
#   REPLICATE=1 CLEAN_STALE=1 bash scripts/run_loop_tmax_coevolve.sh
#   REPLICATE=1 START_ITER=2 bash scripts/run_loop_tmax_coevolve.sh
#   REPLICATE=1 N_ITERS=1 EVOLVE_ROUNDS=2 bash scripts/run_loop_tmax_coevolve.sh  # smoke
#
# Key env:
#   REPLICATE          required; isolates output paths
#   N_ITERS=3
#   EVOLVE_ROUNDS=5
#   EXTRA_EVOLVE_ROUNDS=2   # appended when SFT does not improve holdout
#   MAX_SFT_RETRIES=2
#   MIN_TRAJS=50 MAX_TRAJS=100
#   MODEL_RATCHET_TOL=0     # require strict holdout improvement (tasks)
#   INIT_LORA_PATH=...      # optional warm start (e.g. SFT-500 adapter)
#   SEED_HARNESS=...        # optional; default baseline_tmax_harness.yaml
#   HOLDOUT_TASKS_JSON=.../tasks_tmax_only200.json
#   EVOLVE_TASKS_JSON=.../tasks_tmax_evolve50_list.json
#   ENABLE_RL=0|1           # after SFT, run official-style DPPO (train≠holdout)
#   RL_EPISODES=512         # keep small inside the coevolve loop; scale up offline
#   RL_N_TASKS=100          # taxonomy sample size for RL (excludes holdout-102)

set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
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
export SFT_PYTHON="${SFT_PYTHON:-${PYTHON_BIN:-$HOME/.venv/bin/python}}"

PY="${PYTHON_BIN:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "${VLLM_VENV:-$HOME/.venv}/bin/python" ]]; then
    PY="${VLLM_VENV:-$HOME/.venv}/bin/python"
  else
    PY="$(command -v python3)"
  fi
fi
export PYTHON_BIN="$PY"
export SFT_PYTHON="${SFT_PYTHON:-$PY}"

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
total = len(json.loads(tj.read_text()))
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
  PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" \
    "$PY" -m recipe.tb2_sft.src.build_tmax_evolve_sft "${args[@]}"
}

# ── bootstrap incumbent ─────────────────────────────────────────────────────
prev_harness="$BASELINE_HARNESS"
prev_adapter="${INIT_LORA_PATH:-}"
BEST_FILE="$STATE/best_model.tsv"
[[ -f "$BEST_FILE" ]] || printf 'iter\tscore\tadapter\n' >"$BEST_FILE"

if (( START_ITER > 1 )); then
  p=$(( START_ITER - 1 ))
  if new="$(resolve_evolved "${BASE}-i${p}" 2>/dev/null)"; then
    prev_harness="$new"
  fi
  cand="$ROOT/outputs/sft/${BASE//-/_}_i${p}"
  [[ -d "$cand" ]] && prev_adapter="$cand"
  BEST_SCORE="$(awk -F'\t' 'NR>1 {print $2}' "$BEST_FILE" | sort -n | tail -1)"
  BEST_ADAPTER="$(awk -F'\t' -v b="${BEST_SCORE:--1}" 'NR>1 && $2==b {a=$3} END{print a}' "$BEST_FILE")"
  [[ -n "${BEST_ADAPTER:-}" ]] && prev_adapter="$BEST_ADAPTER"
  log "resuming at iter $START_ITER harness=$prev_harness adapter=${prev_adapter:-<base>}"
fi

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

for (( k=START_ITER; k<=N_ITERS; k++ )); do
  TAG="${BASE}-i${k}"
  ADAPTER_OUT="$ROOT/outputs/sft/${BASE//-/_}_i${k}"
  CORPUS="${BASE//-/_}_i${k}"

  if [[ -n "$prev_adapter" && "$prev_adapter" != "BASE" ]]; then
    IN_MODEL="sft_i$((k-1))"
  else
    IN_MODEL="base"
    prev_adapter=""
  fi

  log "════════ iteration $k / $N_ITERS ════════"
  log "  in-harness : $prev_harness"
  log "  in-model   : ${prev_adapter:-<base $MODEL_SIZE>}"

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

  if new_harness="$(resolve_evolved "$TAG")"; then
    log "  gated harness_$k = $new_harness"
  else
    new_harness="$prev_harness"
    log "  WARNING: no gated harness in iter $k; carrying previous harness"
  fi

  # Holdout with prev model + new harness (isolates harness contribution)
  time_stage "$k" "B_holdout_harness" \
    run_holdout_eval "${TAG}-B-harness" "$new_harness" "$prev_adapter"
  record_score "$k" "B_holdout_harness" "$IN_MODEL" "harness_i${k}" "${TAG}-B-harness"
  BEFORE_SCORE="$(score_holdout "${TAG}-B-harness" | awk '{print $1}')"

  # ── B/C/D with optional extra-evolve retries when SFT does not lift holdout ─
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
      # Collect tags for this corpus build.
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
      if [[ -d "$RL_OUT" ]]; then
        EVAL_MODEL="$RL_OUT"
        log "  RL ckpt ready → holdout will use $RL_OUT (train n<=$RL_N_TASKS, holdout excluded)"
      else
        log "  WARNING: ENABLE_RL=1 but missing $RL_OUT — falling back to SFT adapter"
      fi
    fi

    job_e="${TAG}-E-sft-a${attempt}"
    time_stage "$k" "E_holdout_sft_a${attempt}" \
      run_holdout_eval "$job_e" "$new_harness" "$EVAL_MODEL"
    record_score "$k" "E_holdout_sft_a${attempt}" "sft_i${k}_a${attempt}${ENABLE_RL:+_rl}" "harness_i${k}" "$job_e"
    AFTER_SCORE="$(score_holdout "$job_e" | awk '{print $1}')"

    log "  holdout before(SFT)=${BEFORE_SCORE} after=${AFTER_SCORE} (tol=${MODEL_RATCHET_TOL})"

    # Raise-the-floor: default tol=0 requires AFTER > BEFORE.
    # tol>0 also accepts flat scores (AFTER == BEFORE) as "no regression".
    improved=0
    if (( AFTER_SCORE > BEFORE_SCORE )); then
      improved=1
    elif (( MODEL_RATCHET_TOL > 0 )) && (( AFTER_SCORE >= BEFORE_SCORE )); then
      improved=1
    fi

    if (( improved )); then
      log "  model IMPROVED ${BEFORE_SCORE} → ${AFTER_SCORE} — adopting $EVAL_MODEL"
      # normalize primary adapter/ckpt path for next iter
      if [[ "$EVAL_MODEL" != "$ADAPTER_OUT" ]]; then
        rm -rf "$ADAPTER_OUT"
        cp -a "$EVAL_MODEL" "$ADAPTER_OUT"
      fi
      printf '%s\t%s\t%s\n' "$k" "$AFTER_SCORE" "$ADAPTER_OUT" >>"$BEST_FILE"
      prev_adapter="$ADAPTER_OUT"
      adopted=1
      break
    fi

    log "  model DID NOT improve (${BEFORE_SCORE} → ${AFTER_SCORE})"
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
        run_evolve "$TAG" "$EXTRA_EVOLVE_ROUNDS" "$new_harness" "$prev_adapter" 1
      mark "evolve-extra-i${k}-a${attempt}"
    fi
    if newer="$(resolve_evolved "$TAG")"; then
      new_harness="$newer"
    fi
  done

  if (( ! adopted )); then
    log "  iteration $k: model unchanged (${prev_adapter:-base}); harness advances to $new_harness"
  fi
  prev_harness="$new_harness"
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
