#!/usr/bin/env bash
# -E (errtrace) is load-bearing, not decoration: without it the ERR trap is not
# inherited by shell functions, and every stage runs inside `time_stage`. A stage
# that failed therefore exited the script with no banner and left STATUS reading
# RUNNING forever — which is exactly the "it broke and I could not tell" failure
# this script's status reporting exists to prevent.
set -Eeuo pipefail
# Step 3: the routed harness->SFT loop, N iterations, on one machine.
#
#   iteration k:
#     A  evolve      model_{k-1} + harness_{k-1}  -> harness_k
#                    (ROUTED_EVOLVE=1: the meta-agent sees only the routed
#                     harness-actionable slice, never the raw failure list)
#     B  eval        model_{k-1} + harness_k      -> isolates the HARNESS gain
#     C  route+build this iteration's trajectories -> SFT corpus_k
#     D  sft         corpus_k, continuing from model_{k-1} -> model_k
#     E  eval        model_k     + harness_k      -> isolates the SFT gain
#
# Why B and E both exist: with only one eval per iteration, a flat score cannot
# be told apart from "the harness helped and the SFT hurt by the same amount".
# Two evals per iteration make each stage's contribution separately readable.
#
# The three stages are strictly serial — D consumes A's trajectories, and A of
# iteration k+1 consumes D's model. Extra machines therefore cannot shorten one
# chain; run this script with a different REPLICATE on each machine to get
# paired replicates of the whole trajectory instead. Given the measured ±1.14
# task run-to-run sd on the 28-task set, one chain cannot resolve a per-stage
# effect smaller than about 2 tasks; three chains can.
#
# Usage:
#   REPLICATE=1 bash scripts/run_loop_step3.sh            # full 3-iteration loop
#   REPLICATE=1 START_ITER=2 bash scripts/run_loop_step3.sh   # resume at iter 2
#
# Env:
#   REPLICATE=1|2|3       chain index; MUST differ per machine (tags collide otherwise)
#   N_ITERS=3             iterations
#   EVOLVE_ROUNDS=4       evolve rounds inside each iteration
#   GPU_POOL=0,..,7       GPUs
#   TASKS_JSON=...        default tasks_learnable28.json
#   SFT_EPOCHS=3          epochs per iteration (lower than a one-shot 5: the
#                         adapter is continued, not retrained from scratch)
#   OWN_ONLY=1            skip the Tmax top-up entirely

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REPLICATE="${REPLICATE:?set REPLICATE=1|2|3 — it must differ per machine or the run tags collide}"
N_ITERS="${N_ITERS:-3}"
START_ITER="${START_ITER:-1}"
EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-4}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export TASKS_JSON="${TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_learnable28.json}"
export TB2_MAX_STEPS="${TB2_MAX_STEPS:-120}"
export TB2_CONCURRENT="${TB2_CONCURRENT:-2}"
SFT_EPOCHS="${SFT_EPOCHS:-3}"
HOLDOUT_JSON="${HOLDOUT_JSON:-$ROOT/recipe/tb2_evolver/holdout_v2.json}"

# Greedy decoding for every rollout in the loop.
#
# The in-chain noise estimate (R0_anchor(k) re-measuring E(k-1)'s exact
# condition) came out at 1.67 tasks, and the SFT step measured 1.67 tasks — the
# effect being hunted is the same size as the error bar, so nothing is decidable.
# Token sampling is the dominant controllable source: one byte-identical config
# scored 17, 14 and 11 across three rounds of the same run. Greedy decoding may
# shift absolute scores slightly, but every arm is compared under the same
# setting, and collapsing the variance is what makes a hill-climb possible at
# all. Set TB2_TEMPERATURE explicitly to override.
export TB2_TEMPERATURE="${TB2_TEMPERATURE:-0}"

# Routing is the point of this experiment; it is not optional here.
export ROUTED_EVOLVE=1
export ROUTED_MIN_EVIDENCE="${ROUTED_MIN_EVIDENCE:-3}"
export ROUTED_MAX_AFFORDANCE_GAPS="${ROUTED_MAX_AFFORDANCE_GAPS:-5}"

# Disk guards — a 3-iteration loop pulls far more images than a single eval.
export TB2_DELETE_IMAGES="${TB2_DELETE_IMAGES:-1}"
export TB2_PRUNE_EVERY="${TB2_PRUNE_EVERY:-8}"
export TB2_MIN_FREE_GB="${TB2_MIN_FREE_GB:-30}"

# The corpus builder imports pandas/pyarrow (route_capability reads the Tmax and
# taxonomy parquets). `command -v python3` resolves to the base conda interpreter,
# which has neither — so the C stage died with ModuleNotFoundError *after* a full
# evolve had already been spent. Resolve the same way the rest of the repo does.
PY="${PYTHON_BIN:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "${VLLM_VENV:-$HOME/.venv}/bin/python" ]]; then
    PY="${VLLM_VENV:-$HOME/.venv}/bin/python"
  else
    PY="$(command -v python3)"
  fi
fi
export PYTHON_BIN="$PY"   # so the C stage's subshell inherits the same choice
BASE="loop3-rep${REPLICATE}"
STATE="$ROOT/outputs/loop3/rep${REPLICATE}"
mkdir -p "$STATE"
TIMINGS="$STATE/timings.tsv"
SCORES="$STATE/scores.tsv"
[[ -f "$TIMINGS" ]] || printf 'iter\tstage\tseconds\tstarted\n' >"$TIMINGS"
[[ -f "$SCORES" ]]  || printf 'iter\tstage\tmodel\tharness\tpassed\ttotal\tjob\n' >"$SCORES"

BASELINE_HARNESS="$ROOT/configs/baseline_harness.yaml"

log()  { printf '\n\033[1m[loop3 rep%s] %s\033[0m\n' "$REPLICATE" "$*"; }

# Without this, `set -e` kills the run silently: the last thing in the log is
# whatever the dying stage happened to print, which for a vLLM pool killed
# mid-load is an ordinary-looking progress line. The banner makes "it stopped"
# distinguishable from "it is still working" at a glance, and records where.
CURRENT_STAGE="startup"
STATUS_FILE="$STATE/STATUS"
on_err() {
  local rc=$?
  {
    echo "FAILED"
    echo "stage    : $CURRENT_STAGE"
    echo "exit     : $rc"
    echo "at       : $(date '+%F %T')"
    echo "log tail : see this terminal, and logs/<run_tag>/"
  } >"$STATUS_FILE"
  printf '\n\033[1;31m╔════════════════════════════════════════════════════════╗\n'
  printf   '║  loop3 rep%s FAILED in stage: %-24s ║\n' "$REPLICATE" "$CURRENT_STAGE"
  printf   '║  exit code %-3s                                          ║\n' "$rc"
  printf   '╚════════════════════════════════════════════════════════╝\033[0m\n'
  printf 'Rerun the same command to resume — completed stages are skipped.\n'
}
trap on_err ERR

# ── exclusive lock per REPLICATE ─────────────────────────────────────────────
# Two chains with the same REPLICATE share every path: the evolve run dir, the
# per-round trajectory dirs, the .done markers and the score tables. Running them
# concurrently does not merely duplicate work — it interleaves two independent
# evolve runs into one set of round dirs, so a round ends up holding trajectories
# produced by two different harness configs and the gate scores a task set that
# is partly missing. That happened once and silently invalidated a full iteration
# (rounds r2 and r3 had overlapping wall-clock spans, which is impossible for a
# single process). The lock makes the second launch fail immediately instead.
LOCKFILE="$STATE/.lock"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  holder="$(cat "$STATE/STATUS" 2>/dev/null | head -4 | tr '\n' ' ')"
  echo "ERROR: another loop3 chain with REPLICATE=$REPLICATE is already running here." >&2
  echo "       holder: $holder" >&2
  echo "" >&2
  echo "       Do NOT start a second one — they share every output path and would" >&2
  echo "       interleave their evolve rounds into the same directories." >&2
  echo "       Attach to the running one, or kill it first:" >&2
  echo "           pkill -f 'run_loop_step3.sh'" >&2
  exit 2
fi

printf 'RUNNING\nstarted: %s\npid: %s\n' "$(date '+%F %T')" "$$" >"$STATUS_FILE"

# ── preflight: interpreter can import what every stage needs ────────────────
# One second here versus discovering it three hours in, after stage A has burned
# a full evolve. Checked against $PY specifically, not "some python on PATH".
if ! "$PY" -c "import pandas, pyarrow, json, yaml" 2>/dev/null; then
  echo "ERROR: interpreter $PY cannot import pandas/pyarrow/yaml." >&2
  echo "       The corpus stage needs them to read the Tmax + taxonomy parquets." >&2
  echo "       Point PYTHON_BIN at an interpreter that has them, e.g.:" >&2
  echo "           PYTHON_BIN=$HOME/.venv/bin/python REPLICATE=$REPLICATE bash scripts/run_loop_step3.sh" >&2
  exit 2
fi

# ── preflight: orphaned vLLM servers from a previous, killed run ─────────────
# serve_pool.sh backgrounds its replicas, so killing this script (or losing the
# SSH session) leaves 8 servers alive holding the ports AND the GPU memory. The
# next launch then dies on "port 8300 already has an OpenAI endpoint" — but only
# after spending five minutes loading weights first, because the port check runs
# per replica as they come up. Checking here fails in one second instead.
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
    for _ in $(seq 30); do
      sleep 2
      alive=0
      for p in "${stale[@]}"; do
        curl -fsS --max-time 1 "http://127.0.0.1:$p/v1/models" >/dev/null 2>&1 && alive=1
      done
      (( alive )) || break
    done
    (( alive )) && { echo "ERROR: stale servers survived pkill; kill them by hand." >&2; exit 2; }
    log "stale servers cleared"
  else
    echo "ERROR: ports ${stale[*]} already serve an OpenAI endpoint — orphans from a killed run." >&2
    echo "       They hold GPU memory too, so this run would OOM even if the ports were free." >&2
    echo "" >&2
    echo "       Clear them, then rerun:" >&2
    echo "           pkill -f 'vllm.*--port 83' && sleep 8 && nvidia-smi" >&2
    echo "       Or let the script do it:" >&2
    echo "           CLEAN_STALE=1 REPLICATE=$REPLICATE bash scripts/run_loop_step3.sh" >&2
    exit 2
  fi
fi
mark() { touch "$STATE/.done-$1"; }
done_p() { [[ -f "$STATE/.done-$1" ]]; }

time_stage() {  # time_stage <iter> <stage> <cmd...>
  local it="$1" st="$2"; shift 2
  local t0 t1
  CURRENT_STAGE="iter${it}/${st}"
  printf 'RUNNING\nstage: %s\nsince: %s\npid: %s\n' "$CURRENT_STAGE" "$(date '+%F %T')" "$$" >"$STATUS_FILE"
  t0=$(date +%s)
  "$@"
  t1=$(date +%s)
  printf '%s\t%s\t%s\t%s\n' "$it" "$st" "$((t1-t0))" "$(date -d "@$t0" '+%F %T')" >>"$TIMINGS"
  log "stage '$st' (iter $it) took $(( (t1-t0)/60 )) min"
}

# Gated best harness produced by an evolve run tag.
resolve_evolved() {
  local tag="$1"
  local st="$ROOT/recipe/tb2_evolver/runs/$tag/_meta_v2/_meta_scratch/harness_evolve_state.json"
  [[ -f "$st" ]] || { echo "ERROR: no evolve state at $st" >&2; return 1; }
  "$PY" - "$st" <<'PY'
import json, os, sys
s = json.load(open(sys.argv[1]))
cfg = (s.get("best_so_far") or {}).get("config")
if not cfg or not os.path.isfile(cfg):
    # No candidate ever passed the regression gate. Falling back to the stock
    # harness would be wrong (it discards earlier iterations' accepted edits) and
    # so would silently reusing the seed, so say so and let the caller decide.
    sys.exit("NO_GATED_CONFIG")
print(cfg)
PY
}

score_of() {  # score_of <job_name>
  "$PY" - "$ROOT/.benchmarks/tb2/$1" "$TASKS_JSON" <<'PY'
import json, sys
from pathlib import Path
d, tj = Path(sys.argv[1]), Path(sys.argv[2])
total = len(json.loads(tj.read_text()))
passed = 0
if d.is_dir():
    for p in d.iterdir():
        rp = p / "result.json"
        if not (p.is_dir() and rp.is_file()):
            continue
        try:
            rw = (json.loads(rp.read_text()).get("verifier_result") or {}).get("rewards", {}).get("reward")
        except Exception:
            continue
        if isinstance(rw, (int, float)) and rw > 0:
            passed += 1
# Denominator is the task list, never the number of dirs that happen to exist:
# a task whose container never came up leaves no dir, and dividing by dirs turns
# a crashed run into a high pass rate.
print(f"{passed}\t{total}")
PY
}

record_score() {  # record_score <iter> <stage> <model> <harness> <job>
  # Idempotent: run_eval short-circuits on resume but this ran unconditionally,
  # so every rerun appended another row for the same (iter, stage) and the delta
  # analysis would average a measurement against a copy of itself.
  if awk -F'\t' -v i="$1" -v st="$2" 'NR>1 && $1==i && $2==st {found=1} END{exit !found}' "$SCORES"; then
    log "score for iter=$1 stage=$2 already recorded — not re-appending"
    return 0
  fi
  local sc; sc="$(score_of "$5")"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$sc" "$5" >>"$SCORES"
  log "SCORE  iter=$1 stage=$2 model=$3 harness=$4 -> $sc"
}

run_eval() {  # run_eval <iter> <stage> <job> <harness_cfg> <adapter_or_empty>
  local it="$1" st="$2" job="$3" cfg="$4" ad="${5:-}"
  if done_p "$job"; then log "skip eval $job (already done)"; return 0; fi
  ( export RUN_TAG="$job" JOB_NAME="$job" HARNESS_CONFIG="$cfg" USE_EVOLVED_HARNESS=0
    if [[ -n "$ad" ]]; then
      export EVAL_SFT=1 LORA_PATH="$ad" LORA_NAME="qwen35-${MODEL_SIZE}-loop3r${REPLICATE}"
    else
      export EVAL_SFT=0
    fi
    bash "$ROOT/scripts/evaluate.sh" )
  mark "$job"
}

# ── loop ─────────────────────────────────────────────────────────────────────
# Iteration k's inputs. Resuming at k>1 re-derives them from k-1's artifacts
# rather than from a variable that no longer exists in a fresh shell.
prev_harness="$BASELINE_HARNESS"
prev_adapter=""
if (( START_ITER > 1 )); then
  p=$(( START_ITER - 1 ))
  prev_harness="$(resolve_evolved "${BASE}-i${p}")" || {
    echo "ERROR: cannot resume — iteration $p produced no gated harness." >&2; exit 2; }
  cand="$ROOT/outputs/sft/${BASE//-/_}_i${p}"
  [[ -d "$cand" ]] && prev_adapter="$cand"
  log "resuming at iter $START_ITER  harness=$prev_harness  adapter=${prev_adapter:-<base>}"
fi

for (( k=START_ITER; k<=N_ITERS; k++ )); do
  TAG="${BASE}-i${k}"
  ADAPTER_OUT="$ROOT/outputs/sft/${BASE//-/_}_i${k}"
  CORPUS="${BASE//-/_}_i${k}"

  # `${x:+A}${x:-B}` is not an if/else: when x is non-empty BOTH halves expand,
  # so the incoming-model label came out as "sft_i1/abs/path/to/adapter". Name it
  # once, explicitly.
  if [[ -n "$prev_adapter" ]]; then IN_MODEL="sft_i$((k-1))"; else IN_MODEL="base"; fi

  log "════════ iteration $k / $N_ITERS ════════"
  log "  in-harness : $prev_harness"
  log "  in-model   : ${prev_adapter:-<base $MODEL_SIZE>}"

  # ── A. evolve, on the current model, seeded by the current harness ────────
  if ! done_p "evolve-i${k}"; then
    time_stage "$k" "A_evolve" env \
      RUN_TAG="$TAG" JOB_NAME="$TAG" \
      NUM_ROUNDS="$EVOLVE_ROUNDS" \
      SEED_HARNESS="$prev_harness" \
      ${prev_adapter:+LORA_PATH="$prev_adapter"} \
      ${prev_adapter:+LORA_NAME="qwen35-${MODEL_SIZE}-loop3r${REPLICATE}"} \
      bash "$ROOT/scripts/evolve.sh"
    mark "evolve-i${k}"
  else
    log "skip evolve-i${k} (already done)"
  fi

  # Evolve's own R0 is (model_{k-1} + harness_{k-1}) measured on THIS machine in
  # THIS window — the anchor the harness delta is read against. Recording it
  # costs nothing (the rollout already happened) and is the only same-host
  # measurement of the incumbent pair, so without it iteration 1's harness gain
  # would have to be compared against a number from a different machine.
  if ! done_p "anchor-i${k}"; then
    "$PY" - "$ROOT/recipe/tb2_evolver/runs/$TAG/_meta_v2/_meta_scratch/harness_evolve_state.json" \
           "$TASKS_JSON" "$k" "$IN_MODEL" >>"$SCORES" <<'PY' || true
import json, sys
from pathlib import Path
st = Path(sys.argv[1])
if st.is_file():
    h = (json.load(open(st)).get("history") or [])
    r0 = next((e for e in h if e.get("input_round") == 0), None)
    if r0 and isinstance(r0.get("score"), (int, float)):
        total = len(json.loads(Path(sys.argv[2]).read_text()))
        print(f"{sys.argv[3]}\tR0_anchor\t{sys.argv[4]}\tharness_i{int(sys.argv[3])-1}"
              f"\t{round(r0['score']*total)}\t{total}\tevolve-R0")
PY
    mark "anchor-i${k}"
  fi

  if new_harness="$(resolve_evolved "$TAG")"; then
    log "  gated harness_$k = $new_harness"
  else
    # Nothing beat the incumbent this iteration. Carrying the seed forward keeps
    # the chain honest — the SFT stage still runs, and the scores table will show
    # a harness stage that contributed nothing, which is a result, not an error.
    new_harness="$prev_harness"
    log "  WARNING: no candidate passed the gate in iter $k; carrying harness forward unchanged"
  fi

  # ── B. eval: previous model on the NEW harness -> harness contribution ────
  time_stage "$k" "B_eval_harness" \
    run_eval "$k" "B_eval_harness" "${TAG}-B-harnessgain" "$new_harness" "$prev_adapter"
  record_score "$k" "B_eval_harness" "$IN_MODEL" "harness_i${k}" "${TAG}-B-harnessgain"

  # ── C. route this iteration's trajectories, build corpus_k ───────────────
  if ! done_p "corpus-i${k}"; then
    time_stage "$k" "C_route_build" bash -c '
      set -euo pipefail
      ROOT="$1"; TAG="$2"; CORPUS="$3"; TASKS_JSON="$4"; HOLDOUT="$5"; OWN_ONLY="${6:-0}"
      PY="${PYTHON_BIN:-$(command -v python3)}"
      # Consolidated attribution over every round this iteration produced.
      jobs=()
      for d in "$ROOT"/.benchmarks/tb2/"$TAG"-r*-traj; do
        [[ -d "$d" ]] && jobs+=("$(basename "$d")")
      done
      [[ ${#jobs[@]} -gt 0 ]] || { echo "ERROR: no round trajectory dirs for $TAG" >&2; exit 2; }
      echo "routing ${#jobs[@]} round dirs: ${jobs[*]}"
      "$PY" -m recipe.tb2_evolver.fault_router \
        --jobs "${jobs[@]}" --tasks "$TASKS_JSON" \
        --out "$ROOT/recipe/tb2_evolver/fault_routing_$CORPUS"
      ATTR="$ROOT/recipe/tb2_evolver/fault_routing_$CORPUS/attributions.jsonl"

      cd "$ROOT/recipe/tb2_sft"
      # Own-only first, so the Tmax quota can be set from the amount of own data
      # this iteration actually produced. A fixed quota is what let the earlier
      # "mix" arm end up 71% external, where the own signal was a minority of the
      # gradient and the arm scored below the own-only one.
      PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" "$PY" src/build_recovery_sft.py \
        --attributions "$ATTR" --name "${CORPUS}_own" \
        --tasks "$TASKS_JSON" --holdout-tasks "$HOLDOUT"
      if [[ "$OWN_ONLY" == "1" ]]; then
        cp -r "$ROOT/recipe/tb2_sft/data/${CORPUS}_own" "$ROOT/recipe/tb2_sft/data/${CORPUS}"
      else
        # The quota is set in PAIRS, not trajectories, because that is what the
        # gradient sees. Tmax trajectories are longer — measured ~5.8 supervised
        # pairs each against ~3.7 for our recovery slices — so topping up to 2x
        # the trajectory count lands own at only ~36% of pairs. That is the same
        # dilution that left the earlier "mix" arm 71% external and below the
        # own-only arm. Solving n_tmax * 5.8 <= own_pairs for a 50/50 pair split
        # gives the divisor below.
        read -r N_OWN OWN_PAIRS < <("$PY" "$ROOT/recipe/tb2_sft/src/corpus_stat.py" \
          "$ROOT/recipe/tb2_sft/data/${CORPUS}_own/summary.json")
        echo "own this iteration: $N_OWN trajectories / $OWN_PAIRS pairs"
        N_TMAX=$(( OWN_PAIRS * 100 / 580 ))
        (( N_TMAX < 1 )) && N_TMAX=1
        TARGET=$(( N_OWN + N_TMAX ))
        echo "topping up to $TARGET trajectories (+$N_TMAX Tmax; keeps own >=50% of PAIRS)"
        PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" "$PY" src/build_recovery_sft.py \
          --attributions "$ATTR" --name "$CORPUS" \
          --tasks "$TASKS_JSON" --holdout-tasks "$HOLDOUT" \
          --tmax-topup-to "$TARGET"
      fi
    ' _ "$ROOT" "$TAG" "$CORPUS" "$TASKS_JSON" "$HOLDOUT_JSON" "${OWN_ONLY:-0}"
    mark "corpus-i${k}"
  else
    log "skip corpus-i${k} (already done)"
  fi

  # ── D. SFT, continuing from the previous iteration's adapter ─────────────
  if ! done_p "sft-i${k}"; then
    time_stage "$k" "D_sft" env \
      SFT_DATASET_NAME="$CORPUS" \
      SFT_OUTPUT_DIR="$ADAPTER_OUT" \
      SFT_EPOCHS="$SFT_EPOCHS" \
      SFT_GPUS="$GPU_POOL" \
      ${prev_adapter:+INIT_ADAPTER="$prev_adapter"} \
      bash "$ROOT/scripts/train_sft.sh"
    mark "sft-i${k}"
  else
    log "skip sft-i${k} (already done)"
  fi
  [[ -d "$ADAPTER_OUT" ]] || { echo "ERROR: SFT produced no adapter at $ADAPTER_OUT" >&2; exit 2; }

  # ── E. eval: new model on the new harness -> SFT contribution ────────────
  time_stage "$k" "E_eval_sft" \
    run_eval "$k" "E_eval_sft" "${TAG}-E-sftgain" "$new_harness" "$ADAPTER_OUT"
  record_score "$k" "E_eval_sft" "sft_i${k}" "harness_i${k}" "${TAG}-E-sftgain"

  # ── model-side ratchet ──────────────────────────────────────────────────
  # The harness is already monotone by construction: iteration k+1 seeds its R0
  # with harness_k, so harness_k itself competes in the gate and is carried
  # forward whenever nothing beats it. The model had no such guard — a bad SFT
  # round was adopted unconditionally and every later iteration inherited it,
  # which is the one way a co-evolution chain can go backwards and never recover.
  # Same tolerance and the same "compare against the incumbent" rule as the
  # harness gate, so the two sides ratchet on consistent terms.
  E_SCORE="$(awk -F'\t' -v i="$k" '$1==i && $2=="E_eval_sft" {print $5}' "$SCORES" | tail -1)"
  BEST_FILE="$STATE/best_model.tsv"
  [[ -f "$BEST_FILE" ]] || printf 'iter\tscore\tadapter\n' >"$BEST_FILE"
  BEST_SCORE="$(awk -F'\t' 'NR>1 {print $2}' "$BEST_FILE" | sort -n | tail -1)"
  BEST_ADAPTER="$(awk -F'\t' -v b="${BEST_SCORE:--1}" 'NR>1 && $2==b {print $3}' "$BEST_FILE" | tail -1)"
  MODEL_TOL="${MODEL_RATCHET_TOL:-2}"

  if [[ -z "${E_SCORE:-}" ]]; then
    # No E row to read — the eval produced nothing countable. Adopting on a
    # missing measurement is how an unmeasured model silently becomes the base
    # for every later iteration, so hold the incumbent and say why.
    log "model ratchet: no E score recorded for iteration $k — holding the incumbent adapter"
    [[ -n "${BEST_ADAPTER:-}" ]] && prev_adapter="$BEST_ADAPTER"
  elif [[ -z "${BEST_SCORE:-}" || -z "$BEST_ADAPTER" ]]; then
    log "model ratchet: first SFT model (score $E_SCORE) becomes the incumbent"
    printf '%s\t%s\t%s\n' "$k" "$E_SCORE" "$ADAPTER_OUT" >>"$BEST_FILE"
    prev_adapter="$ADAPTER_OUT"
  elif (( E_SCORE >= BEST_SCORE - MODEL_TOL )); then
    log "model ratchet: sft_i${k} scored $E_SCORE vs incumbent $BEST_SCORE (tol $MODEL_TOL) — adopting"
    printf '%s\t%s\t%s\n' "$k" "$E_SCORE" "$ADAPTER_OUT" >>"$BEST_FILE"
    prev_adapter="$ADAPTER_OUT"
  else
    log "model ratchet: sft_i${k} scored $E_SCORE vs incumbent $BEST_SCORE (tol $MODEL_TOL) — REJECTED"
    log "  iteration $((k+1)) continues from the incumbent adapter instead: $BEST_ADAPTER"
    printf '%s\t%s\t%s\t(rejected)\n' "$k" "$E_SCORE" "$ADAPTER_OUT" >>"$BEST_FILE"
    prev_adapter="$BEST_ADAPTER"
  fi

  prev_harness="$new_harness"
done

trap - ERR
printf 'DONE\nfinished: %s\n' "$(date '+%F %T')" >"$STATUS_FILE"
log "════════ loop complete ════════"
echo
column -t "$SCORES"
echo
column -t "$TIMINGS"
echo
echo "harness evidence actually routed each round:"
grep -h "routed evidence:" "$ROOT"/logs/*/evolve.log 2>/dev/null | tail -20 || true
