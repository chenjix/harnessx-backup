#!/usr/bin/env bash
# Qwen3.5-9B RL from REP19 iter1 evolve evidence, followed by holdout-102.
set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

RUN_ID="${RUN_ID:-${SLURM_JOB_ID:-manual}}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
if [[ -z "${RL_PYTHON:-}" ]]; then
  _oi_python="$REPO/tmax/training/open-instruct/.venv/bin/python"
  if [[ -x "$_oi_python" ]]; then
    export RL_PYTHON="$_oi_python"
  else
    # This cluster's maintained serving venv also carries the complete RL
    # stack. Do not require an optional repo-local uv environment.
    export RL_PYTHON="$PYTHON_BIN"
  fi
fi
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export RL_GPUS="${RL_GPUS:-$GPU_POOL}"
export WANDB_MODE="${WANDB_MODE:-disabled}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"
export RL_WALL_TIMEOUT="${RL_WALL_TIMEOUT:-0}"

export RL_HARNESS_CONFIG="${RL_HARNESS_CONFIG:-$REPO/recipe/tb2_evolver/runs/tmax-coev-rep19-i1/R2/config.yaml}"
export RL_EVOLVE_RUN_TAG=""
export RL_EVOLVE_REPLICATE=""
export PREFER_TASKS_JSON="${PREFER_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
export RL_DATASET_NAME="${RL_DATASET_NAME:-tmax_rl_rep19_i1_frontier100_v2}"
export RL_N_TASKS="${RL_N_TASKS:-100}"
export RL_REBUILD_DATASET="${RL_REBUILD_DATASET:-1}"
export RL_SEED="${RL_SEED:-42}"
export HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_only200.json}"

export RL_EPISODES="${RL_EPISODES:-512}"
export RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-4}"
export RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-4}"
export RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-2}"
export RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-16384}"
export RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-4096}"
export RL_MAX_STEPS="${RL_MAX_STEPS:-40}"
export RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-1}"
export RL_ACTIVE_SAMPLING="${RL_ACTIVE_SAMPLING:-1}"
export RL_MAX_SAMPLED_PROMPT_GROUPS="${RL_MAX_SAMPLED_PROMPT_GROUPS:-4}"
export RL_FILTER_INFRA_ERRORS="${RL_FILTER_INFRA_ERRORS:-1}"
export RL_FRONTIER_PRIOR_FILE="${RL_FRONTIER_PRIOR_FILE:-$REPO/recipe/tb2_sft/data/tmax_rl_rep19_i1_frontier_prior/report.json}"
export RL_FRONTIER_EXPLORATION="${RL_FRONTIER_EXPLORATION:-0.2}"
export RL_FRONTIER_DOMAIN_BALANCED="${RL_FRONTIER_DOMAIN_BALANCED:-1}"
export RL_BETA="${RL_BETA:-0.01}"
export RL_N_LEARNERS="${RL_N_LEARNERS:-6}"
export RL_N_VLLM="${RL_N_VLLM:-2}"
export RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-3}"
export RL_SEQUENCE_PARALLEL="${RL_SEQUENCE_PARALLEL:-1}"
export RL_INIT_MODEL="${RL_INIT_MODEL:-Qwen/Qwen3.5-9B}"
export RL_EXP_NAME="${RL_EXP_NAME:-tmax-rl-rep19-i1}"
export RL_OUTPUT_DIR="${RL_OUTPUT_DIR:-$REPO/outputs/rl/tmax_rl_rep19_i1_${RUN_ID}}"
export RUN_TAG="${RUN_TAG:-rl-rep19-i1-${RUN_ID}}"
export LOG_ROOT="${LOG_ROOT:-$REPO/logs/$RUN_TAG}"
export RL_SAVE_FREQ="${RL_SAVE_FREQ:-16}"
export RL_CKPT_FREQ="${RL_CKPT_FREQ:-16}"

export TMAX_MAX_STEPS=40
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
export TB2_TEMPERATURE="${TB2_TEMPERATURE:-0}"
export RESUME="${RESUME:-1}"

mkdir -p "$RL_OUTPUT_DIR" "$LOG_ROOT" /fsx/home/jixuan.chen/logs
[[ -f "$RL_HARNESS_CONFIG" ]] || { echo "missing harness: $RL_HARNESS_CONFIG" >&2; exit 2; }
[[ -x "$RL_PYTHON" ]] || { echo "missing RL python: $RL_PYTHON" >&2; exit 2; }

if [[ ! -f "$RL_FRONTIER_PRIOR_FILE" ]]; then
  "$PYTHON_BIN" -m recipe.tb2_sft.src.select_rl_tasks_from_evolve \
    --run-tag tmax-coev-rep19-i1 --n-tasks 50 --keep-unanimous \
    --out-dir "$(dirname "$RL_FRONTIER_PRIOR_FILE")"
fi

echo "===== REP19-i1 RL → holdout ====="
echo "harness : $RL_HARNESS_CONFIG (historical holdout 83/102)"
echo "tasks   : fixed REP19 iter1 evolve-50 ($PREFER_TASKS_JSON)"
echo "RL      : episodes=$RL_EPISODES samples/prompt=$RL_SAMPLES_PER_PROMPT response=$RL_RESPONSE_LENGTH steps=$RL_MAX_STEPS"
echo "output  : $RL_OUTPUT_DIR"

bash "$REPO/scripts/tmax/train_rl_grpo.sh"

[[ -f "$RL_OUTPUT_DIR/.rl_training_complete" ]] || { echo "RL training marker missing" >&2; exit 3; }
RL_CKPT="$(cat "$RL_OUTPUT_DIR/checkpoint.path")"
python3 "$REPO/scripts/tmax/check_rl_ckpt.py" "$RL_CKPT"

export JOB_NAME="${HOLDOUT_JOB_NAME:-tmax-rl-rep19-i1-holdout-${RUN_ID}}"
export TASKS_JSON="$HOLDOUT_TASKS_JSON"
export ENVS_JSONL="$REPO/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl"
export HARNESS_CONFIG="$RL_HARNESS_CONFIG"
export MODEL_OVERRIDE="$RL_CKPT"
export EVAL_SFT=0
unset LORA_PATH LORA_NAME || true
bash "$REPO/scripts/tmax/evaluate_tmax.sh"

SUMMARY="$REPO/.benchmarks/tmax/$JOB_NAME/summary.json"
[[ -f "$SUMMARY" ]] || { echo "holdout summary missing: $SUMMARY" >&2; exit 4; }
python3 - "$SUMMARY" "$RL_CKPT" <<'PY'
import json, sys
s=json.load(open(sys.argv[1]))
print(f"FINAL holdout={s['n_passed']}/{s['n_tasks']} ({s['pass_rate']:.3f}) checkpoint={sys.argv[2]}")
PY
