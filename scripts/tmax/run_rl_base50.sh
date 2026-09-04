#!/usr/bin/env bash
# Isolated RL sanity check: 50 taxonomy tasks, Qwen3.5-9B base, one DPPO run,
# then eval AFTER on the same 50. No before-eval by default — get a checkpoint
# first. Set SKIP_BEFORE=0 to also score the hub 9B on the same 50.
#
# Not the coevolve loop. Holdout-102 is still excluded from the 50.
#
#   bash scripts/tmax/run_rl_base50.sh
#   sbatch scripts/slurm/tmax/h200_rl_base50.sbatch
set -Eeuo pipefail

_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
export MODEL_SIZE="${MODEL_SIZE:-9b}"
source "$_HX_SCRIPTS/_common.sh"

RUN_ID="${RUN_ID:-${SLURM_JOB_ID:-manual}}"
RL_DATASET_NAME="${RL_DATASET_NAME:-tmax_rl_base50}"
DATA_ROOT="${RL_DATA_ROOT:-$ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME}"
RL_OUTPUT_DIR="${RL_OUTPUT_DIR:-$ROOT/outputs/rl/${RL_DATASET_NAME}_${RUN_ID}}"
COMPARE_DIR="${COMPARE_DIR:-$ROOT/outputs/rl/${RL_DATASET_NAME}_${RUN_ID}_eval}"
HARNESS_CONFIG="${HARNESS_CONFIG:-$ROOT/configs/baseline_tmax_harness.yaml}"
HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json}"
TAXONOMY_PARQUET="${TAXONOMY_PARQUET:-$ROOT/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"

export GPU_POOL="${GPU_POOL:-${RL_GPUS:-0,1,2,3,4,5,6,7}}"
export RL_GPUS="${RL_GPUS:-$GPU_POOL}"
export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export RL_PYTHON="${RL_PYTHON:-$PYTHON_BIN}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export TB2_TEMPERATURE="${TB2_TEMPERATURE:-0}"
export TMAX_MAX_STEPS="${TMAX_MAX_STEPS:-80}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-2}"
export RESUME="${RESUME:-1}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"
export RL_WALL_TIMEOUT="${RL_WALL_TIMEOUT:-0}"
export WANDB_MODE="${WANDB_MODE:-disabled}"

# Proven 8×H200 9B layout (same as coevolve RL).
export RL_N_TASKS="${RL_N_TASKS:-50}"
export RL_SEED="${RL_SEED:-42}"
export RL_EPISODES="${RL_EPISODES:-256}"
export RL_N_LEARNERS="${RL_N_LEARNERS:-6}"
export RL_N_VLLM="${RL_N_VLLM:-2}"
export RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-3}"
export RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-4}"
export RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-8}"
export RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-2}"
export RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-4096}"
export RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-16384}"
export RL_SAVE_FREQ="${RL_SAVE_FREQ:-4}"
export RL_CKPT_FREQ="${RL_CKPT_FREQ:-4}"
export RL_EXP_NAME="${RL_EXP_NAME:-tmax-rl-base50}"
export RL_INIT_MODEL="${RL_INIT_MODEL:-Qwen/Qwen3.5-9B}"
unset ADAPTER_DIR || true

SKIP_BEFORE="${SKIP_BEFORE:-1}"
SKIP_TRAIN="${SKIP_TRAIN:-0}"
SKIP_AFTER="${SKIP_AFTER:-0}"
BEFORE_JOB="${BEFORE_JOB:-tmax-rl-base50-before}"
AFTER_JOB="${AFTER_JOB:-tmax-rl-base50-after-${RUN_ID}}"

PY="$(python_bin)"
mkdir -p "$DATA_ROOT" "$RL_OUTPUT_DIR" "$COMPARE_DIR" "$ROOT/logs" /fsx/home/jixuan.chen/logs

log() { printf '\n\033[1m[rl-base50] %s\033[0m\n' "$*"; }

kill_stale() {
  pkill -f 'vllm.entrypoints.openai.api_server' 2>/dev/null || true
  pkill -f 'open_instruct/grpo_fast.py' 2>/dev/null || true
  local ray_bin
  ray_bin="$(dirname "$RL_PYTHON")/ray"
  [[ -x "$ray_bin" ]] && "$ray_bin" stop --force >/dev/null 2>&1 || true
  sleep 5
}

resolve_rl_ckpt() {
  local root="$1" p cfg
  [[ -d "$root" ]] || return 1
  if [[ -f "$root/config.json" ]]; then
    printf '%s\n' "$root"
    return 0
  fi
  if [[ -f "$root/checkpoint.path" ]]; then
    p="$(cat "$root/checkpoint.path")"
    if [[ -n "$p" && -f "$p/config.json" ]]; then
      printf '%s\n' "$p"
      return 0
    fi
  fi
  cfg="$(find "$root" -maxdepth 2 -name config.json -not -path '*_checkpoints/*' 2>/dev/null | head -1)"
  [[ -n "$cfg" ]] || return 1
  printf '%s\n' "$(dirname "$cfg")"
}

score_job() {
  local summary="$ROOT/.benchmarks/tmax/$1/summary.json"
  [[ -f "$summary" ]] || { echo "missing $summary" >&2; return 1; }
  "$PY" -c "import json;s=json.load(open('$summary'));print(f\"{s['n_passed']}/{s['n_tasks']} ({s['pass_rate']:.3f})\")"
}

# ── 1. dataset: 50 taxonomy tasks, holdout excluded, no evolve-50 prefer ──
if [[ ! -f "$DATA_ROOT/summary.json" || ! -f "$DATA_ROOT/train.jsonl" ]]; then
  log "building $RL_DATASET_NAME (n=$RL_N_TASKS seed=$RL_SEED, holdout excluded, no prefer list)"
  echo '[]' >"$DATA_ROOT/prefer_none.json"
  "$PY" -m recipe.tb2_sft.src.build_tmax_rl_dataset \
    --name "$RL_DATASET_NAME" \
    --out-root "$ROOT/recipe/tb2_sft/data" \
    --from-taxonomy \
    --taxonomy-parquet "$TAXONOMY_PARQUET" \
    --exclude-tasks "$HOLDOUT_TASKS_JSON" \
    --prefer-tasks "$DATA_ROOT/prefer_none.json" \
    --n-tasks "$RL_N_TASKS" \
    --seed "$RL_SEED" \
    --env-name swerl_vanillux_sandbox \
    --image-mode local
else
  log "reusing dataset $DATA_ROOT"
fi
require_file "$DATA_ROOT/summary.json"
require_file "$DATA_ROOT/train.jsonl"
require_file "$DATA_ROOT/eval_task_set_with_envs.jsonl"
require_file "$DATA_ROOT/tasks_list.json"

n_tasks="$("$PY" -c "import json;print(json.load(open('$DATA_ROOT/summary.json'))['n_tasks'])")"
(( n_tasks == RL_N_TASKS )) || log "WARN: dataset has $n_tasks tasks (requested $RL_N_TASKS)"

export TASKS_JSON="$DATA_ROOT/tasks_list.json"
export ENVS_JSONL="$DATA_ROOT/eval_task_set_with_envs.jsonl"

echo "===== rl-base50 plan ====="
echo "  dataset     : $DATA_ROOT  ($n_tasks tasks)"
echo "  init model  : $RL_INIT_MODEL"
echo "  harness     : $HARNESS_CONFIG"
echo "  RL out      : $RL_OUTPUT_DIR"
echo "  episodes    : $RL_EPISODES  learners=$RL_N_LEARNERS vllm=$RL_N_VLLM"
echo "  skip before : $SKIP_BEFORE  (1 = train first, no pre-RL eval)"
echo "  after job   : $AFTER_JOB"
echo "  gpus        : $GPU_POOL"
"$PY" -c "import json;s=json.load(open('$DATA_ROOT/summary.json'));print('  domains     :', s.get('by_domain'))"

if [[ "$CLEAN_STALE" == "1" ]]; then
  log "cleaning stale vLLM / ray"
  kill_stale
fi

# ── 2. docker images for these 50 (node-local) ────────────────────────────
log "prebuilding task images"
SHARED_BASE=1 JOBS="${RL_IMAGE_JOBS:-8}" ENVS_JSONL="$ENVS_JSONL" \
  bash "$ROOT/scripts/tmax/prebuild_tmax_images.sh"

run_eval() {
  local label="$1" job="$2"
  log "eval $label → $job"
  env \
    JOB_NAME="$job" \
    TASKS_JSON="$TASKS_JSON" \
    ENVS_JSONL="$ENVS_JSONL" \
    HARNESS_CONFIG="$HARNESS_CONFIG" \
    GPU_POOL="$GPU_POOL" \
    EVAL_SFT=0 \
    bash "$ROOT/scripts/evaluate_tmax.sh"
}

# ── 3. BEFORE: base 9B on the 50 ──────────────────────────────────────────
if [[ "$SKIP_BEFORE" != "1" ]]; then
  unset MODEL_OVERRIDE LORA_PATH LORA_NAME || true
  export EVAL_SFT=0
  run_eval "BEFORE base $RL_INIT_MODEL" "$BEFORE_JOB"
  kill_stale
else
  log "skip before eval"
fi

# ── 4. RL on the same 50, from hub 9B ─────────────────────────────────────
if [[ "$SKIP_TRAIN" != "1" ]]; then
  log "RL train"
  export RL_DATASET_NAME DATA_ROOT RL_DATA_ROOT="$DATA_ROOT"
  export RL_OUTPUT_DIR RL_INIT_MODEL RL_EXP_NAME
  export RL_N_TASKS RL_SEED RL_EPISODES RL_GPUS GPU_POOL
  export PREFER_TASKS_JSON="$DATA_ROOT/prefer_none.json"
  export HOLDOUT_TASKS_JSON
  bash "$ROOT/scripts/tmax/train_rl_grpo.sh"
  kill_stale
else
  log "skip RL train"
fi

rl_ckpt=""
rl_ckpt="$(resolve_rl_ckpt "$RL_OUTPUT_DIR" || true)"
if [[ -z "$rl_ckpt" && "$SKIP_AFTER" != "1" ]]; then
  echo "ERROR: no RL checkpoint under $RL_OUTPUT_DIR" >&2
  echo "       (step dirs: $(ls -d "$RL_OUTPUT_DIR"/{step_*,*_checkpoints/step_*} 2>/dev/null | tr '\n' ' ' || true))" >&2
  exit 2
fi
[[ -n "$rl_ckpt" ]] && log "RL ckpt → $rl_ckpt"

# ── 5. AFTER: RL weights on the same 50 ───────────────────────────────────
if [[ "$SKIP_AFTER" != "1" ]]; then
  export MODEL_OVERRIDE="$rl_ckpt"
  unset LORA_PATH LORA_NAME || true
  export EVAL_SFT=0
  run_eval "AFTER $rl_ckpt" "$AFTER_JOB"
  kill_stale
else
  log "skip after eval"
fi

# ── 6. compare ────────────────────────────────────────────────────────────
log "compare"
"$PY" - "$ROOT" "$BEFORE_JOB" "$AFTER_JOB" "$COMPARE_DIR" "$rl_ckpt" "$RL_INIT_MODEL" "$DATA_ROOT" <<'PY'
import json, sys
from pathlib import Path
root, before_job, after_job, out_dir, ckpt, init, data = sys.argv[1:]
bench = Path(root) / ".benchmarks" / "tmax"

def load(job):
    p = bench / job / "summary.json"
    if not p.is_file():
        return None
    s = json.loads(p.read_text())
    s["_path"] = str(p)
    return s

b, a = load(before_job), load(after_job)
print(f"{'arm':<10}  passed  rate    summary")
if b:
    print(f"{'BEFORE':<10}  {b['n_passed']:>3}/{b['n_tasks']:<3}  {b['pass_rate']:.3f}   {b['_path']}")
else:
    print(f"{'BEFORE':<10}  missing  ({before_job})")
if a:
    print(f"{'AFTER':<10}  {a['n_passed']:>3}/{a['n_tasks']:<3}  {a['pass_rate']:.3f}   {a['_path']}")
else:
    print(f"{'AFTER':<10}  missing  ({after_job})")
delta = None
if b and a:
    delta = {
        "passed_delta": a["n_passed"] - b["n_passed"],
        "rate_delta": a["pass_rate"] - b["pass_rate"],
    }
    print(f"{'DELTA':<10}  {delta['passed_delta']:+d} tasks  ({delta['rate_delta']:+.3f})")

report = {
    "before_job": before_job,
    "after_job": after_job,
    "init_model": init,
    "rl_ckpt": ckpt or None,
    "dataset": data,
    "before": b,
    "after": a,
    "delta": delta,
}
out = Path(out_dir)
out.mkdir(parents=True, exist_ok=True)
(out / "compare.json").write_text(json.dumps(report, indent=2) + "\n")
print(f"wrote {out / 'compare.json'}")
PY

log "done"
