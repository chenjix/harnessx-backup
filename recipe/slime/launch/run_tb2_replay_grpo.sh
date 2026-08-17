#!/usr/bin/env bash
# TB2 replay GRPO with Slime + HarnessX tool execution.
#
# This is offline TB2 replay training. It uses prompts derived from completed
# Terminal-Bench 2 episodes and a behavioral reward (valid Bash use + final
# response). It is not Harbor-verifier-backed online RL.
#
# Required: HX_ROOT, DATA_ROOT, SLIME_ROOT, MEGATRON_ROOT, PROMPT_DATA,
# MODEL_ARGS_SCRIPT, HF_CHECKPOINT, REF_LOAD, SAVE_CKPT.
set -euo pipefail
[[ "${TRACE_SHELL:-0}" == "1" ]] && set -x

# keep stdout/stderr unbuffered in ray jobs
export PYTHONUNBUFFERED=1
export PYTHONFAULTHANDLER=1
export HARNESSX_SAMPLE_TIMEOUT=360
export KEEP_LAST_CKPTS=10

# default to 8 GPUs if not set by scheduler
NUM_GPUS=${NUM_GPUS:-8}
ACTOR_GPUS=${ACTOR_GPUS:-4}
ROLLOUT_GPUS=${ROLLOUT_GPUS:-4}

# async mode usually runs actor/rollout on separate GPUs
if (( ACTOR_GPUS + ROLLOUT_GPUS > NUM_GPUS )); then
    echo "ACTOR_GPUS + ROLLOUT_GPUS must be <= NUM_GPUS"
    echo "ACTOR_GPUS=${ACTOR_GPUS}, ROLLOUT_GPUS=${ROLLOUT_GPUS}, NUM_GPUS=${NUM_GPUS}"
    exit 1
fi

# Increase Ray heartbeat/health-check timeouts to reduce false node failures under heavy init.
export RAY_health_check_failure_threshold=20
export RAY_health_check_period_ms=5000
export RAY_health_check_timeout_ms=30000
export RAY_num_heartbeats_timeout=60

SLIME_ROOT="${SLIME_ROOT:?SLIME_ROOT env var is required}"
HX_ROOT="${HX_ROOT:?HX_ROOT env var is required}"
MEGATRON_ROOT="${MEGATRON_ROOT:?MEGATRON_ROOT env var is required}"

# ── Unified data / model storage paths ───────────────────────────────────────
DATA_ROOT="${DATA_ROOT:?DATA_ROOT env var is required}"
export HF_HOME="${DATA_ROOT}/hf_cache"
export HF_HUB_ENABLE_HF_TRANSFER=1
export HF_ENDPOINT="https://hf-mirror.com"
CKPT_ROOT="${DATA_ROOT}/harnessx_slime/ckpt"

# NVLink detection (identical to retool)
NVLINK_COUNT=$(nvidia-smi topo -m 2>/dev/null | grep -o 'NV[0-9][0-9]*' | wc -l)
HAS_NVLINK=$([[ "$NVLINK_COUNT" -gt 0 ]] && echo 1 || echo 0)
echo "HAS_NVLINK: $HAS_NVLINK"

PROMPT_DATA="${PROMPT_DATA:?PROMPT_DATA is required}"
MODEL_ARGS_SCRIPT="${MODEL_ARGS_SCRIPT:?MODEL_ARGS_SCRIPT is required}"
HF_CHECKPOINT="${HF_CHECKPOINT:?HF_CHECKPOINT is required}"
REF_LOAD="${REF_LOAD:?REF_LOAD is required (Slime torch_dist checkpoint)}"
SAVE_CKPT="${SAVE_CKPT:?SAVE_CKPT is required}"
[[ -s "$PROMPT_DATA" ]] || { echo "PROMPT_DATA missing/empty: $PROMPT_DATA" >&2; exit 1; }
[[ -f "$MODEL_ARGS_SCRIPT" ]] || { echo "MODEL_ARGS_SCRIPT not found: $MODEL_ARGS_SCRIPT" >&2; exit 1; }
[[ -e "$HF_CHECKPOINT" ]] || { echo "HF_CHECKPOINT not found: $HF_CHECKPOINT" >&2; exit 1; }
[[ -e "$REF_LOAD" ]] || { echo "REF_LOAD not found: $REF_LOAD" >&2; exit 1; }
mkdir -p "$SAVE_CKPT"
RESUME_LOAD="${RESUME_LOAD:-$SAVE_CKPT}"

# ── Persist wandb run id across restarts ─────────────────────────────────────
WANDB_RUN_ID_FILE="${SAVE_CKPT}/wandb_run_id.txt"
if [[ -f "${WANDB_RUN_ID_FILE}" ]]; then
    WANDB_RESUME_ID=$(cat "${WANDB_RUN_ID_FILE}")
    echo "Resuming wandb run: ${WANDB_RESUME_ID}"
else
    WANDB_RESUME_ID=""
fi

# shellcheck disable=SC1090
source "$MODEL_ARGS_SCRIPT"

# ── Args (aligned with retool_qwen3_4b_rl.sh) ──────────────────────────────

CKPT_ARGS=(
    --hf-checkpoint "${HF_CHECKPOINT}"
    --ref-load      "${REF_LOAD}"
    --load          "${RESUME_LOAD}"
    --save          "${SAVE_CKPT}"
    --save-interval 80
    --rotary-base   5000000
)

ROLLOUT_ARGS=(
    --prompt-data            "${PROMPT_DATA}"
    --input-key              prompt
    --label-key              label
    --apply-chat-template
    --rollout-shuffle
    --reward-key             score
    --num-rollout            "${NUM_ROLLOUT:-3000}"
    --rollout-batch-size     "${ROLLOUT_BATCH_SIZE:-32}"
    --n-samples-per-prompt   "${SAMPLES_PER_PROMPT:-8}"
    --rollout-max-response-len "${ROLLOUT_MAX_RESPONSE_LEN:-8192}"
    --rollout-max-context-len  "${ROLLOUT_MAX_CONTEXT_LEN:-16384}"
    --rollout-temperature    "${ROLLOUT_TEMPERATURE:-1}"

    --num-steps-per-rollout  2
    --balance-data
)

# Eval disabled — remove comment and uncomment to re-enable
# EVAL_ARGS=(
#     --eval-interval          20
#     --eval-prompt-data aime  "${HX_ROOT}/data/slime/retool/aime-2024.jsonl"
#     --n-samples-per-eval-prompt 16
#     --eval-max-response-len  16384
#     --eval-max-context-len   32768
#     --eval-top-p             1
#     --eval-reward-key        is_correct
# )
EVAL_ARGS=()

PERF_ARGS=(
    --tensor-model-parallel-size  "${TENSOR_MODEL_PARALLEL_SIZE:-1}"
    --sequence-parallel
    --pipeline-model-parallel-size 1
    --context-parallel-size        1
    --expert-model-parallel-size   1
    --expert-tensor-parallel-size  1

    --recompute-granularity  full
    --recompute-method       uniform
    --recompute-num-layers   1

    --use-dynamic-batch-size
    --max-tokens-per-gpu     16384
    --log-probs-chunk-size   1024
)

GRPO_ARGS=(
    --advantage-estimator    grpo
    --use-kl-loss
    --kl-loss-coef           0.01
    --kl-loss-type           k3
    --entropy-coef           0.00
    --eps-clip               0.2
    --eps-clip-high          0.28
)

OPTIMIZER_ARGS=(
    --optimizer              adam
    --lr                     1e-6
    --lr-decay-style         constant
    --weight-decay           0.1
    --adam-beta1             0.9
    --adam-beta2             0.98
    --optimizer-cpu-offload
    --overlap-cpu-optimizer-d2h-h2d
    --use-precision-aware-optimizer
)

WANDB_ARGS=()
if [[ -n "${WANDB_KEY:-}" && "${WANDB_MODE:-offline}" == "online" ]]; then
    WANDB_ARGS=(
        --use-wandb
        --wandb-project "${WANDB_PROJECT:-qwen35-tb2-replay-grpo}"
        --wandb-group "${WANDB_GROUP:-tb2-replay}"
        --wandb-key "$WANDB_KEY"
        --log-passrate
        --log-reward-category exit_reason
        --disable-wandb-random-suffix
    )
    [[ -n "$WANDB_RESUME_ID" ]] && WANDB_ARGS+=(--wandb-run-id "$WANDB_RESUME_ID")
fi

SGLANG_ARGS=(
    --rollout-num-gpus-per-engine "${ROLLOUT_GPUS_PER_ENGINE:-1}"
    --sglang-mem-fraction-static  "${SGLANG_MEM_FRACTION:-0.6}"
)

MISC_ARGS=(
    # default dropout in megatron is 0.1
    --attention-dropout      0.0
    --hidden-dropout         0.0
    # should be good for model performance
    --accumulate-allreduce-grads-in-fp32
    --attention-softmax-in-fp32
    # need to comment this when using model with MLA
    --attention-backend      flash
)

# ── Key change: Harness.run()-based generate() + reward_func() ───────────────
CUSTOM_ARGS=(
    --custom-generate-function-path recipe.slime.harness_rollout.generate
    --custom-rm-path                recipe.slime.harness_rollout.reward_func
)

# ── Launch Ray ────────────────────────────────────────────────────────────────
export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-"max_split_size_mb:2048,expandable_segments:True"}

export MASTER_ADDR="${MASTER_ADDR:-127.0.0.1}"
export no_proxy="127.0.0.1,${MASTER_ADDR}"

# Collect all local IPs so Ray workers can bypass proxy for intra-node traffic
# (SGLang servers bind to the actual host IP, not 127.0.0.1)
LOCAL_IPS=$(hostname -I 2>/dev/null | tr ' ' ',' | sed 's/,$//')
NO_PROXY_LIST="127.0.0.1,localhost,${MASTER_ADDR}${LOCAL_IPS:+,${LOCAL_IPS}}"
echo "no_proxy for Ray workers: ${NO_PROXY_LIST}"

if ray status >/dev/null 2>&1; then
    if [[ "${REUSE_RAY:-0}" != "1" ]]; then
        echo "Ray is already running. Set REUSE_RAY=1 or stop it explicitly." >&2
        exit 1
    fi
else
    ray start --head \
        --node-ip-address "${MASTER_ADDR}" \
        --num-gpus "${NUM_GPUS}" \
        --disable-usage-stats \
        --dashboard-host=0.0.0.0 \
        --dashboard-port="${RAY_DASHBOARD_PORT:-8265}"
fi

RUNTIME_ENV_JSON="{
  \"env_vars\": {
    \"PYTHONPATH\": \"${MEGATRON_ROOT}:${SLIME_ROOT}:${HX_ROOT}\",
    \"HF_HOME\": \"${DATA_ROOT}/hf_cache\",
    \"HF_HUB_ENABLE_HF_TRANSFER\": \"1\",
    \"HF_ENDPOINT\": \"https://hf-mirror.com\",
    \"CUDA_DEVICE_MAX_CONNECTIONS\": \"1\",
    \"NCCL_NVLS_ENABLE\": \"${HAS_NVLINK}\",
    \"PYTORCH_CUDA_ALLOC_CONF\": \"${PYTORCH_CUDA_ALLOC_CONF}\",
    \"no_proxy\": \"${NO_PROXY_LIST}\",
    \"NO_PROXY\": \"${NO_PROXY_LIST}\",
    \"KEEP_LAST_CKPTS\": \"${KEEP_LAST_CKPTS}\",
    \"HARNESSX_SAMPLE_TIMEOUT\": \"${HARNESSX_SAMPLE_TIMEOUT}\",
    \"HARNESSX_SLIME_TASK_TYPE\": \"tb2_replay\"
  }
}"

ray job submit --address="http://127.0.0.1:${RAY_DASHBOARD_PORT:-8265}" \
    --runtime-env-json="${RUNTIME_ENV_JSON}" \
    -- python3 "${SLIME_ROOT}/train_async.py" \
        --actor-num-nodes         1 \
        --actor-num-gpus-per-node ${ACTOR_GPUS} \
        --rollout-num-gpus        ${ROLLOUT_GPUS} \
        "${MODEL_ARGS[@]}" \
        "${CKPT_ARGS[@]}" \
        "${ROLLOUT_ARGS[@]}" \
        "${OPTIMIZER_ARGS[@]}" \
        "${GRPO_ARGS[@]}" \
        "${WANDB_ARGS[@]}" \
        "${PERF_ARGS[@]}" \
        "${EVAL_ARGS[@]}" \
        "${SGLANG_ARGS[@]}" \
        "${MISC_ARGS[@]}" \
        "${CUSTOM_ARGS[@]}"
