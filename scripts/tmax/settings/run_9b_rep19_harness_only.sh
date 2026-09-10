#!/usr/bin/env bash
# Frozen-model control for the rep19-H1 Harness+RL chain:
#   Qwen3.5-9B base + H1 -> evolve H2 -> evolve H3 -> evolve H4.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"

# Same starting harness as the clean Harness+RL experiment.
export SEED_HARNESS="${SEED_HARNESS:-$ROOT/recipe/tb2_evolver/runs/tmax-coev-rep19-i1/R2/config.yaml}"

# Match its harness-search protocol and outer-loop count.  Unlike RL-first,
# every control iteration spends its budget on another frozen-model evolve.
export N_ITERS="${N_ITERS:-3}"
export TMAX_SETTING=9b_harness
exec bash "$ROOT/scripts/tmax/settings/_submit.sh"
