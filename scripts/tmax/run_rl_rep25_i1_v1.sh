#!/usr/bin/env bash
# Qwen3.5-4B v1 RL from rep25 iter1 evolve evidence and evolved harness,
# followed by a frozen holdout-102 evaluation.
set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

RUN_ID="${RUN_ID:-${SLURM_JOB_ID:-manual}}"
export MODEL_SIZE=4b
export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
_oi_python="$REPO/tmax/training/open-instruct/.venv/bin/python"
export RL_PYTHON="${RL_PYTHON:-$([[ -x "$_oi_python" ]] && echo "$_oi_python" || echo "$PYTHON_BIN")}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export RL_GPUS="${RL_GPUS:-$GPU_POOL}"
export WANDB_MODE="${WANDB_MODE:-disabled}"
export INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-1}"
export RL_WALL_TIMEOUT="${RL_WALL_TIMEOUT:-0}"

# Use the terminal evolved artifact requested by the experiment, even though
# rep25's conservative paired gate did not promote it to the incumbent.
export RL_HARNESS_CONFIG="$REPO/recipe/tb2_evolver/runs/tmax-coev-rep25-i1/R3/config.yaml"
export RL_EVOLVE_REPLICATE=25
export RL_EVOLVE_RUN_TAG=""
export RL_EVOLVE_MASTERED=""
export RL_EVOLVE_KEEP_UNANIMOUS=0
export RL_DATASET_NAME=tmax_rl_rep25_i1_v1
export RL_N_TASKS=100
export RL_REBUILD_DATASET=1
export RL_PROMPT_SCHEMA=vanillux_instance_v1
export RL_SEED=42
export HOLDOUT_TASKS_JSON="$REPO/recipe/tb2_evolver/tasks_tmax_only200.json"

export RL_EPISODES=512
export RL_UNIQUE_PROMPTS=4
export RL_SAMPLES_PER_PROMPT=8
export RL_ASYNC_STEPS=2
export RL_RESPONSE_LENGTH=16384
export RL_PER_TURN_MAX_TOKENS=4096
export RL_MAX_STEPS=40
export RL_FILTER_ZERO_STD=0
export RL_ACTIVE_SAMPLING=0
export RL_BETA=0.01
export RL_N_LEARNERS=6
export RL_N_VLLM=2
export RL_DEEPSPEED_STAGE=3
export RL_SEQUENCE_PARALLEL=1
export RL_INIT_MODEL=Qwen/Qwen3.5-4B
export RL_EXP_NAME=tmax-rl-rep25-i1-v1
export RL_OUTPUT_DIR="$REPO/outputs/rl/tmax_rl_rep25_i1_v1_${RUN_ID}"
export RUN_TAG="rl-rep25-i1-v1-${RUN_ID}"
export LOG_ROOT="$REPO/logs/$RUN_TAG"
export RL_SAVE_FREQ=4
export RL_CKPT_FREQ=4

export TMAX_MAX_STEPS=40
export TMAX_MAX_TOKENS=4096
export TMAX_CONCURRENT=8
export TB2_TEMPERATURE=0
export RESUME=1

mkdir -p "$RL_OUTPUT_DIR" "$LOG_ROOT" /fsx/home/jixuan.chen/logs
[[ -f "$RL_HARNESS_CONFIG" ]] || { echo "ERROR: missing harness: $RL_HARNESS_CONFIG" >&2; exit 2; }
[[ -x "$RL_PYTHON" ]] || { echo "ERROR: missing RL python: $RL_PYTHON" >&2; exit 2; }

echo "===== rep25-i1 4B RL v1 ====="
echo "init model    : $RL_INIT_MODEL"
echo "harness       : $RL_HARNESS_CONFIG"
echo "evolve source : rep$RL_EVOLVE_REPLICATE"
echo "prompt schema : $RL_PROMPT_SCHEMA"
echo "tasks         : $RL_N_TASKS (holdout excluded)"
echo "episodes      : $RL_EPISODES; group=${RL_UNIQUE_PROMPTS}x${RL_SAMPLES_PER_PROMPT}"
echo "output        : $RL_OUTPUT_DIR"

bash "$REPO/scripts/tmax/train_rl_grpo.sh"

[[ -f "$RL_OUTPUT_DIR/.rl_training_complete" ]] || { echo "ERROR: RL completion marker missing" >&2; exit 3; }
RL_CKPT="$(cat "$RL_OUTPUT_DIR/checkpoint.path")"
python3 "$REPO/scripts/tmax/check_rl_ckpt.py" "$RL_CKPT"

export JOB_NAME="tmax-rl-rep25-i1-v1-holdout-${RUN_ID}"
export TASKS_JSON="$HOLDOUT_TASKS_JSON"
export ENVS_JSONL="$REPO/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl"
export HARNESS_CONFIG="$RL_HARNESS_CONFIG"
export MODEL_OVERRIDE="$RL_CKPT"
export EVAL_SFT=0
unset LORA_PATH LORA_NAME || true
bash "$REPO/scripts/tmax/evaluate_tmax.sh"

SUMMARY="$REPO/.benchmarks/tmax/$JOB_NAME/summary.json"
[[ -f "$SUMMARY" ]] || { echo "ERROR: holdout summary missing: $SUMMARY" >&2; exit 4; }
python3 - "$SUMMARY" "$RL_CKPT" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
print(f"FINAL holdout={s['n_passed']}/{s['n_tasks']} ({s['pass_rate']:.3f}) checkpoint={sys.argv[2]}")
PY
