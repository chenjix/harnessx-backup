#!/usr/bin/env bash
set -euo pipefail
# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
source "$_HX_SCRIPTS/_common.sh"

RUN_GLOB="${GRPO_RUN_GLOB:-${RUN_TAG}-r*-traj}"
REPLAY_BUFFER="${REPLAY_BUFFER:-$ROOT/data/grpo/${RUN_TAG}.jsonl}"
success_args=()
[[ "${GRPO_SUCCESS_ONLY:-0}" == "1" ]] && success_args+=(--success-only)

"$(python_bin)" -m recipe.slime.tb2_replay.build_buffer \
  --bench-root "$ROOT/.benchmarks/tb2" \
  --run-glob "$RUN_GLOB" \
  --out "$REPLAY_BUFFER" \
  "${success_args[@]}" \
  2>&1 | tee "$LOG_ROOT/build_grpo_replay.log"

echo "Replay buffer: $REPLAY_BUFFER"
