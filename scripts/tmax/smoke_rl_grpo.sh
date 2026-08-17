#!/usr/bin/env bash
# End-to-end smoke for Tmax RL (dataset → optional merge → tiny DPPO).
# Invoked by h200_rl_smoke.sbatch on a GPU node.
set -Eeuo pipefail

_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"
source "$_HX_SCRIPTS/_common.sh"

PY="${RL_PYTHON:-${SFT_PYTHON:-${PYTHON_BIN:-$(python_bin)}}}"
export PYTHON_BIN="$PY"
export SFT_PYTHON="$PY"
export RL_PYTHON="$PY"

SMOKE_NAME="${SMOKE_NAME:-tmax_rl_smoke}"
RL_N_TASKS="${RL_N_TASKS:-8}"
RL_EPISODES="${RL_EPISODES:-16}"
RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-2}"
RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-4}"
RL_MAX_STEPS="${RL_MAX_STEPS:-8}"
RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-1024}"
RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-4096}"
RL_POOL_SIZE="${RL_POOL_SIZE:-8}"
RL_GPUS="${RL_GPUS:-${GPU_POOL:-0}}"
RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-1}"
RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-2}"
INSTALL_RAY="${INSTALL_RAY:-1}"

ADAPTER_DIR="${ADAPTER_DIR:-$ROOT/outputs/sft/tmax_coev_rep1_i1}"
RL_OUTPUT_DIR="${RL_OUTPUT_DIR:-$ROOT/outputs/rl/${SMOKE_NAME}}"
RL_DATASET_NAME="${RL_DATASET_NAME:-${SMOKE_NAME}_data}"

echo "===== RL smoke ====="
echo "  python     : $PY"
echo "  n_tasks    : $RL_N_TASKS"
echo "  episodes   : $RL_EPISODES"
echo "  gpus       : $RL_GPUS"
echo "  adapter    : ${ADAPTER_DIR:-<none>}"
echo "  out        : $RL_OUTPUT_DIR"
nvidia-smi -L || true
docker info >/dev/null 2>&1 || { echo "ERROR: docker not usable" >&2; exit 2; }

# 1) deps
echo "----- deps -----"
if ! "$PY" -c "import ray" 2>/dev/null; then
  if [[ "$INSTALL_RAY" == "1" ]]; then
    echo "Installing ray into $PY …"
    "$PY" -m pip install -q "ray[default]>=2.9"
  else
    echo "ERROR: ray missing; set INSTALL_RAY=1 or use an env that has it" >&2
    exit 2
  fi
fi
"$PY" - <<'PY'
import importlib
for m in ("ray","datasets","transformers","torch","vllm","peft"):
    importlib.import_module(m)
    print("OK", m)
PY

# 2) build tiny RL dataset (taxonomy, exclude holdout, prefer evolve50)
echo "----- build dataset -----"
rm -rf "$ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME"
"$PY" -m recipe.tb2_sft.src.build_tmax_rl_dataset \
  --from-taxonomy \
  --n-tasks "$RL_N_TASKS" \
  --seed 42 \
  --name "$RL_DATASET_NAME" \
  --exclude-tasks "$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json" \
  --prefer-tasks "$ROOT/recipe/tb2_evolver/tasks_tmax_evolve50_list.json"
"$PY" - <<PY
import json
from pathlib import Path
s=json.loads(Path("$ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME/summary.json").read_text())
assert s["n_tasks"] == int("$RL_N_TASKS"), s
assert Path(s["train_jsonl"]).is_file()
assert Path(s["task_data_dir"]).is_dir()
# spot-check one task_data layout
tid=s["task_ids"][0]
td=Path(s["task_data_dir"])/tid
assert (td/"instruction.md").is_file()
assert (td/"tests"/"test.sh").is_file()
print("dataset OK", s["n_tasks"], "domains", s.get("by_domain"))
PY

# 3) init model: merge SFT LoRA if present, else base
echo "----- init model -----"
if [[ -d "$ADAPTER_DIR" && -f "$ADAPTER_DIR/adapter_config.json" ]]; then
  export ADAPTER_DIR
  export RL_INIT_MODEL="${RL_MERGED_DIR:-$ROOT/outputs/rl/merged/${SMOKE_NAME}_merged}"
  if [[ ! -f "$RL_INIT_MODEL/config.json" ]]; then
    BASE_MODEL="${BASE_MODEL:-$MODEL}" OUTPUT_DIR="$RL_INIT_MODEL" \
      bash "$ROOT/scripts/merge_sft_adapter.sh"
  fi
else
  echo "WARN: no SFT adapter at $ADAPTER_DIR — using base $MODEL"
  export RL_INIT_MODEL="$MODEL"
  unset ADAPTER_DIR || true
fi

# 4) tiny DPPO
echo "----- train_rl_grpo (smoke) -----"
rm -rf "$RL_OUTPUT_DIR"
export RL_DATASET_NAME RL_OUTPUT_DIR RL_INIT_MODEL
export RL_N_TASKS RL_EPISODES RL_UNIQUE_PROMPTS RL_SAMPLES_PER_PROMPT
export RL_MAX_STEPS RL_PER_TURN_MAX_TOKENS RL_RESPONSE_LENGTH RL_POOL_SIZE
export RL_GPUS RL_ASYNC_STEPS RL_DEEPSPEED_STAGE
export RL_EXP_NAME="${SMOKE_NAME}"
export RL_SAVE_FREQ=5
export RL_CKPT_FREQ=5
# Force rebuild skipped — dataset already built above
bash "$ROOT/scripts/train_rl_grpo.sh"

echo "===== RL smoke PASSED ====="
echo "  dataset : $ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME"
echo "  ckpt    : $RL_OUTPUT_DIR"
ls -la "$RL_OUTPUT_DIR" | head -20
