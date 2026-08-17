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
#   bash scripts/train_rl_grpo.sh
#
# Smoke (tiny): see h200_rl_smoke.sbatch / scripts/smoke_rl_grpo.sh
set -euo pipefail
source "$(dirname "$0")/_common.sh"

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

# Optional override: pass an explicit non-holdout envs jsonl instead of taxonomy sample.
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

# Prefer train.jsonl (open-instruct local mixer); fall back to hf_dataset dir.
TRAIN_MIXER="$DATA_ROOT/train.jsonl"
n_tasks="$("$(python_bin)" -c "import json;print(json.load(open('$DATA_ROOT/summary.json'))['n_tasks'])")"
echo "RL train tasks: $n_tasks (mixer=$TRAIN_MIXER)"
if (( n_tasks < 4 )); then
  echo "WARN: very few RL tasks ($n_tasks); GRPO group diversity will be poor." >&2
fi

# Holdout leakage guard
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
        bash "$ROOT/scripts/merge_sft_adapter.sh"
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

export VLLM_ALLOW_INSECURE_SERIALIZATION="${VLLM_ALLOW_INSECURE_SERIALIZATION:-1}"
export VLLM_DISABLE_COMPILE_CACHE="${VLLM_DISABLE_COMPILE_CACHE:-1}"
export VLLM_USE_V1="${VLLM_USE_V1:-1}"
export SWERL_SANDBOX_TIMING_LOGS="${SWERL_SANDBOX_TIMING_LOGS:-1}"
export SWERL_RESET_FAILURE_ZERO_REWARD="${SWERL_RESET_FAILURE_ZERO_REWARD:-1}"
export SWERL_DOCKER_AUTO_REMOVE="${SWERL_DOCKER_AUTO_REMOVE:-1}"
export PYTORCH_ALLOC_CONF="${PYTORCH_ALLOC_CONF:-expandable_segments:True}"
export SWERL_CONTAINER_RUNTIME="${SWERL_CONTAINER_RUNTIME:-docker}"

TOOL_CONFIGS="$(cat <<EOF
{"task_data_dir": "$TASK_DATA_DIR", "test_timeout": 120, "image": "python:3.12-slim"}
EOF
)"

PACK_LENGTH=$(( RL_RESPONSE_LENGTH + 2048 ))

echo "===== RL plan ====="
echo "  mixer       : $TRAIN_MIXER ($n_tasks tasks; holdout excluded)"
echo "  gpus        : $RL_GPUS (learners=$N_LEARNERS vllm=$N_VLLM)"
echo "  episodes    : $RL_EPISODES"
echo "  group       : prompts=$RL_UNIQUE_PROMPTS x samples=$RL_SAMPLES_PER_PROMPT"
echo "  max_steps   : $RL_MAX_STEPS  per_turn=$RL_PER_TURN_MAX_TOKENS  resp=$RL_RESPONSE_LENGTH"
echo "  lr          : $RL_LR  loss=dppo"

RL_PY="${RL_PYTHON:-${SFT_PYTHON:-${PYTHON_BIN:-$(python_bin)}}}"

# Soft dep check — fail early with a clear message.
"$RL_PY" - <<'PY'
import importlib
missing=[]
for m in ("ray","datasets","transformers","torch","vllm"):
    try:
        importlib.import_module(m)
    except Exception:
        missing.append(m)
if missing:
    raise SystemExit(
        "ERROR: RL python missing modules: " + ", ".join(missing) +
        "\n       Install into RL_PYTHON / SFT_PYTHON env (open-instruct needs ray+vllm)."
    )
print("RL deps OK")
PY

cd "$OPEN_INSTRUCT_ROOT"

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
  --truncated_importance_sampling_ratio_cap 0.0 \
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
  --filter_zero_std_samples true \
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
