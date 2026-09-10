#!/usr/bin/env bash
# Continue rep46 from the completed RL1 checkpoint:
#   RL1 model + rep19 H1 -> evolve H2 -> RL2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

export REPLICATE=46
export ALLOW_RESUME=1
export MODEL_RATCHET=0
export N_ITERS=2

exec bash "$ROOT/scripts/tmax/settings/run_9b_rep19_harness_rl.sh"
