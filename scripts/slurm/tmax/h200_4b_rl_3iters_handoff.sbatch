#!/usr/bin/env bash
# Mentor handoff: canonical 4B RL matrix recipe, with bounded active sampling.
# Submit from the repo root after loading configs/tmax.env; see the issue draft.
#SBATCH --job-name=tx-4b-rl-3iters
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --exclusive
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=5-00:00:00
#SBATCH --no-requeue
#SBATCH --output=tmax_%x_%j.out
#SBATCH --error=tmax_%x_%j.err

set -Eeuo pipefail
export REPO="${REPO:-${SLURM_SUBMIT_DIR:-$PWD}}"
cd "$REPO"
: "${REPLICATE:?export a fresh numeric REPLICATE before sbatch}"
[[ "$REPLICATE" =~ ^[0-9]+$ ]] || { echo 'ERROR: numeric REPLICATE required' >&2; exit 2; }
export START_ITER="${START_ITER:-1}"
[[ "$START_ITER" =~ ^[123]$ ]] || { echo 'ERROR: START_ITER must be 1, 2, or 3' >&2; exit 2; }
state="$REPO/outputs/tmax_coevolve/rep${REPLICATE}"
if [[ -e "$state" ]]; then
  [[ "${ALLOW_RESUME:-0}" == 1 ]] || {
    echo "ERROR: $state exists; choose a fresh replicate or explicitly resume." >&2; exit 2;
  }
else
  [[ "$START_ITER" == 1 ]] || { echo 'ERROR: a fresh run must start at iteration 1' >&2; exit 2; }
fi

# Load credentials/interpreters in the submitting shell. Prevent the legacy
# repo .env from silently supplying an old model or warm-start checkpoint.
export ENV_FILE=/dev/null
unset MODEL_OVERRIDE BASE_MODEL INIT_LORA_PATH ADAPTER_DIR RL_INIT_MODEL
unset REQUIRE_INIT_MODEL REQUIRE_SEED_HARNESS BOOTSTRAP_EVOLVE_RUN_TAG
unset RL_DATA_ROOT RL_ENVS_JSONL RL_EVOLVE_SELECT_DIR RL_FRONTIER_PRIOR_FILE
unset RL_EXCLUDE_EXTRA_JSON POOL_ENVS_JSONL EVOLVE_EXTRA_EXCLUDE
export MODEL_SIZE=4b MODEL_TAG=qwen35 MODEL_4B=Qwen/Qwen3.5-4B
export TMAX_PIPELINE=rl SFT_ROLLOUT_MODE=none N_ITERS=3 HARNESS_ONLY=0
export INIT_LORA_PATH="" SKIP_FIRST_EVOLVE=0
export SEED_HARNESS="$REPO/configs/baseline_tmax_harness.yaml"
export EVOLVE_TASKS_JSON="$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json"
export EVOLVE_ENVS_JSONL="$REPO/recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl"
export HOLDOUT_TASKS_JSON="$REPO/recipe/tb2_evolver/tasks_tmax_only200.json"
export HOLDOUT_ENVS_JSONL="$REPO/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl"
export GPU_POOL=0,1,2,3,4,5,6,7
export LIMIT=0 SKIP_VLLM=0 TB2_TEMPERATURE=0
export EVOLVE_ROUNDS=2 EXTRA_EVOLVE_ROUNDS=0 MAX_SFT_RETRIES=0
export EVOLVE_EXTRA_ARGS='--fanout 5 --fanout-keep 5 --fanout-concurrent 5 --fanout-mode tournament --fanout-eval-concurrent 5 --regression-tolerance 0.04 --explore-every 0 --skip-final-score'
export EVOLVE_WALL_CLOCK_S=14400 EVOLVE_NOOP_ON_META_FAIL=0
export ROTATE_EVOLVE_TASKS=1 ROTATE_FROM_ITER=2 EVOLVE_SET_SIZE=50 ROTATE_SEED=42
export TMAX_CONCURRENT=8 HOLDOUT_CONCURRENT=8 TMAX_MAX_STEPS=80 TMAX_MAX_TOKENS=4096
export HARNESS_RATCHET=1 MODEL_RATCHET=1 ACCEPT_AGGREGATE_GAINS=1
export ACCEPT_TIES=1 HARNESS_ACCEPT_SCORE_TIES=0
export RL_N_TASKS=100 RL_EPISODES=5120 RL_SEED=42
export RL_N_LEARNERS=6 RL_N_VLLM=2 RL_DEEPSPEED_STAGE=3 RL_SEQUENCE_PARALLEL=1
export RL_RESPONSE_LENGTH=32768 RL_PER_TURN_MAX_TOKENS=4096 RL_MAX_STEPS=80
export RL_UNIQUE_PROMPTS=2 RL_SAMPLES_PER_PROMPT=16 RL_ASYNC_STEPS=2
export RL_LR=1e-6 RL_BETA=0.01 RL_TOOL_PARSER=vllm_qwen3_xml
export RL_PROMPT_SCHEMA=vanillux_instance_v1
export RL_ACTIVE_SAMPLING=1 RL_FILTER_ZERO_STD=1 RL_FILTER_INFRA_ERRORS=1
export RL_MAX_SAMPLED_PROMPT_GROUPS=8 RL_FRONTIER_EXPLORATION=0.2
export RL_FRONTIER_DOMAIN_BALANCED=1 RL_WALL_TIMEOUT=0
export RL_EVOLVE_KEEP_UNANIMOUS=1
export RL_SKIP_IMAGE_CHECK=0 RL_AUTOBUILD_IMAGES=1 INSTALL_RL_DEPS=0

echo "handoff git=$(git rev-parse HEAD) replicate=$REPLICATE iterations=$START_ITER..3"
echo 'RL: 5120 episodes/iteration, 32K response, 2x16 group, sample cap=8; H/M ratchets enabled'
if [[ "${TMAX_CONFIG_ONLY:-0}" != 1 ]]; then
  [[ "${DRY_RUN:-0}" != 1 ]] || { echo 'ERROR: unset DRY_RUN before submitting' >&2; exit 2; }
  : "${PYTHON_BIN:?load configs/tmax.env first}"
  : "${VLLM_VENV:?load configs/tmax.env first}"
  : "${RL_PYTHON:?load configs/tmax.env first}"
  for file in "$PYTHON_BIN" "$VLLM_VENV/bin/python" "$RL_PYTHON"; do
    [[ -x "$file" ]] || { echo "ERROR: interpreter missing: $file" >&2; exit 2; }
  done
  for file in "$EVOLVE_ENVS_JSONL" "$HOLDOUT_ENVS_JSONL" "${TAXONOMY_PARQUET:-$REPO/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"; do
    [[ -s "$file" ]] || { echo "ERROR: data missing: $file" >&2; exit 2; }
  done
fi
exec bash "$REPO/scripts/slurm/tmax/h200_tmax_experiment.sbatch"
