#!/usr/bin/env bash
# CPU-only preparation for the RL run. Run this on a login node BEFORE sbatch.
#
#   bash scripts/tmax/prep_rl_smoke.sh
#   RL_DATASET_NAME=tmax_rl_train100 RL_N_TASKS=100 bash scripts/tmax/prep_rl_smoke.sh
#
# Everything here needs CPU, network and docker but no GPU: building the task
# set, building each task's container image, and checking the launcher's flags
# and python deps. Running it as a separate step means the GPU job starts model
# loading right away instead of spending its first 7-10 minutes on data prep.

set -Eeuo pipefail
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"

export MODEL_SIZE="${MODEL_SIZE:-4b}"
RL_DATASET_NAME="${RL_DATASET_NAME:-tmax_rl_smoke_data}"
# grpo_fast needs max(async_steps,1) * num_unique_prompts_rollout distinct prompts
# in the task set. 4 was below that for the default shape and the GPU job died on
# it after the allocation. 16 clears it with room, and costs only prep time.
RL_N_TASKS="${RL_N_TASKS:-8}"
RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-2}"
RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-1}"
RL_SEED="${RL_SEED:-42}"
# Which tasks the builder draws from. Overridable because the right answer
# depends on what the run is for: a TRAINING run wants coverage, while a run
# validating that RL produces a gradient at all wants tasks the model sometimes
# solves — GRPO's advantage is reward minus the group mean, so a task that is
# never solved and one that is always solved contribute equally nothing.
# tasks_rl_known_solvable.json holds the tasks Qwen3.5-9B was measured solving
# under the eval harness in screening job 33602.
RL_EXCLUDE_TASKS="${RL_EXCLUDE_TASKS:-$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json}"
RL_PREFER_TASKS="${RL_PREFER_TASKS:-$ROOT/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
DATA_DIR="$ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME"
PY="${PYTHON_BIN:-${VLLM_VENV:-$HOME/.venv}/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3)"
export RL_PYTHON="${RL_PYTHON:-$ROOT/tmax/training/open-instruct/.venv/bin/python}"

TAXONOMY_PARQUET="${TAXONOMY_PARQUET:-$ROOT/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"
_TAX_FALLBACK=/fsx/home/jixuan.chen/qwen35-tb2-fullstack/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet
if [[ ! -f "$TAXONOMY_PARQUET" && -f "$_TAX_FALLBACK" ]]; then
  TAXONOMY_PARQUET="$_TAX_FALLBACK"
fi
[[ -f "$TAXONOMY_PARQUET" ]] || { echo "ERROR: no taxonomy parquet (docs/DATA.md)" >&2; exit 2; }

echo "===== 1/3 task set: $RL_DATASET_NAME ($RL_N_TASKS tasks) ====="
if [[ -f "$DATA_DIR/train.jsonl" && -f "$DATA_DIR/summary.json" ]]; then
  echo "already built: $DATA_DIR"
else
  PYTHONPATH="$ROOT:$ROOT/recipe/tb2_sft/src" "$PY" -m recipe.tb2_sft.src.build_tmax_rl_dataset \
    --from-taxonomy --taxonomy-parquet "$TAXONOMY_PARQUET" \
    --n-tasks "$RL_N_TASKS" --seed "$RL_SEED" --name "$RL_DATASET_NAME" \
    --exclude-tasks "$RL_EXCLUDE_TASKS" \
    --prefer-tasks "$RL_PREFER_TASKS"
fi

echo
echo "===== 2/3 container images for those tasks ====="
# Login nodes often have little room on the volume holding the image store. When
# that is the case, skip ahead: the trainer builds the missing images on its own
# node and exports the archive, so only the first run pays for it.
if [[ "${SKIP_IMAGES:-0}" == "1" ]]; then
  echo "SKIP_IMAGES=1 — the trainer will build them on the compute node"
elif ! bash "$ROOT/scripts/tmax/docker_preflight.sh" >/dev/null 2>&1; then
  echo "not enough room in this node's image store — skipping."
  echo "the trainer will build them on the compute node and export images.tar"
  bash "$ROOT/scripts/tmax/docker_preflight.sh" 2>&1 | sed -n '1,8p' || true
  SKIP_IMAGES=1
else
  SHARED_BASE="${SHARED_BASE:-1}" JOBS="${IMAGE_JOBS:-6}" \
    ENVS_JSONL="$DATA_DIR/eval_task_set_with_envs.jsonl" \
    bash "$ROOT/scripts/tmax/prebuild_tmax_images.sh"
fi

echo
echo "===== 3/3 launcher + dependency checks ====="
SKIP_GPU_CHECK=1 IMAGES_OPTIONAL="${SKIP_IMAGES:-0}" \
  RL_DATASET_NAME="$RL_DATASET_NAME" bash "$ROOT/scripts/tmax/rl_preflight.sh"

echo
echo "===== export images for the compute node ====="
# Docker images live on the node that built them. This prep runs on a login node,
# so the compute node would otherwise find nothing and have to rebuild all of them
# from the network while holding its GPUs. Export once to shared storage; the
# trainer loads the archive locally in well under a minute.
IMG_TAR="$DATA_DIR/images.tar"
if [[ "${SKIP_IMAGES:-0}" == "1" ]]; then
  echo "skipped (images will be built and exported by the trainer)"
else
mapfile -t IMG_TAGS < <("$PY" - <<PY
import json
rows = [json.loads(l) for l in open("$DATA_DIR/train.jsonl") if l.strip()]
print("\n".join(sorted({r["env_config"]["image"] for r in rows})))
PY
)
if (( ${#IMG_TAGS[@]} == 0 )); then
  echo "ERROR: no image tags in $DATA_DIR/train.jsonl" >&2; exit 2
fi
missing_local=()
for tag in "${IMG_TAGS[@]}"; do
  docker image inspect "$tag" >/dev/null 2>&1 || missing_local+=("$tag")
done
if (( ${#missing_local[@]} )); then
  echo "ERROR: ${#missing_local[@]} image(s) were not built here, e.g. ${missing_local[0]}" >&2
  exit 2
fi
echo "saving ${#IMG_TAGS[@]} image(s) -> $IMG_TAR"
docker save -o "$IMG_TAR" "${IMG_TAGS[@]}"
echo "archive: $(du -h "$IMG_TAR" | cut -f1)"
fi

echo
echo "===== task-set size check ====="
pool_needed=$(( (RL_ASYNC_STEPS < 1 ? 1 : RL_ASYNC_STEPS) * RL_UNIQUE_PROMPTS ))
n_built="$("$PY" -c "import json;print(json.load(open('$DATA_DIR/summary.json'))['n_tasks'])")"
echo "  tasks built : $n_built"
echo "  minimum     : $pool_needed  (async_steps $RL_ASYNC_STEPS x unique_prompts $RL_UNIQUE_PROMPTS)"
if (( n_built < pool_needed )); then
  echo "ERROR: task set too small for that shape — rebuild with RL_N_TASKS >= $pool_needed" >&2
  exit 2
fi

echo
echo "===== strict dependency import (CPU) ====="
"$RL_PYTHON" - <<'PY'
import importlib
mods = ("ray", "deepspeed", "datasets", "transformers", "torch", "vllm", "openenv.core")
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        bad.append(f"{m} ({type(e).__name__})")
if bad:
    raise SystemExit("cannot import: " + ", ".join(bad))
print("all trainer dependencies import cleanly")
PY

echo
echo "prep complete. The GPU job needs nothing else:"
echo "  dataset : $DATA_DIR/train.jsonl"
echo "  images  : ${SKIP_IMAGES:+built by trainer} $([[ -f "$IMG_TAR" ]] && du -h "$IMG_TAR" | cut -f1), locally $("$PY" -c "
import json,subprocess
rows=[json.loads(l) for l in open('$DATA_DIR/train.jsonl')]
want={r['env_config']['image'] for r in rows}
have=set(subprocess.run(['docker','images','--format','{{.Repository}}:{{.Tag}}'],text=True,capture_output=True).stdout.split())
print(f'{len(want & have)}/{len(want)} present')")"
echo
echo "then: sbatch /fsx/home/jixuan.chen/gpu_subm.sbatch"
