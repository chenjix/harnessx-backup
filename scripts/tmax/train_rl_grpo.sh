#!/usr/bin/env bash
# Online Tmax RL (DPPO / GRPO-fast) after coevolve SFT.
#
# Train pool: up to RL_N_TASKS (default 100) from taxonomy parquet, always
# excluding holdout-102. Prefer keeping evolve-50 inside the pool.
#
# Usage:
#   RL_DATASET_NAME=tmax_rl_train100 \
#   ADAPTER_DIR=outputs/sft/tmax_coev_rep1_i1 \
#   RL_OUTPUT_DIR=outputs/rl/tmax_coev_rep1_i1 \
#   bash scripts/tmax/train_rl_grpo.sh
#
# Or pass an already-merged full checkpoint:
#   RL_INIT_MODEL=outputs/rl/merged/... RL_OUTPUT_DIR=... RL_DATASET_NAME=... \
#     bash scripts/tmax/train_rl_grpo.sh
set -euo pipefail

_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
source "$_HX_SCRIPTS/_common.sh"

OPEN_INSTRUCT_ROOT="${OPEN_INSTRUCT_ROOT:-$ROOT/tmax/training/open-instruct}"
[[ -d "$OPEN_INSTRUCT_ROOT/open_instruct" ]] || {
  echo "ERROR: open-instruct not found at $OPEN_INSTRUCT_ROOT" >&2
  exit 2
}

RL_DATASET_NAME="${RL_DATASET_NAME:?set RL_DATASET_NAME}"
RL_N_TASKS="${RL_N_TASKS:-100}"
RL_SEED="${RL_SEED:-42}"
HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json}"
PREFER_TASKS_JSON="${PREFER_TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
TAXONOMY_PARQUET="${TAXONOMY_PARQUET:-$ROOT/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"
DATA_ROOT="${RL_DATA_ROOT:-$ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME}"
RL_ENVS_JSONL="${RL_ENVS_JSONL:-}"

if [[ ! -f "$DATA_ROOT/summary.json" || ! -f "$DATA_ROOT/train.jsonl" ]]; then
  echo "Building RL dataset → $DATA_ROOT (n_tasks=$RL_N_TASKS, exclude holdout)"
  build_args=(
    --name "$RL_DATASET_NAME"
    --out-root "$ROOT/recipe/tb2_sft/data"
    --exclude-tasks "$HOLDOUT_TASKS_JSON"
    --n-tasks "$RL_N_TASKS"
    --seed "$RL_SEED"
    --prefer-tasks "$PREFER_TASKS_JSON"
  )
  if [[ -n "$RL_ENVS_JSONL" ]]; then
    build_args+=(--envs-jsonl "$RL_ENVS_JSONL")
  else
    build_args+=(--from-taxonomy --taxonomy-parquet "$TAXONOMY_PARQUET")
  fi
  "$(python_bin)" -m recipe.tb2_sft.src.build_tmax_rl_dataset "${build_args[@]}"
fi
require_file "$DATA_ROOT/summary.json"
require_file "$DATA_ROOT/train.jsonl"
TASK_DATA_DIR="$DATA_ROOT/task_data"
[[ -d "$TASK_DATA_DIR" ]] || { echo "ERROR: missing $TASK_DATA_DIR" >&2; exit 2; }

TRAIN_MIXER="$DATA_ROOT/train.jsonl"
n_tasks="$("$(python_bin)" -c "import json;print(json.load(open('$DATA_ROOT/summary.json'))['n_tasks'])")"
echo "RL train tasks: $n_tasks (mixer=$TRAIN_MIXER)"
if (( n_tasks < 1 )); then
  echo "ERROR: empty RL task set" >&2
  exit 2
fi

"$(python_bin)" - "$DATA_ROOT/summary.json" "$HOLDOUT_TASKS_JSON" <<'PY'
import json, sys
from pathlib import Path
s=json.load(open(sys.argv[1]))
raw=json.loads(Path(sys.argv[2]).read_text())
items=raw if isinstance(raw,list) else raw.get("tasks") or raw.get("task_ids") or []
hold=set()
for x in items:
    hold.add(x if isinstance(x,str) else (x.get("task_id") or x.get("name")))
leak=[t for t in s["task_ids"] if t in hold]
if leak:
    raise SystemExit(f"HOLD OUT LEAK in RL set: {leak[:8]}")
print(f"holdout-leak check OK (0 / {len(hold)} holdout ids)")
PY

# ── Init weights (merged SFT or base) ───────────────────────────────────────
RL_OUTPUT_DIR="${RL_OUTPUT_DIR:?set RL_OUTPUT_DIR}"
RL_INIT_MODEL="${RL_INIT_MODEL:-}"
ADAPTER_DIR="${ADAPTER_DIR:-}"
if [[ -z "$RL_INIT_MODEL" ]]; then
  if [[ -n "$ADAPTER_DIR" && -d "$ADAPTER_DIR" ]]; then
    RL_INIT_MODEL="${RL_MERGED_DIR:-$ROOT/outputs/rl/merged/$(basename "$ADAPTER_DIR")}"
    if [[ ! -f "$RL_INIT_MODEL/config.json" ]]; then
      BASE_MODEL="${BASE_MODEL:-$MODEL}" ADAPTER_DIR="$ADAPTER_DIR" OUTPUT_DIR="$RL_INIT_MODEL" \
        bash "$ROOT/scripts/tmax/merge_sft_adapter.sh"
    fi
  else
    RL_INIT_MODEL="${MODEL_OVERRIDE:-${BASE_MODEL:-$MODEL}}"
  fi
fi
echo "RL init model: $RL_INIT_MODEL"
echo "RL output    : $RL_OUTPUT_DIR"
mkdir -p "$RL_OUTPUT_DIR" "$LOG_ROOT"

# ── Hyperparams ─────────────────────────────────────────────────────────────
RL_GPUS="${RL_GPUS:-${SFT_GPUS:-${GPU_POOL:-0,1,2,3,4,5,6,7}}}"
NPROC="$(awk -F',' '{print NF}' <<<"$RL_GPUS")"
if (( NPROC >= 2 )); then
  N_LEARNERS=$(( NPROC / 2 ))
  N_VLLM=$(( NPROC - N_LEARNERS ))
  SINGLE_GPU_ARGS=()
else
  N_LEARNERS=1
  N_VLLM=1
  SINGLE_GPU_ARGS=(--single_gpu_mode --vllm_sync_backend gloo --vllm_gpu_memory_utilization 0.35 --vllm_enforce_eager)
fi

RL_EPISODES="${RL_EPISODES:-512}"
RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-8}"
RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-4}"
RL_MAX_STEPS="${RL_MAX_STEPS:-40}"
RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-4096}"
RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-16384}"
RL_LR="${RL_LR:-1e-6}"
RL_POOL_SIZE="${RL_POOL_SIZE:-64}"
RL_EXP_NAME="${RL_EXP_NAME:-tmax-coevolve-rl}"
RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-2}"
RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-3}"
# 1 = drop zero-std groups (large runs); 0 for tiny smokes
RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-1}"
RL_SYSTEM_PROMPT_FILE="${RL_SYSTEM_PROMPT_FILE:-$OPEN_INSTRUCT_ROOT/scripts/train/debug/envs/swerl_vanillux_sandbox_system_prompt.txt}"

export VLLM_ALLOW_INSECURE_SERIALIZATION="${VLLM_ALLOW_INSECURE_SERIALIZATION:-1}"
export VLLM_DISABLE_COMPILE_CACHE="${VLLM_DISABLE_COMPILE_CACHE:-1}"
export VLLM_USE_V1="${VLLM_USE_V1:-1}"
export SWERL_SANDBOX_TIMING_LOGS="${SWERL_SANDBOX_TIMING_LOGS:-1}"
export SWERL_RESET_FAILURE_ZERO_REWARD="${SWERL_RESET_FAILURE_ZERO_REWARD:-1}"
export SWERL_DOCKER_AUTO_REMOVE="${SWERL_DOCKER_AUTO_REMOVE:-1}"
export PYTORCH_ALLOC_CONF="${PYTORCH_ALLOC_CONF:-expandable_segments:True}"
export SWERL_CONTAINER_RUNTIME="${SWERL_CONTAINER_RUNTIME:-docker}"
export PYTHONPATH="${OPEN_INSTRUCT_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

# Absolute paths — grpo_fast runs with cwd=open-instruct.
TRAIN_MIXER="$(cd "$(dirname "$TRAIN_MIXER")" && pwd)/$(basename "$TRAIN_MIXER")"
TASK_DATA_DIR="$(cd "$TASK_DATA_DIR" && pwd)"
if [[ -d "$RL_INIT_MODEL" ]]; then
  RL_INIT_MODEL="$(cd "$RL_INIT_MODEL" && pwd)"
fi
RL_OUTPUT_DIR="$(mkdir -p "$RL_OUTPUT_DIR" && cd "$RL_OUTPUT_DIR" && pwd)"

TOOL_CONFIGS="$(cat <<EOF
{"task_data_dir": "$TASK_DATA_DIR", "test_timeout": 120, "image": "python:3.12-slim"}
EOF
)"

PACK_LENGTH=$(( RL_RESPONSE_LENGTH + 2048 ))
FILTER_ARG=(--filter_zero_std_samples true)
[[ "$RL_FILTER_ZERO_STD" == "0" ]] && FILTER_ARG=(--filter_zero_std_samples false)

SYSTEM_PROMPT_ARGS=()
if [[ -n "$RL_SYSTEM_PROMPT_FILE" && -f "$RL_SYSTEM_PROMPT_FILE" ]]; then
  SYSTEM_PROMPT_ARGS=(--system_prompt_override_file "$RL_SYSTEM_PROMPT_FILE")
fi

echo "===== RL plan ====="
echo "  mixer       : $TRAIN_MIXER ($n_tasks tasks; holdout excluded)"
echo "  task_data   : $TASK_DATA_DIR"
echo "  init model  : $RL_INIT_MODEL"
echo "  gpus        : $RL_GPUS (learners=$N_LEARNERS vllm=$N_VLLM)"
echo "  episodes    : $RL_EPISODES"
echo "  group       : prompts=$RL_UNIQUE_PROMPTS x samples=$RL_SAMPLES_PER_PROMPT"
echo "  max_steps   : $RL_MAX_STEPS  per_turn=$RL_PER_TURN_MAX_TOKENS  resp=$RL_RESPONSE_LENGTH"
echo "  filter0std  : $RL_FILTER_ZERO_STD"
echo "  lr          : $RL_LR  loss=dppo"

RL_PY="${RL_PYTHON:-${SFT_PYTHON:-${PYTHON_BIN:-$(python_bin)}}}"

# Optional auto-install of critical missing packages (smoke / first coevolve RL).
if [[ "${INSTALL_RL_DEPS:-0}" == "1" ]]; then
  "$RL_PY" - <<'PY'
import importlib.util as u, subprocess, sys
need=[]
for mod, pip in [("ray","ray[default]>=2.9"),("deepspeed","deepspeed>=0.14"),
                 ("openenv","openenv-core>=0.2.1"),("docker","docker>=7.0")]:
    if u.find_spec(mod) is None:
        need.append(pip)
if need:
    print("Installing RL deps:", need)
    subprocess.check_call([sys.executable,"-m","pip","install","-q",*need])
PY
fi

"$RL_PY" - <<'PY'
import importlib
missing=[]
for m in ("ray","deepspeed","datasets","transformers","torch","vllm"):
    try:
        importlib.import_module(m)
    except Exception:
        missing.append(m)
try:
    import openenv.core  # noqa: F401
except Exception:
    missing.append("openenv.core")
if missing:
    raise SystemExit(
        "ERROR: RL python missing modules: " + ", ".join(missing) +
        "\n       Install into RL_PYTHON (ray, deepspeed, openenv-core, vllm)."
    )
print("RL deps OK")
PY

mkdir -p "$LOG_ROOT"
cd "$OPEN_INSTRUCT_ROOT"

set -o pipefail
CUDA_VISIBLE_DEVICES="$RL_GPUS" \
"$RL_PY" open_instruct/grpo_fast.py \
  --dataset_mixer_list "$TRAIN_MIXER" 1.0 \
  --dataset_mixer_list_splits train \
  --max_prompt_token_length 2048 \
  --per_turn_max_tokens "$RL_PER_TURN_MAX_TOKENS" \
  --response_length "$RL_RESPONSE_LENGTH" \
  --pack_length "$PACK_LENGTH" \
  --per_device_train_batch_size 1 \
  --num_unique_prompts_rollout "$RL_UNIQUE_PROMPTS" \
  --num_samples_per_prompt_rollout "$RL_SAMPLES_PER_PROMPT" \
  --async_steps "$RL_ASYNC_STEPS" \
  --model_name_or_path "$RL_INIT_MODEL" \
  --temperature 1.0 \
  --learning_rate "$RL_LR" \
  --total_episodes "$RL_EPISODES" \
  --lr_scheduler_type constant \
  --deepspeed_stage "$RL_DEEPSPEED_STAGE" \
  --num_epochs 1 \
  --num_learners_per_node "$N_LEARNERS" \
  --vllm_num_engines "$N_VLLM" \
  --vllm_tensor_parallel_size 1 \
  "${SINGLE_GPU_ARGS[@]}" \
  --beta 0.0 \
  --use_vllm_logprobs true \
  --truncate_importance_sampling_ratio_cap 0.0 \
  --seed 42 \
  --gradient_checkpointing \
  --vllm_enable_prefix_caching \
  --push_to_hub false \
  --tools swerl_vanillux_sandbox \
  --tool_configs "$TOOL_CONFIGS" \
  --pool_size "$RL_POOL_SIZE" \
  --max_steps "$RL_MAX_STEPS" \
  --verification_reward 1.0 \
  --tool_parser_type vllm_qwen3_xml \
  "${FILTER_ARG[@]}" \
  "${SYSTEM_PROMPT_ARGS[@]}" \
  --backend_timeout 1200 \
  --advantage_normalization_type centered \
  --loss_fn dppo \
  --dppo_divergence_type tv \
  --dppo_divergence_threshold 0.1 \
  --output_dir "$RL_OUTPUT_DIR" \
  --exp_name "$RL_EXP_NAME" \
  --save_freq "${RL_SAVE_FREQ:-50}" \
  --checkpoint_state_freq "${RL_CKPT_FREQ:-25}" \
  2>&1 | tee "$LOG_ROOT/train_rl_grpo.log"

echo "$RL_OUTPUT_DIR" >"$ROOT/outputs/rl/${RL_DATASET_NAME}.path" 2>/dev/null || true
echo "RL checkpoint: $RL_OUTPUT_DIR"
