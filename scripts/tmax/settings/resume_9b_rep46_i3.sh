#!/usr/bin/env bash
# Clean rerun of iteration 3 from rep46's completed iteration-2 checkpoint:
#   rep46 RL2 model + incumbent harness -> rep50 H3 evolve -> rep50 RL3.
# A separate replicate preserves the earlier RL-only i3 result for comparison.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

export REPLICATE=50
export ALLOW_RESUME=0
export START_ITER=3
export N_ITERS=3
export MODEL_RATCHET=0
export HARNESS_ACCEPT_SCORE_TIES=1
export SKIP_FIRST_EVOLVE=0
export BOOTSTRAP_EVOLVE_RUN_TAG=""

export INIT_LORA_PATH="$ROOT/outputs/rl/tmax_coev_rep46_i2"
export SEED_HARNESS="$ROOT/recipe/tb2_evolver/runs/tmax-coev-rep19-i1/R2/config.yaml"
export REQUIRE_INIT_MODEL="$INIT_LORA_PATH"
export REQUIRE_SEED_HARNESS="$SEED_HARNESS"

# Reuse the fresh 50-task set prepared for iteration 3. The new rep50 evolve
# outcomes, rather than historical REP19 evidence, feed RL frontier selection.
export EVOLVE_TASKS_JSON="$ROOT/recipe/tb2_sft/data/tmax_coev_rep46_i3_evolveset/task_ids.json"
export EVOLVE_ENVS_JSONL="$ROOT/recipe/tb2_sft/data/tmax_coev_rep46_i3_evolveset/eval_task_set_with_envs.jsonl"

exec bash "$ROOT/scripts/tmax/settings/run_9b_rep19_harness_rl.sh"
