#!/usr/bin/env bash
# Frozen Qwen3.5-9B harness search from the canonical baseline:
#   H0 -> H1 -> H2 -> H3 -> H4
# Four evolve rounds, five tournament candidates per round, evaluated in
# parallel against an eight-endpoint H200 inference pool.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"

export SEED_HARNESS="$ROOT/configs/baseline_tmax_harness.yaml"
export SKIP_FIRST_EVOLVE=0
export N_ITERS=1
export EVOLVE_ROUNDS=4

# Keep all eight GPUs serving the frozen 9B model and allow eight task
# rollouts at once. Candidate generation and all five full candidate evals
# are launched concurrently by tournament mode.
export GPU_POOL=0,1,2,3,4,5,6,7
export TMAX_CONCURRENT=8
export HOLDOUT_CONCURRENT=8
export EVOLVE_EXTRA_ARGS="--fanout 5 --fanout-keep 5 --fanout-concurrent 5 --fanout-mode tournament --fanout-eval-concurrent 5 --regression-tolerance 0.04 --explore-every 0 --skip-final-score"

export TMAX_SETTING=9b_harness
exec bash "$ROOT/scripts/tmax/settings/_submit.sh"
