#!/usr/bin/env bash
# TB2 89-task harness <-> SFT co-evolution loop.
# The task set is published as terminal-bench@2.0; there is no 2.1 in the registry.
#
# Port of scripts/tmax/run_loop_tmax_coevolve.sh to the Harbor/TB2 backend. The
# control logic -- both ratchets, the tie rule, task rotation, the corpus floor
# and its top-ups -- is deliberately identical, so a TB2.1 result is comparable
# with the Tmax chain rather than being its own bespoke experiment. What differs
# is forced by the benchmark:
#
#   task universe   89 fixed tasks, so stage S splits them once into an evolve
#                   pool and a scoring holdout instead of drawing from a
#                   thousands-strong taxonomy. Rotation refills from the pool's
#                   unused remainder and is therefore exhaustible by design.
#   containers      Harbor resolves them from --dataset; there is no envs JSONL
#                   to build or pass (Tmax needs one per task set).
#   trajectories    nested <task>__<trial>/result.json, read by
#                   build_tb2_evolve_sft.py (the Tmax builder globs flat files
#                   and would silently find nothing).
#   system errors   TB2 has no "agent_error" status. The equivalent is a trial
#                   that raised (exception_info) or produced zero output tokens,
#                   which is the signature of a provider/transport failure
#                   rather than an honest task failure. Both feed the tie rule.
#
#   S  split 89 -> evolve pool + holdout, stratified on a one-off baseline eval
#      of all 89 tasks. That same eval supplies the anchor score (restricted to
#      the holdout ids), so the split costs one eval, not two.
#   A0 rotate the evolve set: retire tasks that already handed a correct
#      trajectory to an SFT corpus, carry the unsolved ones, refill from the pool
#   A  harness evolve on that set: best_model + best_harness -> harness_k
#   B  holdout with best_model + harness_k -> isolates the harness delta
#   C  filter success trajs -> SFT corpus_k; top up a thin corpus with extra
#      evolve rounds rather than training on too little
#   D  LoRA SFT continuing from best_model -> model_k
#   E  holdout with model_k + harness_k -> isolates the model delta
#
# Usage:
#   REPLICATE=1 bash scripts/tb2/run_loop_tb21_coevolve.sh
#   REPLICATE=1 START_ITER=2 bash scripts/tb2/run_loop_tb21_coevolve.sh
#   REPLICATE=9 N_ITERS=1 EVOLVE_ROUNDS=2 bash scripts/tb2/run_loop_tb21_coevolve.sh  # smoke
#
# Key env (defaults tuned for the 89-task universe; see the sbatch header):
#   REPLICATE            required; isolates output paths
#   N_ITERS=3  EVOLVE_ROUNDS=5  EXTRA_EVOLVE_ROUNDS=2  MAX_SFT_RETRIES=2
#   N_HOLDOUT=40         holdout size; pool is 89-N_HOLDOUT
#   EVOLVE_N_TASKS=18    tasks per iteration, drawn from the pool
#   MIN_TRAJS=30         corpus floor  MAX_TOPUP_ROUNDS=3  MAX_TRAJS=0 (no cap)
#   RATCHET_ALLOW_TIE=1  SYSERR_TOL=0  TIE_MAX_SYSERR=0  MODEL_RATCHET_TOL=0
#   HARNESS_RATCHET=1    ROTATE_EVOLVE_TASKS=1
#   TB2_DATASET=terminal-bench@2.0

set -Eeuo pipefail

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
# Floor on usable SFT trajectories. Lower than the Tmax chain's 80 because an
# 18-task evolve set cannot physically produce that many: loop3 measured ~2.5
# kept trajs per solved task, so a first iteration yields roughly 30-40. The
# corpus is cumulative across iterations, so only iteration 1 is really at risk.
MIN_TRAJS="${MIN_TRAJS:-30}"
MAX_TOPUP_ROUNDS="${MAX_TOPUP_ROUNDS:-3}"
MAX_TRAJS="${MAX_TRAJS:-0}"
PER_TASK="${PER_TASK:-3}"
MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
RATCHET_ALLOW_TIE="${RATCHET_ALLOW_TIE:-1}"
SYSERR_TOL="${SYSERR_TOL:-0}"
TIE_MAX_SYSERR="${TIE_MAX_SYSERR:-0}"
HARNESS_RATCHET="${HARNESS_RATCHET:-1}"
ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
# 18 of a 49-task pool survives three iterations at every plausible solve rate.
# Larger sets exhaust the pool: at the 89% solve rate loop3 measured, n=25 leaves
# iteration 3 with about 4 tasks, which is no longer an experiment.
EVOLVE_N_TASKS="${EVOLVE_N_TASKS:-18}"
EVOLVE_TASK_SEED="${EVOLVE_TASK_SEED:-42}"
N_HOLDOUT="${N_HOLDOUT:-40}"
SPLIT_SEED="${SPLIT_SEED:-42}"
SFT_EPOCHS="${SFT_EPOCHS:-2}"
CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"

export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"
case "$MODEL_SIZE" in
  27b) export MODEL_TAG="${MODEL_TAG:-qwen36}" ;;
  *)   export MODEL_TAG="${MODEL_TAG:-qwen35}" ;;
esac

export TB2_DATASET="${TB2_DATASET:-terminal-bench@2.0}"
ALL_TASKS_JSON="${ALL_TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_all_tb2.json}"
SPLIT_DIR="${SPLIT_DIR:-$ROOT/recipe/tb2_evolver/tb21_split_rep${REPLICATE}}"
POOL_TASKS_JSON="$SPLIT_DIR/tasks_tb21_evolve_pool.json"
HOLDOUT_TASKS_JSON="$SPLIT_DIR/tasks_tb21_holdout.json"

export TB2_MAX_STEPS="${TB2_MAX_STEPS:-120}"
export TB2_CONCURRENT="${TB2_CONCURRENT:-2}"
HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-2}"
export TB2_TEMPERATURE="${TB2_TEMPERATURE:-0}"
export REGRESSION_TOLERANCE="${REGRESSION_TOLERANCE:-0.04}"
export EVOLVE_NOOP_ON_META_FAIL="${EVOLVE_NOOP_ON_META_FAIL:-1}"
# Each TB2 task pulls a multi-GB image onto the node's root disk, which a
# 40-89 task sweep will otherwise fill mid-run.
export TB2_DELETE_IMAGES="${TB2_DELETE_IMAGES:-1}"
export TB2_PRUNE_EVERY="${TB2_PRUNE_EVERY:-10}"
export TB2_MIN_FREE_GB="${TB2_MIN_FREE_GB:-30}"

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

BASE="tb21-coev-rep${REPLICATE}"
STATE="$ROOT/outputs/tb21_coevolve/rep${REPLICATE}"
BENCH="$ROOT/.benchmarks/tb2"
mkdir -p "$STATE"
TIMINGS="$STATE/timings.tsv"
SCORES="$STATE/scores.tsv"
[[ -f "$TIMINGS" ]] || printf 'iter\tstage\tseconds\tstarted\n' >"$TIMINGS"
[[ -f "$SCORES" ]]  || printf 'iter\tstage\tmodel\tharness\tpassed\ttotal\tjob\n' >"$SCORES"

BASELINE_HARNESS="${SEED_HARNESS:-$ROOT/configs/baseline_harness.yaml}"
LORA_SERVE_NAME="${MODEL_TAG}-${MODEL_SIZE}-tb21-coev-r${REPLICATE}"

log() { printf '\n\033[1m[tb21-coev rep%s] %s\033[0m\n' "$REPLICATE" "$*"; }

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
  printf '\n\033[1;31m[tb21-coev rep%s] FAILED in %s (exit %s)\033[0m\n' \
    "$REPLICATE" "$CURRENT_STAGE" "$rc"
  printf 'Rerun the same command to resume — completed stages are skipped via .done markers.\n'
}
trap on_err ERR

LOCKFILE="$STATE/.lock"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "ERROR: another tb21-coevolve chain with REPLICATE=$REPLICATE is already running." >&2
  exit 2
fi
printf 'RUNNING\nstarted: %s\npid: %s\n' "$(date '+%F %T')" "$$" >"$STATUS_FILE"

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

[[ -f "$ALL_TASKS_JSON" ]] || { echo "ERROR: missing $ALL_TASKS_JSON" >&2; exit 2; }
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

# score_job <job_name> [tasks_json] -> "passed\ttotal"
# Restricting to a task list lets the 89-task baseline double as the holdout
# anchor, which saves a whole eval cycle at the start of the chain.
score_job() {
  "$PY" - "$BENCH/$1" "${2:-}" <<'PY'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
want = None
if len(sys.argv) > 2 and sys.argv[2]:
    tj = Path(sys.argv[2])
    if tj.is_file():
        want = {str(t) for t in json.loads(tj.read_text())}
seen = {}
if d.is_dir():
    for rp in d.glob("*/result.json"):
        try:
            o = json.loads(rp.read_text())
        except Exception:
            continue
        task = str(o.get("task_name") or rp.parent.name.split("__")[0])
        if want is not None and task not in want:
            continue
        r = ((o.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        ok = isinstance(r, (int, float)) and r >= 1.0
        # A task retried across shards counts as passed if any attempt passed.
        seen[task] = seen.get(task, False) or ok
total = len(want) if want is not None else len(seen)
print(f"{sum(1 for v in seen.values() if v)}\t{total}")
PY
}

# count_sys_errors <job_name> [tasks_json] -> int
# TB2 has no agent_error status, so this reconstructs the same idea: a trial
# that raised, or a requested task that never reported a result at all (a dead
# shard, an image that would not pull). Both are infrastructure failures rather
# than honest attempts, which is what the tie rule needs to know.
#
# Deliberately NOT using agent_result.n_output_tokens as a failure signal: the
# HarnessX agent never reports it back to Harbor (measured across a full 28-task
# job: n_input_tokens populated, n_output_tokens == 0 on 28/28). Treating zero
# as "provider failure" would mark every task a system error and make any tie
# unreachable under TIE_MAX_SYSERR=0.
count_sys_errors() {
  "$PY" - "$BENCH/$1" "${2:-}" <<'PY'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
want = None
if len(sys.argv) > 2 and sys.argv[2]:
    tj = Path(sys.argv[2])
    if tj.is_file():
        want = {str(t) for t in json.loads(tj.read_text())}
bad: set[str] = set()
reported: set[str] = set()
if d.is_dir():
    for rp in d.glob("*/result.json"):
        try:
            o = json.loads(rp.read_text())
        except Exception:
            bad.add(rp.parent.name)
            continue
        task = str(o.get("task_name") or rp.parent.name.split("__")[0])
        if want is not None and task not in want:
            continue
        reported.add(task)
        if o.get("exception_info") is not None:
            bad.add(task)
if want is not None:
    bad |= (want - reported)
print(len(bad))
PY
}

ratchet_ok() {  # ratchet_ok <new_score> <new_err> <base_score> <base_err>
  local ns="$1" ne="$2" bs="$3" be="$4"
  if (( ns > bs + MODEL_RATCHET_TOL )); then
    return 0
  fi
  if (( ns < bs )); then
    return 1
  fi
  if [[ "$RATCHET_ALLOW_TIE" != "1" ]]; then
    return 1
  fi
  if (( ne > be + SYSERR_TOL )); then
    log "  tie rejected: sys_error ${ne} > incumbent ${be} + tol ${SYSERR_TOL}"
    return 1
  fi
  if (( ne > TIE_MAX_SYSERR )); then
    log "  tie rejected: ${ne} system error(s) > TIE_MAX_SYSERR=${TIE_MAX_SYSERR}"
    return 1
  fi
  return 0
}

record_score() {  # record_score <iter> <stage> <model> <harness> <job> [tasks_json]
  if awk -F'\t' -v i="$1" -v st="$2" 'NR>1 && $1==i && $2==st {found=1} END{exit !found}' "$SCORES"; then
    log "score for iter=$1 stage=$2 already recorded — not re-appending"
    return 0
  fi
  local sc; sc="$(score_job "$5" "${6:-$HOLDOUT_TASKS_JSON}")"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$sc" "$5" >>"$SCORES"
  log "SCORE  iter=$1 stage=$2 model=$3 harness=$4 -> $sc"
}

run_tb2_eval() {  # run_tb2_eval <job> <harness_cfg> <tasks_json> <adapter_or_empty>
  local job="$1" cfg="$2" tasks="$3" ad="${4:-}"
  if done_p "$job"; then log "skip eval $job (already done)"; return 0; fi
  (
    export JOB_NAME="$job"
    export RUN_TAG="$job"
    export TASKS_JSON="$tasks"
    export HARNESS_CONFIG="$cfg"
    # The loop always names the harness explicitly; letting evaluate.sh resolve
    # one from this tag's evolve state would score a different config.
    export USE_EVOLVED_HARNESS=0
    export TB2_CONCURRENT="$HOLDOUT_CONCURRENT"
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
    bash "$ROOT/scripts/evaluate.sh"
  )
  mark "$job"
}

run_evolve() {  # run_evolve <run_tag> <rounds> <seed_harness> <adapter_or_empty> <resume 0|1>
  local tag="$1" rounds="$2" seed="$3" ad="${4:-}" resume="${5:-0}"
  (
    export RUN_TAG="$tag"
    export TASKS_JSON="$EVOLVE_TASKS_JSON"
    export NUM_ROUNDS="$rounds"
    export EVOLVE_ROUNDS="$rounds"
    export SEED_HARNESS="$seed"
    export RESUME="$resume"
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
    bash "$ROOT/scripts/evolve.sh"
  )
}

build_corpus() {  # build_corpus <dataset_name> <run_tag...>
  local name="$1"; shift
  local args=(--name "$name" --min-trajs "$MIN_TRAJS" --max-trajs "$MAX_TRAJS"
              --per-task "$PER_TASK"
              --exclude-tasks "$HOLDOUT_TASKS_JSON")
  local t
  for t in "$@"; do
    args+=(--run-tag "$t")
  done
  PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" \
    "$PY" -m recipe.tb2_sft.src.build_tb2_evolve_sft "${args[@]}"
}

corpus_size() {  # corpus_size <dataset_name> -> n_selected_trajs (0 when absent)
  "$PY" - "$ROOT/recipe/tb2_sft/data/$1/summary.json" <<'PY' 2>/dev/null || echo 0
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
print(int(json.loads(p.read_text())["n_selected_trajs"]) if p.is_file() else 0)
PY
}

# ── S. baseline scan + split ────────────────────────────────────────────────
# One eval of all 89 tasks does double duty: it stratifies the split so both
# halves have comparable difficulty, and (restricted to the holdout ids) it is
# the anchor the ratchets measure against.
SCAN_JOB="${BASE}-scan89"
if ! done_p "split"; then
  time_stage 0 "S_scan_all89" \
    run_tb2_eval "$SCAN_JOB" "$BASELINE_HARNESS" "$ALL_TASKS_JSON" "${INIT_LORA_PATH:-}"
  time_stage 0 "S_split" \
    "$PY" "$ROOT/scripts/tb2/split_tb21_tasks.py" \
      --tasks "$ALL_TASKS_JSON" \
      --baseline-job "$SCAN_JOB" \
      --bench-root "$BENCH" \
      --n-holdout "$N_HOLDOUT" \
      --seed "$SPLIT_SEED" \
      --out-dir "$SPLIT_DIR"
  mark "split"
fi
[[ -f "$POOL_TASKS_JSON" ]] || { echo "ERROR: missing evolve pool $POOL_TASKS_JSON" >&2; exit 2; }
[[ -f "$HOLDOUT_TASKS_JSON" ]] || { echo "ERROR: missing holdout $HOLDOUT_TASKS_JSON" >&2; exit 2; }

# Iteration 1 draws its evolve set from the pool like every other iteration.
export EVOLVE_TASKS_JSON="$POOL_TASKS_JSON"

# ── bootstrap incumbent ─────────────────────────────────────────────────────
prev_harness="$BASELINE_HARNESS"
prev_adapter="${INIT_LORA_PATH:-}"
BEST_FILE="$STATE/best_model.tsv"
[[ -f "$BEST_FILE" ]] || printf 'iter\tscore\tadapter\n' >"$BEST_FILE"

INCUMBENT_FILE="$STATE/incumbent.tsv"
if [[ -f "$INCUMBENT_FILE" ]]; then
  INCUMBENT_SCORE="$(awk -F'\t' 'END{print $1}' "$INCUMBENT_FILE")"
  INCUMBENT_ERR="$(awk -F'\t' 'END{print $2}' "$INCUMBENT_FILE")"
  BEST_HARNESS="$(awk -F'\t' 'END{print $3}' "$INCUMBENT_FILE")"
  log "restored incumbent: score=$INCUMBENT_SCORE sys_err=$INCUMBENT_ERR harness=$BEST_HARNESS"
else
  INCUMBENT_SCORE=-1
  INCUMBENT_ERR=0
  BEST_HARNESS="$BASELINE_HARNESS"
fi
[[ -n "${BEST_HARNESS:-}" && -f "$BEST_HARNESS" ]] || BEST_HARNESS="$BASELINE_HARNESS"

save_incumbent() {  # save_incumbent <score> <errors> <harness>
  INCUMBENT_SCORE="$1"; INCUMBENT_ERR="$2"; BEST_HARNESS="$3"
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$(date '+%F %T')" >>"$INCUMBENT_FILE"
}

best_adapter_from_file() {
  awk -F'\t' 'NR>1 && $3 != "" && $3 != "BASE" {
                if (best == "" || $2+0 > top+0) { top = $2; best = $3 }
              } END { print best }' "$BEST_FILE"
}

# Every iteration starts from the best (model, harness) pair seen so far.
if (( START_ITER > 1 )); then
  cand="$ROOT/outputs/sft/${BASE//-/_}_i$(( START_ITER - 1 ))"
  [[ -d "$cand" ]] && prev_adapter="$cand"
fi
cand="$(best_adapter_from_file || true)"
[[ -n "$cand" && -d "$cand" ]] && prev_adapter="$cand"

if [[ "$HARNESS_RATCHET" == "1" ]]; then
  prev_harness="$BEST_HARNESS"
elif (( START_ITER > 1 )); then
  if new="$(resolve_evolved "${BASE}-i$(( START_ITER - 1 ))" 2>/dev/null)"; then
    prev_harness="$new"
  fi
fi
if (( START_ITER > 1 )); then
  log "resuming at iter $START_ITER harness=$prev_harness adapter=${prev_adapter:-<base>}"
fi

# Anchor: the 89-task scan restricted to the holdout half. No extra eval.
if ! done_p "anchor0"; then
  record_score 0 "holdout_anchor" "${prev_adapter:+init_lora}${prev_adapter:-base}" \
    "harness0" "$SCAN_JOB" "$HOLDOUT_TASKS_JSON"
  if ! awk -F'\t' 'NR>1{found=1} END{exit !found}' "$BEST_FILE"; then
    sc="$(score_job "$SCAN_JOB" "$HOLDOUT_TASKS_JSON" | awk '{print $1}')"
    printf '%s\t%s\t%s\n' "0" "$sc" "${prev_adapter:-BASE}" >>"$BEST_FILE"
  fi
  mark "anchor0"
fi
if (( INCUMBENT_SCORE < 0 )); then
  a_sc="$(score_job "$SCAN_JOB" "$HOLDOUT_TASKS_JSON" | awk '{print $1}')"
  a_er="$(count_sys_errors "$SCAN_JOB" "$HOLDOUT_TASKS_JSON")"
  save_incumbent "$a_sc" "$a_er" "$prev_harness"
  log "incumbent anchored: score=$a_sc sys_err=$a_er"
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

  # ── A0. rotate the evolve task set ───────────────────────────────────────
  if [[ "$ROTATE_EVOLVE_TASKS" == "1" ]]; then
    EVOSET="${BASE//-/_}_i${k}_evolveset"
    EVOSET_DIR="$ROOT/recipe/tb2_sft/data/$EVOSET"
    if ! done_p "evoset-i${k}"; then
      prev_list=""
      # Keyed on the absolute iteration, not START_ITER: resuming at iter 3 must
      # still carry iter 2's unsolved tasks forward.
      if (( k > 1 )); then
        cand="$ROOT/recipe/tb2_sft/data/${BASE//-/_}_i$((k-1))_evolveset/tasks_list_flat.json"
        [[ -f "$cand" ]] && prev_list="$cand"
      fi
      time_stage "$k" "A0_evolve_taskset" \
        "$PY" "$ROOT/scripts/tb2/build_iter_evolve_tasks_tb21.py" \
          --name "$EVOSET" \
          --n-tasks "$EVOLVE_N_TASKS" \
          --seed "$((EVOLVE_TASK_SEED + k))" \
          --pool "$POOL_TASKS_JSON" \
          --holdout-tasks "$HOLDOUT_TASKS_JSON" \
          ${prev_list:+--prev-tasks "$prev_list"} \
          --corpus-glob "$ROOT/recipe/tb2_sft/data/${BASE//-/_}_i*/summary.json"
      mark "evoset-i${k}"
    fi
    if [[ -f "$EVOSET_DIR/tasks_list_flat.json" ]]; then
      export EVOLVE_TASKS_JSON="$EVOSET_DIR/tasks_list_flat.json"
      log "  evolve set : $EVOSET ($("$PY" -c "import json;print(len(json.load(open('$EVOSET_DIR/tasks_list_flat.json'))))") tasks)"
    else
      log "  WARNING: rotation produced no task list — falling back to $EVOLVE_TASKS_JSON"
    fi
  fi

  log "  in-harness : $prev_harness"
  log "  in-model   : ${prev_adapter:-<base $MODEL_SIZE>}"
  log "  incumbent  : score=$INCUMBENT_SCORE sys_err=$INCUMBENT_ERR"

  # ── A. harness evolve ────────────────────────────────────────────────────
  if ! done_p "evolve-i${k}"; then
    ev_state="$ROOT/recipe/tb2_evolver/runs/$TAG/_meta_v2/_meta_scratch/harness_evolve_state.json"
    if [[ -f "$ev_state" || -d "$BENCH/${TAG}-r0-traj" ]]; then
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

  # ── B. holdout with prev model + new harness ─────────────────────────────
  time_stage "$k" "B_holdout_harness" \
    run_tb2_eval "${TAG}-B-harness" "$new_harness" "$HOLDOUT_TASKS_JSON" "$prev_adapter"
  record_score "$k" "B_holdout_harness" "$IN_MODEL" "harness_i${k}" "${TAG}-B-harness"
  BEFORE_SCORE="$(score_job "${TAG}-B-harness" "$HOLDOUT_TASKS_JSON" | awk '{print $1}')"
  BEFORE_ERR="$(count_sys_errors "${TAG}-B-harness" "$HOLDOUT_TASKS_JSON")"

  # ── harness ratchet ──────────────────────────────────────────────────────
  if [[ "$HARNESS_RATCHET" == "1" && "$new_harness" != "$prev_harness" ]]; then
    if ratchet_ok "$BEFORE_SCORE" "$BEFORE_ERR" "$INCUMBENT_SCORE" "$INCUMBENT_ERR"; then
      log "  harness ADOPTED ${INCUMBENT_SCORE} → ${BEFORE_SCORE} (sys_err ${INCUMBENT_ERR} → ${BEFORE_ERR})"
      save_incumbent "$BEFORE_SCORE" "$BEFORE_ERR" "$new_harness"
    else
      log "  harness REJECTED ${BEFORE_SCORE} vs incumbent ${INCUMBENT_SCORE} — reverting to $BEST_HARNESS"
      new_harness="$BEST_HARNESS"
      BEFORE_SCORE="$INCUMBENT_SCORE"
      BEFORE_ERR="$INCUMBENT_ERR"
    fi
  elif [[ "$HARNESS_RATCHET" == "1" ]]; then
    if (( BEFORE_SCORE > INCUMBENT_SCORE )); then
      save_incumbent "$BEFORE_SCORE" "$BEFORE_ERR" "$new_harness"
    fi
  fi

  # ── C/D/E with retries when SFT does not lift holdout ────────────────────
  attempt=0
  adopted=0
  while (( attempt <= MAX_SFT_RETRIES )); do
    CORPUS_ATTEMPT="${CORPUS}_a${attempt}"
    ADAPTER_ATTEMPT="${ADAPTER_OUT}_a${attempt}"
    if (( attempt == 0 )); then
      CORPUS_ATTEMPT="$CORPUS"
      ADAPTER_ATTEMPT="$ADAPTER_OUT"
    fi

    corpus_tags=("$TAG")
    if [[ "$CUMULATIVE_CORPUS" == "1" ]]; then
      for (( pk=1; pk<k; pk++ )); do
        corpus_tags+=("${BASE}-i${pk}")
      done
    fi

    if ! done_p "corpus-i${k}-a${attempt}"; then
      time_stage "$k" "C_corpus_a${attempt}" \
        build_corpus "$CORPUS_ATTEMPT" "${corpus_tags[@]}"
      mark "corpus-i${k}-a${attempt}"
    fi
    n_sel="$(corpus_size "$CORPUS_ATTEMPT")"
    log "  corpus $CORPUS_ATTEMPT selected_trajs=$n_sel (floor $MIN_TRAJS)"

    # ── top up a thin corpus ───────────────────────────────────────────────
    # The harness is deliberately left alone: these rounds buy data, and
    # swapping it would break the E-vs-B comparison the model ratchet uses.
    topup=0
    while (( n_sel < MIN_TRAJS && topup < MAX_TOPUP_ROUNDS )); do
      topup=$((topup + 1))
      log "  corpus below floor ($n_sel < $MIN_TRAJS) — +${EXTRA_EVOLVE_ROUNDS} evolve rounds (top-up $topup/$MAX_TOPUP_ROUNDS)"
      topup_ran=0
      if ! done_p "evolve-topup-i${k}-a${attempt}-t${topup}"; then
        time_stage "$k" "A_topup_evolve_a${attempt}_t${topup}" \
          run_evolve "$TAG" "$EXTRA_EVOLVE_ROUNDS" "$new_harness" "$prev_adapter" 1
        mark "evolve-topup-i${k}-a${attempt}-t${topup}"
        topup_ran=1
      fi
      if ! done_p "corpus-i${k}-a${attempt}-t${topup}"; then
        time_stage "$k" "C_corpus_a${attempt}_t${topup}" \
          build_corpus "$CORPUS_ATTEMPT" "${corpus_tags[@]}"
        mark "corpus-i${k}-a${attempt}-t${topup}"
        topup_ran=1
      fi
      prev_n_sel="$n_sel"
      n_sel="$(corpus_size "$CORPUS_ATTEMPT")"
      log "  corpus $CORPUS_ATTEMPT selected_trajs=${prev_n_sel} → ${n_sel}"
      # Only a round that actually ran can prove the well is dry; a fully
      # resumed round moves nothing and must not forfeit the remaining budget.
      if (( topup_ran && n_sel <= prev_n_sel )); then
        log "  top-up yielded nothing new — the evolve set looks exhausted, moving on"
        break
      fi
    done

    if (( n_sel < 1 )); then
      log "  ERROR: empty corpus — cannot SFT"
      break
    fi
    if (( n_sel < MIN_TRAJS )); then
      log "  WARNING: SFT proceeding with $n_sel trajs (< $MIN_TRAJS) after $topup top-up round(s)"
    fi

    if ! done_p "sft-i${k}-a${attempt}"; then
      sft_env=(
        SFT_DATASET_NAME="$CORPUS_ATTEMPT"
        SFT_OUTPUT_DIR="$ADAPTER_ATTEMPT"
        SFT_EPOCHS="$SFT_EPOCHS"
        SFT_GPUS="$GPU_POOL"
      )
      if [[ -n "$prev_adapter" && -f "$prev_adapter/adapter_config.json" ]]; then
        sft_env+=(INIT_ADAPTER="$prev_adapter")
      elif [[ -n "$prev_adapter" && -f "$prev_adapter/config.json" ]]; then
        sft_env+=(MODEL_OVERRIDE="$prev_adapter")
      fi
      time_stage "$k" "D_sft_a${attempt}" env "${sft_env[@]}" \
        bash "$ROOT/scripts/train_sft.sh"
      mark "sft-i${k}-a${attempt}"
    fi
    [[ -d "$ADAPTER_ATTEMPT" ]] || { echo "ERROR: no adapter at $ADAPTER_ATTEMPT" >&2; exit 2; }

    job_e="${TAG}-E-sft-a${attempt}"
    time_stage "$k" "E_holdout_sft_a${attempt}" \
      run_tb2_eval "$job_e" "$new_harness" "$HOLDOUT_TASKS_JSON" "$ADAPTER_ATTEMPT"
    record_score "$k" "E_holdout_sft_a${attempt}" "sft_i${k}_a${attempt}" "harness_i${k}" "$job_e"
    AFTER_SCORE="$(score_job "$job_e" "$HOLDOUT_TASKS_JSON" | awk '{print $1}')"
    AFTER_ERR="$(count_sys_errors "$job_e" "$HOLDOUT_TASKS_JSON")"

    log "  holdout before(SFT)=${BEFORE_SCORE}/err${BEFORE_ERR} after=${AFTER_SCORE}/err${AFTER_ERR}"

    improved=0
    if ratchet_ok "$AFTER_SCORE" "$AFTER_ERR" "$BEFORE_SCORE" "$BEFORE_ERR"; then
      improved=1
    fi

    if (( improved )); then
      if (( AFTER_SCORE > BEFORE_SCORE )); then
        log "  model IMPROVED ${BEFORE_SCORE} → ${AFTER_SCORE} — adopting $ADAPTER_ATTEMPT"
      else
        log "  model TIED at ${AFTER_SCORE} (sys_err ${AFTER_ERR}) — adopting $ADAPTER_ATTEMPT"
      fi
      if [[ "$ADAPTER_ATTEMPT" != "$ADAPTER_OUT" ]]; then
        rm -rf "$ADAPTER_OUT"
        cp -a "$ADAPTER_ATTEMPT" "$ADAPTER_OUT"
      fi
      printf '%s\t%s\t%s\n' "$k" "$AFTER_SCORE" "$ADAPTER_OUT" >>"$BEST_FILE"
      prev_adapter="$ADAPTER_OUT"
      save_incumbent "$AFTER_SCORE" "$AFTER_ERR" "$new_harness"
      adopted=1
      break
    fi

    log "  model DID NOT improve (${BEFORE_SCORE} → ${AFTER_SCORE})"
    if (( attempt >= MAX_SFT_RETRIES )); then
      log "  max SFT retries reached — keeping incumbent model ${prev_adapter:-base}"
      break
    fi

    attempt=$((attempt + 1))
    log "  collecting more data: +${EXTRA_EVOLVE_ROUNDS} evolve rounds (attempt $attempt)"
    if ! done_p "evolve-extra-i${k}-a${attempt}"; then
      time_stage "$k" "A_extra_evolve_a${attempt}" \
        run_evolve "$TAG" "$EXTRA_EVOLVE_ROUNDS" "$new_harness" "$prev_adapter" 1
      mark "evolve-extra-i${k}-a${attempt}"
    fi
    if [[ "$HARNESS_RATCHET" == "1" ]]; then
      # The retry collects trajectories; an extra-round harness has never been
      # scored on holdout, and swapping it would bypass the harness gate.
      log "  retry keeps harness $new_harness (extra rounds only add trajectories)"
    elif newer="$(resolve_evolved "$TAG")"; then
      new_harness="$newer"
    fi
  done

  if (( ! adopted )); then
    log "  iteration $k: model unchanged (${prev_adapter:-base}); harness advances to $new_harness"
  fi
  if [[ "$HARNESS_RATCHET" == "1" ]]; then
    prev_harness="$BEST_HARNESS"
  else
    prev_harness="$new_harness"
  fi
  log "  next iteration seeds from harness=$prev_harness model=${prev_adapter:-<base>}"
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
