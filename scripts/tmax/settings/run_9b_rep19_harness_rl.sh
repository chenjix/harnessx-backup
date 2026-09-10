#!/usr/bin/env bash
# Three RL updates starting from the already accepted rep19 iteration-1
# harness, with the untouched Qwen3.5-9B base model:
#   reuse H1 -> RL1 -> evolve H2 -> RL2 -> evolve H3 -> RL3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# The generic sbatch default points at the optional open-instruct checkout's
# private venv.  This workspace uses the shared, fully provisioned venv.
export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export RL_PYTHON="${RL_PYTHON:-$PYTHON_BIN}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"

export SEED_HARNESS="${SEED_HARNESS:-$ROOT/recipe/tb2_evolver/runs/tmax-coev-rep19-i1/R2/config.yaml}"
export SKIP_FIRST_EVOLVE="${SKIP_FIRST_EVOLVE:-1}"
export BOOTSTRAP_EVOLVE_RUN_TAG="${BOOTSTRAP_EVOLVE_RUN_TAG:-tmax-coev-rep19-i1}"

# About six hours per RL stage on the observed 9B throughput.  The 7-hour cap
# is a safety bound, not the target duration.
export RL_EPISODES="${RL_EPISODES:-2304}"
export RL_N_TASKS="${RL_N_TASKS:-100}"
export RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-16384}"
export RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-4096}"
export RL_MAX_STEPS="${RL_MAX_STEPS:-40}"
export RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-4}"
export RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-8}"
export RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-2}"
export RL_N_LEARNERS="${RL_N_LEARNERS:-6}"
export RL_N_VLLM="${RL_N_VLLM:-2}"
export RL_ACTIVE_SAMPLING="${RL_ACTIVE_SAMPLING:-1}"
export RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-1}"
export RL_MAX_SAMPLED_PROMPT_GROUPS="${RL_MAX_SAMPLED_PROMPT_GROUPS:-8}"
export RL_FRONTIER_EXPLORATION="${RL_FRONTIER_EXPLORATION:-0.2}"
export RL_FRONTIER_DOMAIN_BALANCED="${RL_FRONTIER_DOMAIN_BALANCED:-1}"
export RL_WALL_TIMEOUT="${RL_WALL_TIMEOUT:-25200}"

export N_ITERS="${N_ITERS:-3}"
export TMAX_SETTING=9b_rl
exec bash "$ROOT/scripts/tmax/settings/_submit.sh"
