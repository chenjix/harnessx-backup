#!/usr/bin/env bash
# Set MIN_IMAGE_GB from images actually missing on this node.
# Source from sbatch after PYTHON_BIN / REPO / SHARED_BASE are set.
#
# The old formula (4G + 0.10G × 152, floor 16 → 20G) treated the store as empty.
# After a coevolve the 152 tmax-eval images ARE the thing filling /var/lib/containerd,
# so 11–19G free is normal and enough to run. Jobs 2895/2898 died on that floor.

if [[ -n "${MIN_IMAGE_GB:-}" ]]; then
  echo "  image budget       : MIN_IMAGE_GB=$MIN_IMAGE_GB (caller override)"
  return 0 2>/dev/null || true
fi

_hx_evolve_envs="${EVOLVE_ENVS_JSONL:-$REPO/recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl}"
_hx_holdout_envs="${HOLDOUT_ENVS_JSONL:-$REPO/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl}"
_hx_budget_args=(--budget)
[[ "${SHARED_BASE:-1}" == "1" ]] || _hx_budget_args+=(--no-shared-base)

_hx_line="$(
  PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" \
    "$REPO/scripts/tmax/count_task_images.py" \
    "${_hx_budget_args[@]}" "$_hx_evolve_envs" "$_hx_holdout_envs" 2>/dev/null || true
)"

if [[ "$_hx_line" =~ ^([0-9]+)[[:space:]]+([0-9]+)[[:space:]]+([0-9]+)$ ]]; then
  MIN_IMAGE_GB="${BASH_REMATCH[3]}"
  echo "  image budget       : ${BASH_REMATCH[1]} missing / ${BASH_REMATCH[2]} cached-or-wanted -> need ${MIN_IMAGE_GB}G"
else
  MIN_IMAGE_GB=6
  echo "  image budget       : inventory failed, using ${MIN_IMAGE_GB}G floor (was 20G and killed warm nodes)"
fi
unset _hx_evolve_envs _hx_holdout_envs _hx_budget_args _hx_line
